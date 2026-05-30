# scanner.py

import os
from dotenv import load_dotenv

load_dotenv()

import time
from logger import BotLogger
from market_oracle import KrakenOracle
from execution_engine import ExecutionEngine

# Change this to "LIVE" when you are ready to trade real capital
ACTIVE_ENVIRONMENT = "PAPER"

def main():
    db_log = BotLogger()
    db_log.environment = ACTIVE_ENVIRONMENT
    oracle = KrakenOracle(db_log)
    
    db_log.info("SYSTEM", f"🚀 Headless Scanner Booting in {ACTIVE_ENVIRONMENT} mode...")
    
    while True:
        try:
            # 1. The Eyes: Update prices and indicators
            oracle.scan_markets()
            
            # 2. The Hands: Manage tranches and execute trades
            trader = ExecutionEngine(db_log, environment=ACTIVE_ENVIRONMENT)
            trader.run_cycle()
            
        except Exception as e:
            db_log.error("SYSTEM", f"Scanner loop exception: {e}")
            
        # The ultimate sniper heartbeat: 15 seconds
        time.sleep(15)

if __name__ == "__main__":
    main()