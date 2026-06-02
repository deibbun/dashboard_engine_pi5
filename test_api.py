# test_api.py

import unittest
import json
from unittest.mock import patch, MagicMock
from app import ExecutiveEngineApp

class TestDashboardAPI(unittest.TestCase):

    def setUp(self):
        # Initialize the app but swap out the real dependencies so it doesn't hit Kraken
        with patch('app.KrakenPrivateClient'), patch('app.BotLogger'):
            self.engine = ExecutiveEngineApp()
            
            # Set to paper mode for maximum safety during tests
            self.engine.LIVE_MODE = False 
            self.engine.db_log.environment = "TEST"
            
            # Create the test client using the encapsulated Flask app
            self.client = self.engine.app.test_client()

    # Block the real database connection
    @patch('app.ExecutiveEngineApp.get_db_connection')
    def test_change_play_route(self, mock_db_conn):
        """Test shifting the treasury playbook via API."""
        # Mock the treasury execution so it returns True (success)
        self.engine.treasury.execute_playbook = MagicMock(return_value=True)

        # Simulate the frontend sending JSON to the change_play endpoint
        payload = {"play_name": "defensive"}
        response = self.client.post('/api/change_play', json=payload)
        
        # Verify the server responded with a 200 OK
        self.assertEqual(response.status_code, 200)
        
        # Verify the JSON payload matches our expected success structure
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["message"], "Strategy shifted to defensive")

    def test_change_play_empty_payload(self):
        """Simulate a broken frontend sending empty data to change_play."""
        response = self.client.post('/api/change_play', json={})
        data = response.get_json()
        
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["message"], "No play_name provided.")

    @patch('app.ExecutiveEngineApp.get_db_connection')
    @patch('app.KrakenPrivateClient')
    def test_toggle_mode(self, mock_kraken, mock_db_conn):
        """Test toggling between PAPER and LIVE modes."""
        # Setup mock kraken balance so the live switch doesn't fail
        mock_instance = mock_kraken.return_value
        mock_instance.get_live_usd_balance.return_value = {"USD": 5000.0}

        response = self.client.post('/api/toggle_mode')
        
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        
        # We started in PAPER (False), so toggling makes it True (LIVE)
        self.assertTrue(data["live_mode"])

if __name__ == '__main__':
    unittest.main()