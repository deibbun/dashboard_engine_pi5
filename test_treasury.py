# test_treasury.py
import unittest
from unittest.mock import patch, MagicMock
from treasury_manager import TreasuryManager

class TestTreasuryManager(unittest.TestCase):
    def setUp(self):
        # Mock the DB connection so we don't need a live Postgres instance for unit tests
        self.patcher = patch('treasury_manager.get_db_connection')
        self.mock_db = self.patcher.start()
        self.treasury = TreasuryManager()

    def tearDown(self):
        self.patcher.stop()

    def test_reconcile_with_exchange_truth_imbalance(self):
        """
        Verify that if the internal DB ledger doesn't match the Kraken API truth, 
        the system flags the imbalance rather than silently overwriting.
        """
        # Mock internal state returning $1000, but exchange returns $900
        self.treasury.get_internal_balance = MagicMock(return_value=1000.0)
        
        # Call reconciliation
        result, variance = self.treasury.reconcile_with_exchange_truth(actual_exchange_balance=900.0)
        
        # Expecting a flag/warning, not a silent pass
        self.assertFalse(result, "Treasury should flag a negative variance")
        self.assertEqual(variance, -100.0)

    def test_allocation_limits(self):
        """Ensure the manager blocks negative or over-leveraged trade allocations."""
        self.treasury.get_internal_balance = MagicMock(return_value=500.0)
        
        with self.assertRaises(ValueError):
            self.treasury.allocate_funds(amount=600.0) # Should fail: exceeds balance
            
        with self.assertRaises(ValueError):
            self.treasury.allocate_funds(amount=-50.0) # Should fail: negative allocation