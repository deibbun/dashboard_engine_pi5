# app.py

import os
from dotenv import load_dotenv

load_dotenv()

from kraken_auth import KrakenPrivateClient
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, render_template

#from apscheduler.schedulers.background import BackgroundScheduler
from logger import BotLogger
from market_oracle import KrakenOracle
from treasury_manager import TreasuryManager
from execution_engine import ExecutionEngine

class ExecutiveEngineApp:
    def __init__(self):
        self.app = Flask(__name__)
        self.app.json.compact = False
        self.app.config['JSONIFY_PRETTYPRINT_REGULAR'] = True
        self.db_log = BotLogger()
        
        # --- The CFO Module (LIVE MODE Engaged) ---
        self.LIVE_MODE = False
        
        if self.LIVE_MODE:
            print("🔐 Authenticating with Kraken Private API...")
            self.db_log.environment = "LIVE"
            kraken_client = KrakenPrivateClient()
            wallet = kraken_client.get_live_usd_balance()
            
            if wallet is not None:
                live_usd = wallet["USD"]
                self.treasury = TreasuryManager(self.db_log, initial_capital=live_usd, environment=self.db_log.environment)
                self.treasury.verify_reality(live_usd)
                self.db_log.success("TREASURY", f"LIVE MODE ENGAGED.  Synced Real Capital: ${live_usd}")
            else:
                print("❌ FAILED TO FETCH LIVE BALANCE.  Defaulting to lockdown.")
                self.treasury = TreasuryManager(self.db_log, initial_capital=0.0, environment=self.db_log.environment)
                self.treasury.reconciliation_light = "RED"
        else:
            self.db_log.environment = "PAPER"
            paper_capital = self._get_latest_paper_balance()
            self.treasury = TreasuryManager(self.db_log, initial_capital=paper_capital, environment=self.db_log.environment)
        
        self._setup_routes()

    def get_db_connection(self):
        return psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASS'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', '5432')
        )
        
    def _get_latest_paper_balance(self):
        """Fetches the last known paper balance, or defaults to 10k if empty."""
        try:
            conn = self.get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT total_capital FROM treasury_state WHERE environment = 'PAPER' ORDER BY updated_time DESC LIMIT 1;")
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row[0] is not None:
                return float(row[0])
        except Exception as e:
            print(f"Warning: Failed to fetch historical paper balance. {e}")
        return 10000.00 # Base starting capital if no history exists

    def _setup_routes(self):
        @self.app.route('/')
        def index():
            return render_template('index.html')

        @self.app.route('/api/test_connection')
        def test_connection():
            try:
                conn = self.get_db_connection()
                conn.close()
                return jsonify({"status": "success", "message": f"Successfully connected!"})
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)})
                
        @self.app.route('/api/toggle_mode', methods=['POST'])
        def toggle_mode():
            self.LIVE_MODE = not self.LIVE_MODE
            
            if self.LIVE_MODE:
                print("🔐 Switching to LIVE MODE...")
                self.db_log.environment = "LIVE"
                kraken_client = KrakenPrivateClient()
                wallet = kraken_client.get_live_usd_balance()
                
                if wallet is not None:
                    live_usd = wallet["USD"]
                    self.treasury = TreasuryManager(self.db_log, initial_capital=live_usd, environment=self.db_log.environment)
                    self.treasury.verify_reality(live_usd)
                    self.db_log.success("TREASURY", f"LIVE MODE ENGAGED.  Synced Real Capital:  ${live_usd}")
                else:
                    self.db_log.error("TREASURY", "FAILED TO FETCH LIVE BALANCE.  Defaulting to lockdown.")
                    self.treasury = TreasuryManager(self.db_log, initial_capital=0.0, environment=self.db_log.environment)
                    self.treasury.reconciliation_light = "RED"
            else:
                print("📝 Switching to PAPER TRADING...")
                self.db_log.environment = "PAPER"
                paper_capital = self._get_latest_paper_balance()
                self.treasury = TreasuryManager(self.db_log, initial_capital=paper_capital, environment=self.db_log.environment)
                self.db_log.info("TREASURY", f"PAPER TRADING ENGAGED.  Synced Paper Capital:  ${paper_capital}")
                
            return jsonify({"status": "success", "live_mode": self.LIVE_MODE})
            
        @self.app.route('/api/override/close', methods=['POST'])
        def override_close():
            from flask import request
            data = request.json
            strat = data.get('strategy_id')
            sym = data.get('symbol')
            env_str = "LIVE" if self.LIVE_MODE else "PAPER"
            
            try:
                conn = self.get_db_connection()
                cur = conn.cursor(cursor_factory=RealDictCursor)
                
                # 1. Grab the current live price and the original entry price
                cur.execute("""
                    SELECT p.qty, p.entry_price, m.price as current_price
                    FROM positions p
                    JOIN live_market_data m ON p.symbol = m.symbol
                    WHERE p.strategy_id = %s AND p.symbol = %s AND p.environment = %s AND p.status = 'OPEN';
                """, (strat, sym, env_str))
                
                pos = cur.fetchone()
                if pos:
                    entry_price = float(pos['entry_price'])
                    current_price = float(pos['current_price'])
                    qty = float(pos['qty'])
                    
                    # 2. Calculate the exact math of the early exit
                    pnl = (current_price - entry_price) * qty
                    
                    # 3. Liquidate the position
                    cur.execute("""
                        UPDATE positions 
                        SET status = 'WAITING', qty = 0, entry_price = 0, sl_price = 0, tp1_price = 0, tp2_price = 0, tp3_price = 0, initial_margin_usd = 0, last_updated = CURRENT_TIMESTAMP
                        WHERE strategy_id = %s AND symbol = %s AND environment = %s;
                    """, (strat, sym, env_str))
                    
                    # --- Apply PnL to Treasury on override ---
                    cur.execute("SELECT total_capital, reserve, allocations, play_name FROM treasury_state WHERE environment = %s ORDER BY updated_time DESC LIMIT 1;", (env_str,))
                    t_state = cur.fetchone()
                    if t_state:
                        import json
                        new_capital = round(float(t_state['total_capital']) + pnl, 2)
                        new_reserve = round(float(t_state['reserve']) + pnl, 2)
                        allocs = t_state['allocations'] if isinstance(t_state['allocations'], str) else json.dumps(t_state['allocations'])
                        
                        cur.execute("""
                            INSERT INTO treasury_state (environment, play_name, total_capital, reserve, allocations)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (env_str, t_state['play_name'], new_capital, new_reserve, allocs))
                        
                    
                    conn.commit()
                    
                    # 4. Log the override to the dashboard
                    log_level = "SUCCESS" if pnl > 0 else "WARNING"
                    self.db_log._write_log("EXECUTIVE", log_level, f"MANUAL OVERRIDE [{sym}] - Close: ${current_price:.2f} | PnL: ${pnl:.2f}")
                    
                cur.close()
                conn.close()
                return jsonify({"status": "success", "message": "Position liquidated."})
            except Exception as e:
                import traceback
                traceback.print_exc()
                return jsonify({"status": "error", "message": str(e)})
                
        @self.app.route('/api/override/open', methods=['POST'])
        def override_open():
            from flask import request
            import json
            data = request.json
            sym = data.get('symbol')
            env_str = "LIVE" if self.LIVE_MODE else "PAPER"
            
            base_asset = sym.split('/')[0].lower()
            strat_id = f"{base_asset}_pure"

            try:
                conn = self.get_db_connection()
                cur = conn.cursor(cursor_factory=RealDictCursor)
                
                # 1. Grab current live price and ATR
                cur.execute("SELECT price, atr_pct FROM live_market_data WHERE symbol = %s;", (sym,))
                m_data = cur.fetchone()
                if not m_data:
                    return jsonify({"status": "error", "message": "No market data available."})
                
                price = float(m_data['price'])
                atr_pct = float(m_data['atr_pct']) / 100.0 if m_data['atr_pct'] else 0.01

                # 2. Ask Treasury for permitted capital
                cur.execute("SELECT allocations FROM treasury_state WHERE environment = %s ORDER BY updated_time DESC LIMIT 1;", (env_str,))
                t_row = cur.fetchone()
                if not t_row:
                    return jsonify({"status": "error", "message": "No treasury state found."})
                    
                allocations = json.loads(t_row['allocations']) if isinstance(t_row['allocations'], str) else t_row['allocations']
                total_allocated = float(allocations.get(strat_id, allocations.get("master", 0.0)))

                if total_allocated < 10:
                    return jsonify({"status": "error", "message": f"Not enough capital allocated to {strat_id}."})

                # 3. Math for Tranche 1
                max_tranches = 3
                tranche_usd = total_allocated / max_tranches
                qty = tranche_usd / price
                sl = price - (price * (atr_pct * 1.5))
                tp1 = price + (price * atr_pct)
                tp2 = price + (price * (atr_pct * 2.0))
                tp3 = price + (price * (atr_pct * 3.0))

                # 4. Insert Tranche 1 State
                cur.execute("""
                    INSERT INTO positions (symbol, strategy_id, environment, status, current_tranche, max_tranches, qty, average_entry_price, entry_price, sl_price, tp1_price, tp2_price, tp3_price, initial_margin_usd, last_updated)
                    VALUES (%s, %s, %s, 'OPEN', 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (symbol, strategy_id, environment)
                    DO UPDATE SET status = 'OPEN', current_tranche = 1, max_tranches = EXCLUDED.max_tranches, qty = EXCLUDED.qty, average_entry_price = EXCLUDED.average_entry_price, entry_price = EXCLUDED.entry_price, sl_price = EXCLUDED.sl_price, tp1_price = EXCLUDED.tp1_price, tp2_price = EXCLUDED.tp2_price, tp3_price = EXCLUDED.tp3_price, initial_margin_usd = EXCLUDED.initial_margin_usd, last_updated = CURRENT_TIMESTAMP;
                """, (sym, strat_id, env_str, max_tranches, qty, price, price, sl, tp1, tp2, tp3, tranche_usd))
                
                conn.commit()
                self.db_log._write_log("EXECUTIVE", "SUCCESS", f"MANUAL TRANCHE 1 TRIGGERED [{sym}] @ ${price:.2f}")

                cur.close()
                conn.close()
                return jsonify({"status": "success", "message": f"Force entered {sym}"})
            except Exception as e:
                import traceback
                traceback.print_exc()
                return jsonify({"status": "error", "message": str(e)})
                
        @self.app.route('/api/change_play', methods=['POST'])
        def change_play():
            from flask import request
            data = request.json
            new_play = data.get('play_name')
            
            if not new_play:
                return jsonify({"status": "error", "message": "No play_name provided."})
                
            success = self.treasury.execute_playbook(new_play)
            
            if success:
                self.db_log.info("EXECUTIVE", f"Treasury Shift: Playbook changed to {new_play.upper()}")
                return jsonify({"status": "success", "message": f"Strategy shifted to {new_play}"})
            else:
                return jsonify({"status": "error", "message": "Invalid playbook name."})

        @self.app.route('/api/data')
        def get_data():
            data = {"balance": 0, "positions": [], "market": [], "journals": []}
            env_str = "LIVE" if self.LIVE_MODE else "PAPER"
            try:
                conn = self.get_db_connection()
                cur = conn.cursor(cursor_factory=RealDictCursor)

                cur.execute("SELECT total_capital FROM treasury_state WHERE environment = %s ORDER BY updated_time DESC LIMIT 1;", (env_str,))
                balance_row = cur.fetchone()
                if balance_row: data["balance"] = float(balance_row['total_capital'])
                data["live_mode"] = self.LIVE_MODE

                # UPDATED: Fetching tranche state, targets, AND active status
                cur.execute("""
                    SELECT p.strategy_id, p.symbol, p.status, p.current_tranche, p.max_tranches, 
                           p.qty, p.average_entry_price, p.sl_price, p.tp1_price, p.tp2_price, p.tp3_price,
                           m.is_active
                    FROM positions p
                    JOIN monitored_pairs m ON p.symbol = m.ticker
                    WHERE p.environment = %s 
                    ORDER BY p.status ASC, p.strategy_id ASC;
                """, (env_str,))
                data["positions"] = cur.fetchall()

                # UPDATED: Fetching market data AND active status
                cur.execute("""
                    SELECT l.symbol, l.price, l.closed_price, l.sma, l.atr_pct, l.is_hunting, l.momentum_ignition, l.rsi, m.is_active 
                    FROM live_market_data l
                    JOIN monitored_pairs m ON l.symbol = m.ticker;
                """)
                data["market"] = cur.fetchall()

                # FIXED: Correct ORDER BY, aliased columns to match your JS, fixed the loop indentation
                cur.execute("SELECT updated_time, strategy_id, log_level, message FROM bot_journals WHERE environment = %s ORDER BY updated_time DESC LIMIT 15;", (env_str,))
                journals = []
                for row in cur.fetchall():
                    # Overwrite the datetime object with a string so your JS gets exactly what it expects
                    row['updated_time'] = row['updated_time'].strftime("%m-%d-%y %H:%M:%S")
                    journals.append(row)
                data["journals"] = journals
                
                # --- Inject CFO Treasury State to UI ---
                data["treasury"] = {
                    "total_capital": self.treasury.total_capital,
                    "reserve": self.treasury.reserve,
                    "allocations": self.treasury.allocations,
                    "reconciliation_light": self.treasury.reconciliation_light
                }# --- NEW: Calculate Live Equity ---
                unrealized_pnl = 0.0
                for pos in data["positions"]:
                    if pos['status'] == 'OPEN' and float(pos['qty']) > 0:
                        # Find the matching live market price
                        current_price = 0.0
                        for m in data["market"]:
                            if m['symbol'] == pos['symbol']:
                                current_price = float(m['price'])
                                break
                        
                        # (Current Price - Entry Price) * Quantity
                        qty = float(pos['qty'])
                        entry = float(pos['average_entry_price'])
                        unrealized_pnl += (current_price - entry) * qty

                # Live Equity = Settled Treasury Capital + Unrealized PnL
                data["live_equity"] = data["balance"] + unrealized_pnl

                cur.close()
                conn.close()
                return jsonify({"status": "success", "data": data})
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)})

    def run(self):
        self.app.run(host='0.0.0.0', port=5000, debug=False)
        
        # Inside your app.py telemetry aggregator endpoint:
        actual_balance = float(data['balance']) # $9938.38 from exchange response
        treasury_manager.reconcile_with_exchange_truth(actual_balance)

if __name__ == '__main__':
    server = ExecutiveEngineApp()
    server.run()