# test_accountant.py

import unittest
from accountant import TradeAccountant

class TestTradeAccountant(unittest.TestCase):
    def setUp(self):
        self.accountant = TradeAccountant()
        # Assume base fees: maker = 0.0025 (0.25%), taker = 0.0040 (0.40%), slippage = 0.0005 (0.05%)

    def test_standard_maker_fee_calculation(self):
        """Test standard limit order fee calculations (Maker)."""
        cost = self.accountant.calculate_order_cost(size=100, price=1.0, is_manual_override=False)
        
        # 100 * 1.0 = 100. Maker fee (0.25%) = 0.25. Slippage (0.05%) = 0.05. Total = 0.30
        self.assertAlmostEqual(cost['total_fee'], 0.30, places=4)
        self.assertEqual(cost['fee_type'], 'maker')

    def test_manual_override_forces_taker_fees(self):
        """Test that forcing a market order via the UI strictly applies Taker fees."""
        cost = self.accountant.calculate_order_cost(size=100, price=1.0, is_manual_override=True)
        
        # 100 * 1.0 = 100. Taker fee (0.40%) = 0.40. Slippage (0.05%) = 0.05. Total = 0.45
        self.assertAlmostEqual(cost['total_fee'], 0.45, places=4)
        self.assertEqual(cost['fee_type'], 'taker')