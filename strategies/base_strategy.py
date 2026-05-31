# base_strategy.py

class BaseStrategy:
    """The contract that all strategies must follow."""
    def __init__(self, tranche_spacing_pct, max_tranches):
        self.tranche_spacing_pct = tranche_spacing_pct
        self.max_tranches = max_tranches
    
    def evaluate(self, market_data):
        raise NotImplementedError("Each strategy must implement its own evaluate method.")