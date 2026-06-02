# test_market_oracle.py

import unittest
import requests
from unittest.mock import patch, MagicMock
from market_oracle import KrakenOracle

class TestMarketOracle(unittest.TestCase):
    def setUp(self):
        self.oracle = KrakenOracle()

    @patch('market_oracle.requests.get')
    def test_chaos_monkey_timeout_handling(self, mock_get):
        """
        Verify that a timeout exception from the API is intercepted and handled 
        safely, returning a fallback state instead of crashing the scanner loop.
        """
        # Force the mock API call to raise a Timeout
        mock_get.side_effect = requests.exceptions.Timeout("Chaos monkey strike!")

        # The oracle should catch this and return None (or a specific error dict), NOT raise an exception
        try:
            result = self.oracle.fetch_ticker_data("XXBTZUSD")
            self.assertIsNone(result, "Oracle should return None on timeout to keep the engine alive.")
        except Exception as e:
            self.fail(f"Oracle failed to intercept the timeout exception. Crashed with: {e}")