# scanner.py

import os
from dotenv import load_dotenv

load_dotenv()


import timefrom logger import BotLogger
from market_oracle import KrakenOracle
from execution_engine import ExecutionEngine
from radar import MarketRadar  # <-- Import the Radar

ACTIVE_ENVIRONMENT = "PAPER"

def main():
    db_log = BotLogger()
    db_log.environment = ACTIVE_ENVIRONMENT
    oracle = KrakenOracle(db_log)
    radar = MarketRadar(db_log, max_dynamic_pairs=3) # Allow 3 wildcards
    
    db_log.info("SYSTEM", f"🚀 Headless Scanner Booting in {ACTIVE_ENVIRONMENT} mode...")
    
    loops = 0
    
    while True:
        try:
            # 1. Run the Radar (Every 240 loops ~ 1 hour)
            if loops % 240 == 0:
                radar.discover_top_movers()

            # 2. The Eyes: Update prices and indicators
            oracle.scan_markets()
            
            # 3. The Hands: Manage tranches and execute trades
            trader = ExecutionEngine(db_log, environment=ACTIVE_ENVIRONMENT)
            trader.run_cycle()
            
            loops += 1
            
        except Exception as e:
            db_log.error("SYSTEM", f"Scanner loop exception: {e}")
            
        # The ultimate sniper heartbeat: 15 seconds
        time.sleep(15)

if __name__ == "__main__":
    main()