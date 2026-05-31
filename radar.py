# radar.py

import os
from dotenv import load_dotenv

load_dotenv()

import requests
import psycopg2

class MarketRadar:
    def __init__(self, logger, max_dynamic_pairs=3):
        self.logger = logger
        self.max_dynamic_pairs = max_dynamic_pairs
        self.core_pairs = ['BTC/USD', 'ETH/USD', 'SOL/USD']
        
        self.db_params = {
            'dbname': os.getenv('DB_NAME'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASS'),
            'host': os.getenv('DB_HOST'),
            'port': os.getenv('DB_PORT', '5432')
        }

    def _get_db_connection(self):
        return psycopg2.connect(**self.db_params)

    def discover_top_movers(self):
        """Scans all of Kraken to find the highest volume USD pairs."""
        self.logger.info("RADAR", "Scanning Kraken for new opportunities...")
        
        try:
            # Get 24h ticker data for all assets on Kraken
            url = "https://api.kraken.com/0/public/Ticker"
            res = requests.get(url).json()
            
            if res.get('error'):
                self.logger.error("RADAR", f"Kraken Error: {res['error']}")
                return
                
            tickers = res['result']
            candidates = []
            
            for krak_sym, data in tickers.items():
                # Only look at USD fiat pairs, ignore margins and weird wraps
                if not krak_sym.endswith('USD') or '.d' in krak_sym: continue
                
                # Format to our clean ticker format (e.g., ADA/USD)
                base = krak_sym.replace('ZUSD', '').replace('USD', '').replace('XX', 'X')
                if base.startswith('X') and len(base) > 3 and base not in ['XRP', 'XLM', 'XMR']:
                    base = base[1:] # Strip Kraken's weird 'X' prefix for crypto
                
                clean_ticker = f"{base}/USD"
                
                # We only want to discover NEW pairs, skip the core ones
                if clean_ticker in self.core_pairs: continue
                
                # Calculate 24h USD Volume
                vol_24h = float(data['v'][1])
                price = float(data['c'][0])
                usd_volume = vol_24h * price
                
                candidates.append({
                    "ticker": clean_ticker,
                    "kraken_symbol": krak_sym,
                    "usd_vol": usd_volume
                })
                
            # Sort by highest volume and take the top N
            candidates.sort(key=lambda x: x['usd_vol'], reverse=True)
            top_picks = candidates[:self.max_dynamic_pairs]
            
            self._update_database(top_picks)
            
        except Exception as e:
            self.logger.error("RADAR", f"Discovery failed: {e}")

    def _update_database(self, top_picks):
        """Updates the monitored_pairs table and prunes dead assets."""
        try:
            conn = self._get_db_connection()
            cur = conn.cursor()
            
            # Step 1: Deactivate everything except our Core Permanent pairs
            cur.execute("""
                UPDATE monitored_pairs 
                SET is_active = FALSE 
                WHERE ticker NOT IN %s;
            """, (tuple(self.core_pairs),))
            
            # Step 2: Insert or Reactivate the top moving pairs
            for pick in top_picks:
                cur.execute("""
                    INSERT INTO monitored_pairs (ticker, kraken_symbol, is_active)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (ticker) DO UPDATE 
                    SET is_active = TRUE;
                """, (pick['ticker'], pick['kraken_symbol']))
                
                self.logger.success("RADAR", f"Tracking Activated: {pick['ticker']} (${pick['usd_vol']:,.0f} 24h Vol)")

            conn.commit()
            cur.close()
            conn.close()
            
            # Trigger the Treasury to redistribute funds immediately!
            from treasury_manager import TreasuryManager
            treasury = TreasuryManager(self.logger, environment=self.logger.environment)
            treasury.execute_playbook("dynamic_equal")
            
        except Exception as e:
            self.logger.error("RADAR", f"Database update failed: {e}")

if __name__ == "__main__":
    from logger import BotLogger
    test_logger = BotLogger()
    radar = MarketRadar(test_logger)
    radar.discover_top_movers()