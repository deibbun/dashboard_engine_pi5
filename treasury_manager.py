# treasury_manager.py

import json

class TreasuryManager:
    def __init__(self, logger, initial_capital=10009.58, environment="PAPER"):
        self.db_log = logger
        self.environment = environment
        self.total_capital = initial_capital
        self.reserve = initial_capital
        self.reconciliation_light = "YELLOW"
        self.allocations = {
            "btc_pure": 0.0,
            "eth_pure": 0.0,
            "sol_pure": 0.0,
            "master": 0.0
        }
        
        self.history = []
        
        self.execute_playbook("normal_split")
        
    def verify_reality(self, kraken_actual_balance):
        """The ultimate safety switch"""
        # Allow a tiny 5-cent margin of error for floating point math
        if kraken_actual_balance >= (self.total_capital - 0.05):
            self.reconciliation_light = "GREEN"
            return True
        else:
            self.reconciliation_light = "RED"
            self.total_capital = kraken_actual_balance
            self.execute_playbook("defensive")
            self.db_log.error("TREASURY", "REALITY CHECK FAILED:  Allocations Zeroed.")
            return False
            
    def _save_state_to_db(self, play_name):
        """Writes the new funding strategy to PostgreSQL"""
        sql = """
            INSERT INTO treasury_state(environment, play_name, total_capital, reserve, allocations)
            VALUES (%s, %s, %s, %s, %s);
        """
        try:
            conn = self.db_log._get_connection()
            cur = conn.cursor()
            cur.execute(sql, (
                self.environment,
                play_name,
                self.total_capital,
                self.reserve,
                json.dumps(self.allocations)
            ))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"CRITICAL TREASURY DB ERROR: {e}")
        
    def _save_state(self):
        """Takes a snapshot of current funding before shifting."""
        state = {
            "reserve": self.reserve,
            "allocations": self.allocations.copy()
        }
        
    def undo_last_shift(self):
        """Pops the last snapshot and restores the funding."""
        if self.history:
            last_state = self.history.pop()
            self.reserve = last_state["reserve"]
            self.allocations = last_state["allocations"]
            return True
        return False
        
    def execute_playbook(self, play_name):
        """Re-deals the total capital based on target percentages dynamically."""
        # Static Legacy Playbooks
        plays = {
            "normal_split": {"btc_pure": 0.166, "master": 0.166, "eth_pure": 0.30, "sol_pure": 0.30},
            "sol_breakout": {"btc_pure": 0.05, "master": 0.15, "eth_pure": 0.15, "sol_pure": 0.60},
            "eth_run": {"btc_pure": 0.05, "master": 0.15, "eth_pure": 0.60, "sol_pure": 0.15},
            "defensive": {"master": 1.0} # Lock everything in the master reserve
        }
        
        self._save_state()
        weights = {}
        
        if play_name == "dynamic_equal":
            # DYNAMIC PLAYBOOK: Ask the database what we are currently scanning
            try:
                conn = self.db_log._get_connection()
                cur = conn.cursor()
                cur.execute("SELECT ticker FROM monitored_pairs WHERE is_active = TRUE;")
                active_pairs = cur.fetchall()
                cur.close()
                conn.close()
                
                count = len(active_pairs)
                if count > 0:
                    # Keep 10% in reserve, split the remaining 90% across active pairs
                    weight_per_pair = 0.90 / count
                    for row in active_pairs:
                        # Convert "ADA/USD" -> "ada_pure"
                        base_asset = row[0].split('/')[0].lower()
                        weights[f"{base_asset}_pure"] = weight_per_pair
                        
                    weights["master"] = 0.10
                else:
                    weights = {"master": 1.0} # Defensive fallback
            except Exception as e:
                self.db_log.error("TREASURY", f"Failed to build dynamic playbook: {e}")
                weights = {"master": 1.0}
                
        elif play_name in plays:
            weights = plays[play_name]
        else:
            return False
            
        allocated_total = 0.0
        self.allocations = {} # Clear old allocations
        
        # Mathematically distribute the capital
        for bot, weight in weights.items():
            amount = round(self.total_capital * weight, 2)
            self.allocations[bot] = amount
            allocated_total += amount
            
        self.reserve = round(self.total_capital - allocated_total, 2)
        self._save_state_to_db(play_name)
        return True