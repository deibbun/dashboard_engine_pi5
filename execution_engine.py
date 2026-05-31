import os
from dotenv import load_dotenv

load_dotenv()

import json
import psycopg2
from psycopg2.extras import RealDictCursor

from strategies.factory import get_strategy  # <-- ADD THIS
PLAYBOOK_REGISTRY = {
    "sniper_v1": SniperPlaybook(tranche_spacing_pct=0.03, max_tranches=3),
    "dip_buyer": DipBuyerPlaybook(tranche_spacing_pct=0.05, max_tranches=4)
}

# ==========================================

class ExecutionEngine:
    def __init__(self, logger, environment="PAPER"):
        self.logger = logger
        self.environment = environment
        self.db_params = {
            'dbname': os.getenv('DB_NAME'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASS'),
            'host': os.getenv('DB_HOST'),
            'port': os.getenv('DB_PORT', '5432')
        }

    def _get_db_connection(self):
        return psycopg2.connect(**self.db_params)

    def _get_current_allocations(self):
        sql = "SELECT allocations FROM treasury_state WHERE environment = %s ORDER BY updated_time DESC LIMIT 1;"
        try:
            conn = self._get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, (self.environment,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row['allocations']:
                return json.loads(row['allocations']) if isinstance(row['allocations'], str) else row['allocations']
            return {}
        except:
            return {}

    def process_entries(self):
        """Evaluates initial entries and tranche scale-ins."""
        allocations = self._get_current_allocations()
        
        try:
            conn = self._get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Map out current active states
            cur.execute("SELECT symbol, strategy_id, current_tranche, average_entry_price FROM positions WHERE status = 'OPEN' AND environment = %s;", (self.environment,))
            open_states = {(row['symbol'], row['strategy_id']): row for row in cur.fetchall()}

            sql = "SELECT m.* FROM live_market_data m JOIN monitored_pairs p ON m.symbol = p.ticker WHERE p.is_active = TRUE;"
            cur.execute(sql)
            opportunities = cur.fetchall()

            for opp in opportunities:
                sym = opp['symbol']
                price = float(opp['price'])
                atr_pct = float(opp['atr_pct']) / 100.0 if opp['atr_pct'] else 0.01
                
                base_asset = sym.split('/')[0].lower()
                strat_id = f"{base_asset}_pure"
                total_allocated = float(allocations.get(strat_id, allocations.get("master", 0.0)))
                
                if total_allocated < 10.0: continue
                
                #playbook = PLAYBOOK_REGISTRY.get("sniper_v1") # Defaulting for now
                strategy_name = opp.get('playbook_name', "sniper_v1")
                playbook = get_strategy(strategy_name)
                state = open_states.get((sym, strat_id))
                
                # --- SCENARIO 1: INITIAL ENTRY (Tranche 1) ---
                if not state:
                    #if playbook.evaluate_initial(opp) == "BUY":
                    if playbook.evaluate(opp) == "BUY":
                        tranche_usd = total_allocated / playbook.max_tranches
                        qty = tranche_usd / price
                        
                        # Set wide targets based on ATR
                        sl = price - (price * (atr_pct * 1.5))
                        tp1 = price + (price * atr_pct)
                        tp2 = price + (price * (atr_pct * 2.0))
                        tp3 = price + (price * (atr_pct * 3.0))
                        
                        insert_sql = """
                            INSERT INTO positions (symbol, strategy_id, environment, status, current_tranche, max_tranches, qty, average_entry_price, entry_price, sl_price, tp1_price, tp2_price, tp3_price, initial_margin_usd, last_updated)
                            VALUES (%s, %s, %s, 'OPEN', 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (symbol, strategy_id, environment)
                            DO UPDATE SET status = 'OPEN', current_tranche = 1, max_tranches = EXCLUDED.max_tranches, qty = EXCLUDED.qty, average_entry_price = EXCLUDED.average_entry_price, entry_price = EXCLUDED.entry_price, sl_price = EXCLUDED.sl_price, tp1_price = EXCLUDED.tp1_price, tp2_price = EXCLUDED.tp2_price, tp3_price = EXCLUDED.tp3_price, initial_margin_usd = EXCLUDED.initial_margin_usd, last_updated = CURRENT_TIMESTAMP;
                        """
                        cur.execute(insert_sql, (sym, strat_id, self.environment, playbook.max_tranches, qty, price, price, sl, tp1, tp2, tp3, tranche_usd))
                        conn.commit()
                        self.logger.success(strat_id, f"TRANCHE 1 FILLED [{sym}] - Avg Price: ${price:.2f}")

                # --- SCENARIO 2: SCALING IN (Tranches 2+) ---
                else:
                    curr_tranche = int(state['current_tranche'])
                    avg_entry = float(state['average_entry_price'])
                    
                    if curr_tranche < playbook.max_tranches:
                        # Check if price dropped enough to trigger the next scale-in
                        target_drop_price = avg_entry * (1.0 - playbook.tranche_spacing_pct)
                        
                        if price <= target_drop_price:
                            tranche_usd = total_allocated / playbook.max_tranches
                            new_qty = tranche_usd / price
                            
                            # The critical math: blending the average price
                            cur.execute("SELECT qty FROM positions WHERE symbol = %s AND strategy_id = %s AND environment = %s", (sym, strat_id, self.environment))
                            old_qty = float(cur.fetchone()['qty'])
                            
                            total_qty = old_qty + new_qty
                            new_avg = ((old_qty * avg_entry) + (new_qty * price)) / total_qty
                            
                            cur.execute("""
                                UPDATE positions 
                                SET current_tranche = current_tranche + 1, qty = %s, average_entry_price = %s, initial_margin_usd = initial_margin_usd + %s, last_updated = CURRENT_TIMESTAMP
                                WHERE symbol = %s AND strategy_id = %s AND environment = %s
                            """, (total_qty, new_avg, tranche_usd, sym, strat_id, self.environment))
                            conn.commit()
                            self.logger.success(strat_id, f"TRANCHE {curr_tranche + 1} FILLED [{sym}] - New Avg Price: ${new_avg:.2f}")

            cur.close()
            conn.close()
        except Exception as e:
            self.logger.error("EXECUTION", f"Entry processing failed: {e}")

    def process_exits(self):
        """Handles Stop Losses, Partial Take Profits (TP1/TP2), and Final Exits (TP3)."""
        sql = """
            SELECT p.*, m.price as current_price
            FROM positions p
            JOIN live_market_data m ON p.symbol = m.symbol
            WHERE p.status = 'OPEN' AND p.environment = %s;
        """
        try:
            conn = self._get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, (self.environment,))
            open_positions = cur.fetchall()

            for pos in open_positions:
                strat = pos['strategy_id']
                sym = pos['symbol']
                current_price = float(pos['current_price'])
                avg_entry = float(pos['average_entry_price'])
                qty = float(pos['qty'])
                
                sl_price = float(pos['sl_price'])
                tp1 = float(pos['tp1_price'])
                tp2 = float(pos['tp2_price'])
                tp3 = float(pos['tp3_price'])
                
                pnl_realized = 0.0
                action_taken = None
                
                # 1. HARD STOP LOSS (Liquidate Everything)
                if current_price <= sl_price:
                    pnl_realized = (current_price - avg_entry) * qty
                    cur.execute("""
                        UPDATE positions SET status = 'WAITING', current_tranche = 0, qty = 0, average_entry_price = 0, sl_price = 0, tp1_price = 0, tp2_price = 0, tp3_price = 0, initial_margin_usd = 0
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (strat, sym, self.environment))
                    action_taken = "STOP LOSS (FULL CLOSE)"
                    
                # 2. TAKE PROFIT 1 HIT (Sell 33%, Move SL to Break Even)
                elif tp1 > 0 and current_price >= tp1:
                    sell_qty = qty * 0.33
                    pnl_realized = (current_price - avg_entry) * sell_qty
                    new_qty = qty - sell_qty
                    cur.execute("""
                        UPDATE positions SET qty = %s, tp1_price = 0, sl_price = %s
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (new_qty, avg_entry, strat, sym, self.environment))
                    action_taken = "TP1 HIT (RISK FREE SECURED)"
                    
                # 3. TAKE PROFIT 2 HIT (Sell another chunk, Trail SL higher)
                elif tp2 > 0 and current_price >= tp2:
                    sell_qty = qty * 0.50 # Sell half of what's left
                    pnl_realized = (current_price - avg_entry) * sell_qty
                    new_qty = qty - sell_qty
                    cur.execute("""
                        UPDATE positions SET qty = %s, tp2_price = 0, sl_price = %s
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (new_qty, tp1, strat, sym, self.environment)) # Move SL to old TP1 level
                    action_taken = "TP2 HIT (PROFIT TRAILED)"
                    
                # 4. TAKE PROFIT 3 HIT (Final Liquidation)
                elif tp3 > 0 and current_price >= tp3:
                    pnl_realized = (current_price - avg_entry) * qty
                    cur.execute("""
                        UPDATE positions SET status = 'WAITING', current_tranche = 0, qty = 0, average_entry_price = 0, sl_price = 0, tp1_price = 0, tp2_price = 0, tp3_price = 0, initial_margin_usd = 0
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (strat, sym, self.environment))
                    action_taken = "TP3 HIT (FULL TARGET REACHED)"
                
                # Apply PnL to Treasury if action was taken
                if action_taken:
                    conn.commit()
                    
                    # Update Treasury Balance
                    cur.execute("SELECT total_capital, reserve, allocations, play_name FROM treasury_state WHERE environment = %s ORDER BY updated_time DESC LIMIT 1;", (self.environment,))
                    t_state = cur.fetchone()
                    if t_state:
                        new_capital = round(float(t_state['total_capital']) + pnl_realized, 2)
                        new_reserve = round(float(t_state['reserve']) + pnl_realized, 2)
                        allocs = t_state['allocations'] if isinstance(t_state['allocations'], str) else json.dumps(t_state['allocations'])
                        
                        cur.execute("""
                            INSERT INTO treasury_state (environment, play_name, total_capital, reserve, allocations)
                            VALUES (%s, %s, %s, %s, %s);
                        """, (self.environment, t_state['play_name'], new_capital, new_reserve, allocs))
                        conn.commit()
                        
                    log_type = "SUCCESS" if pnl_realized > 0 else "WARNING"
                    self.logger._write_log(strat, log_type, f"{action_taken} [{sym}] @ ${current_price:.2f} | PnL: ${pnl_realized:.2f}")

            cur.close()
            conn.close()
        except Exception as e:
            self.logger.error("EXECUTION", f"Exit processing failed: {e}")

    def run_cycle(self):
        self.process_exits()
        self.process_entries()