# logger.py

import os
from dotenv import load_dotenv

load_dotenv()
import psycopg2

class BotLogger:
    """Handles all database logging for trading strategies."""

    def __init__(self):
        self.db_name = os.getenv('DB_NAME')
        self.db_user = os.getenv('DB_USER')
        self.db_pass = os.getenv('DB_PASS')
        self.db_host = os.getenv('DB_HOST')
        self.db_port = os.getenv('DB_PORT', '5432')
        self.environment = "PAPER"

    def _get_connection(self):
        return psycopg2.connect(
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_pass,
            host=self.db_host,
            port=self.db_port
        )

    def _write_log(self, strategy_id, log_level, message):
        # FIXED: Aligned SQL columns with the actual Postgres schema
        sql = """
            INSERT INTO bot_journals (updated_time, environment, strategy_id, log_level, message)
            VALUES (CURRENT_TIMESTAMP, %s, %s, %s, %s);
        """
        cur = None
        conn = None
        
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(sql, (self.environment, strategy_id, log_level, message))
            conn.commit()
            
            # Still printing to standard output so systemd can capture it if needed
            print(f"[{self.environment}][{log_level}] {strategy_id}: {message}")
            
        except Exception as e:
            print(f"CRITICAL DB LOGGER ERROR: {e} | Original Message: {message}")
        finally:
            if cur: cur.close()
            if conn: conn.close()

    def info(self, strategy_id, message):
        self._write_log(strategy_id, 'INFO', message)

    def warning(self, strategy_id, message):
        self._write_log(strategy_id, 'WARNING', message)

    def error(self, strategy_id, message):
        self._write_log(strategy_id, 'ERROR', message)

    def success(self, strategy_id, message):
        self._write_log(strategy_id, 'SUCCESS', message)