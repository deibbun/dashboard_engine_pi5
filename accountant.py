# accountant.py

import os
from dotenv import load_dotenv

load_dotenv()

import random

class TradeAccountant:
    def __init__(self, environment="PAPER"):
        self.environment = environment
        # Standard Kraken Pro entry tier fees
        self.maker_fee = 0.0025  # 0.25%
        self.taker_fee = 0.0040  # 0.40%
        self.base_slippage_pct = 0.0005 # 0.05% standard slippage on Pi 5 execution

    def calculate_order_cost(self, target_price, quantity, is_manual_override=False):
        """
        Computes the exact total fiat cost (or gross proceeds) of an execution.
        Forces Taker fees if manually executed via the dashboard panel.
        """
        gross_amount = float(target_price) * float(quantity)
        
        # Determine appropriate fee tier
        fee_rate = self.taker_fee if is_manual_override else self.maker_fee
        execution_fee = gross_amount * fee_rate
        
        # Apply standard execution slippage model
        slippage = gross_amount * self.base_slippage_pct if self.environment == "LIVE" else 0.0
        
        return {
            "gross": round(gross_amount, 2),
            "fee": round(execution_fee, 2),
            "slippage": round(slippage, 2),
            "net_cost": round(gross_amount + execution_fee + slippage, 2)
        }

    def apply_entry_slippage(self, target_price):
        """Simulates buying at a slightly higher price than intended during paper trading."""
        if self.environment == "LIVE":
            return target_price # Live mode relies on actual exchange fills
            
        # Add a random variance to the slippage (e.g., between 0.5x and 2.0x the base slip)
        dynamic_slip = self.base_slippage_pct * random.uniform(0.5, 2.0)
        filled_price = target_price * (1.0 + dynamic_slip)
        return round(filled_price, 4)

    def calculate_exit(self, avg_entry, current_price, qty, is_maker=False):
        """
        Calculates the full financial breakdown of a closed trade.
        Simulates exit slippage if in PAPER mode.
        """
        exit_fee_rate = self.maker_fee if is_maker else self.taker_fee
        
        # Simulate slippage: selling at a slightly lower price than current market
        actual_exit_price = current_price
        if self.environment == "PAPER":
            dynamic_slip = self.base_slippage_pct * random.uniform(0.5, 2.0)
            actual_exit_price = current_price * (1.0 - dynamic_slip)
        
        # 1. Gross Profit/Loss
        gross_pnl = (actual_exit_price - avg_entry) * qty

        # 2. Fee Calculation
        entry_fee_usd = (avg_entry * qty) * self.taker_fee
        exit_fee_usd = (actual_exit_price * qty) * exit_fee_rate
        total_fees = entry_fee_usd + exit_fee_usd

        # 3. Net Realized PnL
        net_pnl = gross_pnl - total_fees

        return {
            "gross_pnl": round(gross_pnl, 4),
            "total_fees": round(total_fees, 4),
            "net_pnl": round(net_pnl, 4),
            "simulated_exit_price": round(actual_exit_price, 4)
        }