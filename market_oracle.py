# market_oracle.py

import os
from dotenv import load_dotenv
import time
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
import concurrent.futures

load_dotenv()

class KrakenOracle:
    def __init__(self, logger):
        self.logger = logger
        self.base_url = "https://api.kraken.com/0/public"
        
        self.db_params = {
            'dbname': os.getenv('DB_NAME'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASS'),
            'host': os.getenv('DB_HOST'),
            'port': os.getenv('DB_PORT', '5432')
        }
        
    def _get_db_connection(self):
        return psycopg2.connect(**self.db_params)

    def fetch_active_pairs(self):
        """Pulls the dynamic list of monitored pairs from the database."""
        try:
            conn = self._get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT ticker, kraken_symbol FROM monitored_pairs WHERE is_active = TRUE;")
            pairs = cur.fetchall()
            cur.close()
            conn.close()
            return pairs
        except Exception as e:
            self.logger.error("ORACLE", f"Failed to fetch monitored pairs: {e}")
            return []
            
    def fetch_ohlc_data(self, kraken_symbol, interval=60):
        """Pulls the latest hourly candlestick data from Kraken."""
        url = f"{self.base_url}/OHLC?pair={kraken_symbol}&interval={interval}"
            
        try:
            response = requests.get(url)
            data = response.json()
                
            if data['error']:
                self.logger.error("ORACLE", f"Kraken API Error for {kraken_symbol}: {data['error']}")
                return None
                
            candles = data['result'][kraken_symbol]
            return candles
                
        except Exception as e:
            self.logger.error("ORACLE", f"Network failure fetching {kraken_symbol}: {e}")
            return None
                
    def calculate_indicators(self, candles, period=14):
        """Calculates indicators strictly on closed candles, but returns live price for the UI."""
        if not candles or len(candles) < period + 2:
            return None, None, None, None, None, None
            
        live_price = float(candles[-1][4])
        closed_history = candles[-(period+2):-1]
        
        closes = [float(candle[4]) for candle in closed_history]
        highs = [float(candle[2]) for candle in closed_history]
        lows = [float(candle[3]) for candle in closed_history]
        volumes = [float(candle[6]) for candle in closed_history]
        
        closed_price = closes[-1]
        sma = sum(closes[1:]) / period
        
        # 1. ATR Percentage
        true_ranges = [highs[i] - lows[i] for i in range(1, len(closes))]
        atr = sum(true_ranges) / period
        atr_pct = (atr / closed_price) * 100 if closed_price > 0 else 0
        
        # 2. Momentum Ignition
        avg_volume = sum(volumes[1:-1]) / (period - 1)
        current_volume = volumes[-1]
        price_moving_up = closes[-1] > closes[-2]
        momentum_ignition = (current_volume > (avg_volume * 2)) and price_moving_up
        
        # 3. RSI
        gains = []
        losses = []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
                
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
        return live_price, closed_price, sma, round(atr_pct, 2), momentum_ignition, round(rsi, 2)
            
    def update_database(self, symbol, live_price, closed_price, sma, atr_pct, momentum, rsi):
        """Pushes data into the DB using an UPSERT to support dynamic pairs."""
        live_price = float(live_price)
        closed_price = float(closed_price)
        sma = float(sma)
        atr_pct = float(atr_pct)
        rsi = float(rsi)
        momentum = bool(momentum)
        is_hunting = (closed_price > sma) and momentum and (rsi < 70)
        
        # UPSERT: Insert if new pair, otherwise update the existing row.
        sql = """
            INSERT INTO live_market_data (symbol, price, closed_price, sma, atr_pct, momentum_ignition, rsi, is_hunting, last_updated)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol) DO UPDATE 
            SET price = EXCLUDED.price, 
                closed_price = EXCLUDED.closed_price, 
                sma = EXCLUDED.sma, 
                atr_pct = EXCLUDED.atr_pct, 
                momentum_ignition = EXCLUDED.momentum_ignition, 
                rsi = EXCLUDED.rsi, 
                is_hunting = EXCLUDED.is_hunting, 
                last_updated = CURRENT_TIMESTAMP;
        """
        try:
            conn = self._get_db_connection()
            cur = conn.cursor()
            cur.execute(sql, (symbol, live_price, closed_price, sma, atr_pct, momentum, rsi, is_hunting))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            self.logger.error("ORACLE", f"Database update failed for {symbol}: {e}")

    def process_pair(self, pair_data):
        """Worker function for concurrent scanning."""
        ticker = pair_data['ticker']
        kraken_symbol = pair_data['kraken_symbol']
        
        candles = self.fetch_ohlc_data(kraken_symbol, interval=60)
        if candles:
            live_price, closed_price, sma, atr_pct, momentum, rsi = self.calculate_indicators(candles, period=14)
            if live_price and sma:
                self.update_database(ticker, live_price, closed_price, sma, atr_pct, momentum, rsi)
                return f"{ticker} success"
        return f"{ticker} failed"
                
    def scan_markets(self):
        """Checks all tracked pairs concurrently."""
        self.logger.info("ORACLE", "Initiating dynamic market scan...")
        active_pairs = self.fetch_active_pairs()
        
        if not active_pairs:
            self.logger.warning("ORACLE", "No active pairs found in monitored_pairs table.")
            return

        # Fire concurrent requests using a thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(self.process_pair, pair) for pair in active_pairs]
            concurrent.futures.wait(futures)
            
        self.logger.success("ORACLE", f"Market scan complete. {len(active_pairs)} pairs processed.")
            
if __name__ == "__main__":
    from logger import BotLogger
    test_logger = BotLogger()
    oracle = KrakenOracle(test_logger)
    oracle.scan_markets()
                