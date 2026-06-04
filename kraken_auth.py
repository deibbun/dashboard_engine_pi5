# kraken_auth.py

import os
from dotenv import load_dotenv

load_dotenv()

import time
import urllib.parse
import hashlib
import hmac
import base64
import requests

class KrakenPrivateClient:
    def __init__(self):
        self.api_url = os.getenv('KRAKEN_URL')
        self.api_key = os.getenv('KRAKEN_API_KEY')
        self.api_secret = os.getenv('KRAKEN_API_SECRET')
        
        # Diagnostic print to prove the keys made it into Python (flushed immediately)
        print(f"🔑 Auth Check - Key Loaded:  {bool(self.api_key)} | Secret Loaded: {bool(self.api_secret)}", flush=True)
        
    def _get_kraken_signature(self, urlpath, data):
        """Generate the required HMAC-SHA512 signature for Kraken Private Endpoints"""
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data['nonce']) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        sigdigest = base64.b64encode(mac.digest())
        return sigdigest.decode()
        
    def get_live_usd_balance(self):
        """Fetches the exact real world balances of all supported assets"""
        if not self.api_key or not self.api_secret:
            print("❌ Auth Error:  Keys are missing from the environment!", flush=True)
            return None
            
        endpoint = "/0/private/Balance"
        url = self.api_url + endpoint
        
        data = {"nonce": str(int(1000 * time.time()))}
        
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._get_kraken_signature(endpoint, data)
        }
        
        try:
            response = requests.post(url, headers=headers, data=data)
            res_json = response.json()
            
            if res_json.get("error"):
                print(f"Kraken API Error:  {res_json['error']}", flush=True)
                return None
                
            raw_balances = res_json.get("result", {})
            #return float(balances.get("ZUSD", 0.0))
            clean_balances = {
                "USD": float(raw_balances.get("ZUSD", 0.0)),
                "XBT": float(raw_balances.get("XXBT", 0.0)),
                "ETH": float(raw_balances.get("XETH", 0.0)),
                "SOL": float(raw_balances.get("SOL", 0.0))
            }
            
            return clean_balances
            
        except Exception as e:
            print(f"Network Error:  fetch live balances: {e}", flush=True)
            return None