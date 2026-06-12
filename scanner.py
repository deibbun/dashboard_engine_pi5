# scanner.py

import os
from dotenv import load_dotenv

load_dotenv()

import time
import psycopg2
from psycopg2.extras import RealDictCursor
from logger import BotLogger
from market_oracle import KrakenOracle
from execution_engine import ExecutionEngine
from radar import MarketRadar
from kraken_auth import KrakenPrivateClient

def get_active_environment():
    """Checks the system_config table to see if the UI switched us to LIVE or PAPER."""
    try:
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASS'), host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', '5432')
        )
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_config WHERE key = 'trading_mode';")
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if row:
            return row[0]
        return "PAPER" # Default fallback
    except:
        return "PAPER"

def main():
    db_log = BotLogger()
    
    # Establish initial environment
    active_env = get_active_environment()
    db_log.environment = active_env
    
    oracle = KrakenOracle(db_log)
    radar = MarketRadar(db_log, max_dynamic_pairs=3)
    
    # FIXED: Initialize ExecutionEngine outside the loop to save memory and CPU
    trader = ExecutionEngine(db_log, environment=active_env)

    db_log.info("SYSTEM", f"🚀 Headless Scanner Booting (Current Mode: {active_env})...")

    loops = 0

    while True:
        try:
            # FIXED (Split-Brain): Sync environment with the Web Dashboard every loop
            current_env = get_active_environment()
            if current_env != active_env:
                active_env = current_env
                db_log.environment = active_env
                trader.environment = active_env
                trader.accountant.environment = active_env
                
                if active_env == "LIVE":
                    trader.kraken_client = KrakenPrivateClient()
                else:
                    trader.kraken_client = None
                
                db_log.info("SYSTEM", f"🔄 Scanner detected environment shift to {active_env.upper()}")

            # 1. Run the Radar (Every 240 loops ~ 1 hour)
            if loops % 240 == 0:
                radar.discover_top_movers()

            # 2. The Eyes: Update prices and indicators
            oracle.scan_markets()

            # 3. The Hands: Manage tranches and execute trades
            trader.run_cycle()

            loops += 1

        except Exception as e:
            db_log.error("SYSTEM", f"Scanner loop exception: {e}")

        # The ultimate sniper heartbeat: 15 seconds
        time.sleep(15)

# FIXED: Added the required method call to prevent EOF Syntax/Indentation error
if __name__ == "__main__":
    main()