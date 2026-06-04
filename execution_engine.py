# execution_engine.py

import os
from dotenv import load_dotenv

load_dotenv()

import json
import psycopg2
from psycopg2.extras import RealDictCursor

from strategies.factory import get_strategy, normalize_strategy_id
from accountant import TradeAccountant

KRAKEN_TAKER_FEE = float(os.getenv('KRAKEN_TAKER_FEE', 0.0025))
KRAKEN_MAKER_FEE = float(os.getenv('KRAKEN_MAKER_FEE', 0.0040))

class ExecutionEngine:
    def __init__(self, logger, environment="PAPER"):
        self.logger = logger
        self.environment = environment
        self.accountant = TradeAccountant(environment=self.environment)
        
        self.db_params = {
            'dbname': os.getenv('DB_NAME'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASS'),
            'host': os.getenv('DB_HOST'),
            'port': os.getenv('DB_PORT', '5432')
        }

    def _get_db_connection(self):
        return psycopg2.connect(**self.db_params)
        
    def _get_system_mode(self):
        """Reads the master environment state from the database."""
        try:
            conn = self.get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT value FROM system_config WHERE key = 'trading_mode';")
            row = cur.fetchone()
            cur.close()
            conn.close()
            return row[0] if row else 'PAPER'
        except Exception as e:
            print(f"Failed to fetch system mode, defaulting to PAPER: {e}")
            return 'PAPER'

    def _set_system_mode(self, mode_string):
        """Writes the new environment state to the database."""
        try:
            conn = self.get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE system_config SET value = %s WHERE key = 'trading_mode';", (mode_string,))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"Failed to update system mode: {e}")

    def _get_current_allocations(self):
        sql = "SELECT allocations FROM treasury_state WHERE environment = %s ORDER BY updated_time DESC LIMIT 1;"
        
        conn = None
        cur = None
        
        try:
            conn = self._get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, (self.environment,))
            row = cur.fetchone()
            if row and row['allocations']:
                return json.loads(row['allocations']) if isinstance(row['allocations'], str) else row['allocations']
            return {}
        except:
            return {}
            
        finally:
            # This ensures the connection never hangs open, no matter how the query ends
            if cur:
                cur.close()
            if conn:
                conn.close()

    def process_entries(self):
        """Evaluates initial entries and tranche scale-ins."""
        allocations = self._get_current_allocations()
        
        conn = None
        cur = None
        
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
                target_price = float(opp['price'])
                atr_pct = float(opp['atr_pct']) / 100.0 if opp['atr_pct'] else 0.01
                
                base_asset = sym.split('/')[0].lower()
                strat_id = normalize_strategy_id(sym)
                total_allocated = float(allocations.get(strat_id, allocations.get("master", 0.0)))
                
                if total_allocated < 10.0: continue
                
                strategy_name = opp.get('playbook_name', "sniper_v1")
                playbook = get_strategy(strategy_name)
                state = open_states.get((sym, strat_id))
                
                # --- SCENARIO 1: INITIAL ENTRY (Tranche 1) ---
                if not state:
                    if playbook.evaluate(opp) == "BUY":
                        
                        filled_price = self.accountant.apply_entry_slippage(target_price)
                        
                        tranche_usd = total_allocated / playbook.max_tranches
                        qty = tranche_usd / filled_price
                        
                        # Set wide targets based on ATR
                        brackets = playbook.calculate_brackets(filled_price, atr_pct)
                        sl = brackets["sl_price"]
                        tp1 = brackets["tp1_price"]
                        tp2 = brackets["tp2_price"]
                        tp3 = brackets["tp3_price"]
                        
                        insert_sql = """
                            INSERT INTO positions (symbol, strategy_id, environment, status, current_tranche, max_tranches, qty, average_entry_price, entry_price, sl_price, tp1_price, tp2_price, tp3_price, initial_margin_usd, last_updated)
                            VALUES (%s, %s, %s, 'OPEN', 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (symbol, strategy_id, environment)
                            DO UPDATE SET status = 'OPEN', current_tranche = 1, max_tranches = EXCLUDED.max_tranches, qty = EXCLUDED.qty, average_entry_price = EXCLUDED.average_entry_price, entry_price = EXCLUDED.entry_price, sl_price = EXCLUDED.sl_price, tp1_price = EXCLUDED.tp1_price, tp2_price = EXCLUDED.tp2_price, tp3_price = EXCLUDED.tp3_price, initial_margin_usd = EXCLUDED.initial_margin_usd, last_updated = CURRENT_TIMESTAMP;
                        """
                        cur.execute(insert_sql, (sym, strat_id, self.environment, playbook.max_tranches, qty, filled_price, filled_price, sl, tp1, tp2, tp3, tranche_usd))
                        conn.commit()
                        self.logger.success(strat_id, f"TRANCHE 1 FILLED [{sym}] - Avg Price: ${filled_price:.2f} (Target was ${target_price:.2f})")

                # --- SCENARIO 2: SCALING IN (Tranches 2+) ---
                else:
                    curr_tranche = int(state['current_tranche'])
                    avg_entry = float(state['average_entry_price'])
                    
                    if curr_tranche < playbook.max_tranches:
                        # Check if price dropped enough to trigger the next scale-in
                        target_drop_price = avg_entry * (1.0 - playbook.tranche_spacing_pct)
                        
                        if playbook.evaluate_scale_in(target_price, avg_entry, curr_tranche, atr_pct):
                            
                            filled_price = self.accountant.apply_entry_slippage(target_price)
                            
                            tranche_usd = total_allocated / playbook.max_tranches
                            new_qty = tranche_usd / filled_price
                            
                            # The critical math: blending the average price
                            cur.execute("SELECT qty FROM positions WHERE symbol = %s AND strategy_id = %s AND environment = %s", (sym, strat_id, self.environment))
                            old_qty = float(cur.fetchone()['qty'])
                            
                            total_qty = old_qty + new_qty
                            new_avg = ((old_qty * avg_entry) + (new_qty * filled_price)) / total_qty
                            
                            cur.execute("""
                                UPDATE positions 
                                SET current_tranche = current_tranche + 1, qty = %s, average_entry_price = %s, initial_margin_usd = initial_margin_usd + %s, last_updated = CURRENT_TIMESTAMP
                                WHERE symbol = %s AND strategy_id = %s AND environment = %s
                            """, (total_qty, new_avg, tranche_usd, sym, strat_id, self.environment))
                            conn.commit()
                            self.logger.success(strat_id, f"TRANCHE {curr_tranche + 1} FILLED [{sym}] - New Avg Price: ${new_avg:.2f}")

        except Exception as e:
            self.logger.error("EXECUTION", f"Entry processing failed: {e}")
            
        finally:
            if cur: cur.close()
            if conn: conn.close()

    def process_exits(self):
        """Handles Stop Losses, Partial Take Profits (TP1/TP2), and Final Exits (TP3)."""
        sql = """
            SELECT p.*, m.price as current_price
            FROM positions p
            JOIN live_market_data m ON p.symbol = m.symbol
            WHERE p.status = 'OPEN' AND p.environment = %s;
        """
        
        conn = None
        cur = None
        
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
                margin = float(pos['initial_margin_usd'])
                
                sl_price = float(pos['sl_price'])
                tp1_price = float(pos['tp1_price'])
                tp2_price = float(pos['tp2_price'])
                tp3_price = float(pos['tp3_price'])
                
                pnl_realized = 0.0
                action_taken = None
                
                # 1. HARD STOP LOSS (Liquidate Everything)
                if current_price <= sl_price:
                    financials = self.accountant.calculate_exit(avg_entry, current_price, qty)
                    pnl_realized = financials["net_pnl"]
                    
                    cur.execute("""
                        UPDATE positions 
                        SET status = 'WAITING', current_tranche = 0, qty = 0, average_entry_price = 0, entry_price = 0, 
                            sl_price = 0, tp1_price = 0, tp2_price = 0, tp3_price = 0, initial_margin_usd = 0, last_updated = CURRENT_TIMESTAMP
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (strat, sym, self.environment))
                    action_taken = "STOP LOSS (FULL CLOSE)"
                    
                # 2. TAKE PROFIT 1 HIT (Sell 33%, Move SL to Break Even)
                elif tp1_price > 0 and current_price >= tp1_price:
                    sell_qty = qty * 0.33
                    margin_reduction = margin * 0.33
                    
                    financials = self.accountant.calculate_exit(avg_entry, current_price, sell_qty)
                    pnl_realized = financials["net_pnl"]
                    
                    new_qty = qty - sell_qty
                    new_margin = margin - margin_reduction
                    
                    cur.execute("""
                        UPDATE positions 
                        SET qty = %s, initial_margin_usd = %s, tp1_price = 0, sl_price = %s, last_updated = CURRENT_TIMESTAMP
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (new_qty, new_margin, avg_entry, strat, sym, self.environment))
                    action_taken = "TP1 HIT (RISK FREE SECURED)"
                    
                # 3. TAKE PROFIT 2 HIT (Sell another chunk, Trail SL higher)
                elif tp2_price > 0 and current_price >= tp2_price:
                    sell_qty = qty * 0.50
                    margin_reduction = margin * 0.50
                    
                    financials = self.accountant.calculate_exit(avg_entry, current_price, sell_qty)
                    pnl_realized = financials["net_pnl"]
                    
                    new_qty = qty - sell_qty
                    new_margin = margin - margin_reduction
                    
                    # Trail stop loss to halfway between entry and TP2 to ensure profit lock
                    trailed_sl = avg_entry + ((current_price - avg_entry) * 0.5)
                    
                    cur.execute("""
                        UPDATE positions 
                        SET qty = %s, initial_margin_usd = %s, tp2_price = 0, sl_price = %s, last_updated = CURRENT_TIMESTAMP
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (new_qty, new_margin, trailed_sl, strat, sym, self.environment))
                    action_taken = "TP2 HIT (PROFIT TRAILED)"
                    
                # 4. TAKE PROFIT 3 HIT (Final Liquidation)
                elif tp3_price > 0 and current_price >= tp3_price:
                    financials = self.accountant.calculate_exit(avg_entry, current_price, qty)
                    pnl_realized = financials["net_pnl"]
                    
                    cur.execute("""
                        UPDATE positions 
                        SET status = 'WAITING', current_tranche = 0, qty = 0, average_entry_price = 0, entry_price = 0, 
                            sl_price = 0, tp1_price = 0, tp2_price = 0, tp3_price = 0, initial_margin_usd = 0, last_updated = CURRENT_TIMESTAMP
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (strat, sym, self.environment))
                    action_taken = "TP3 HIT (FULL TARGET REACHED)"
                
                # Apply PnL to Treasury if action was taken
                if action_taken:
                    conn.commit()
                    cur.execute("SELECT total_capital, reserve, allocations, play_name FROM treasury_state WHERE environment = %s ORDER BY updated_time DESC LIMIT 1;", (self.environment,))
                    t_state = cur.fetchone()
                    
                    if t_state:
                        import json
                        new_capital = round(float(t_state['total_capital']) + pnl_realized, 2)
                        new_reserve = round(float(t_state['reserve']) + pnl_realized, 2)
                        allocs = t_state['allocations'] if isinstance(t_state['allocations'], str) else json.dumps(t_state['allocations'])
                        
                        cur.execute("""
                            INSERT INTO treasury_state (environment, play_name, total_capital, reserve, allocations)
                            VALUES (%s, %s, %s, %s, %s);
                        """, (self.environment, t_state['play_name'], new_capital, new_reserve, allocs))
                        conn.commit()
                        
                    log_type = "SUCCESS" if pnl_realized > 0 else "WARNING"
                    self.logger.info(
                        strat, 
                        f"{action_taken} [{sym}] @ ${current_price:.2f} | "
                        f"Gross: ${financials['gross_pnl']:.2f} | Net PnL: ${pnl_realized:.2f}"
                    )

        except Exception as e:
            self.logger.error("EXECUTION", f"Exit processing failed: {e}")
            
        finally:
            if cur: cur.close()
            if conn: conn.close()
            
    def prune_dead_assets(self):
        """Dynamically removes non-core assets that are inactive and not currently in a trade."""
        core_symbols = ('BTC/USD', 'ETH/USD', 'SOL/USD')
        try:
            conn = self._get_db_connection()
            cur = conn.cursor()
            
            # 1. Delete from positions (Only if WAITING, we don't want to delete an active trade!)
            cur.execute("""
                DELETE FROM positions 
                WHERE symbol NOT IN %s 
                  AND status = 'WAITING' 
                  AND symbol NOT IN (SELECT ticker FROM monitored_pairs WHERE is_active = TRUE);
            """, (core_symbols,))
            
            # 2. Delete from live_market_data (Only if no OPEN positions exist for it)
            cur.execute("""
                DELETE FROM live_market_data 
                WHERE symbol NOT IN %s 
                  AND symbol NOT IN (SELECT symbol FROM positions WHERE status = 'OPEN')
                  AND symbol NOT IN (SELECT ticker FROM monitored_pairs WHERE is_active = TRUE);
            """, (core_symbols,))
            
            # 3. Clean up the monitored_pairs table entirely so it doesn't bloat
            cur.execute("""
                DELETE FROM monitored_pairs 
                WHERE ticker NOT IN %s 
                  AND is_active = FALSE
                  AND ticker NOT IN (
                    SELECT symbol FROM positions WHERE status = 'OPEN'
                  );
            """, (core_symbols,))

            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            self.logger.error("EXECUTION", f"Failed to prune dead assets: {e}")

    def run_cycle(self):
        self.process_exits()
        self.process_entries()
        self.prune_dead_assets()