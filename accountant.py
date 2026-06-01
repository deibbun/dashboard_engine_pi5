# accountant.py

import os
from dotenv import load_dotenv

load_dotenv()

class TradeAccountant:
    """Strictly handles the math for trades, fees, and PnL."""
    
    def __init__(self):
        # Load fee tiers once when instantiated
        self.taker_fee = float(os.getenv('KRAKEN_TAKER_FEE', 0.0025))
        self.maker_fee = float(os.getenv('KRAKEN_MAKER_FEE', 0.0040))

    def calculate_exit(self, avg_entry, exit_price, qty, is_maker=False):
        """
        Calculates the full financial breakdown of a closed trade.
        Returns a dictionary with gross PnL, total fees, and net PnL.
        """
        # Determine which fee tier applies to the exit
        exit_fee_rate = self.maker_fee if is_maker else self.taker_fee
        
        # 1. Gross Profit/Loss
        gross_pnl = (exit_price - avg_entry) * qty

        # 2. Fee Calculation (Assuming entry was a market/taker order)
        entry_fee_usd = (avg_entry * qty) * self.taker_fee
        exit_fee_usd = (exit_price * qty) * exit_fee_rate
        total_fees = entry_fee_usd + exit_fee_usd

        # 3. Net Realized PnL
        net_pnl = gross_pnl - total_fees

        return {
            "gross_pnl": round(gross_pnl, 4),
            "total_fees": round(total_fees, 4),
            "net_pnl": round(net_pnl, 4)
        }