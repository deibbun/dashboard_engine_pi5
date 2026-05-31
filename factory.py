# factory.py

# In a new file: strategies/factory.py
from .sniper import SniperStrategy
from .dip_buyer import DipBuyerStrategy

def get_strategy(strategy_id):
    registry = {
        "sniper_v1": SniperStrategy(tranche_spacing_pct=0.03, max_tranches=3),
        "dip_buyer": DipBuyerStrategy(tranche_spacing_pct=0.05, max_tranches=4)
    }
    return registry.get(strategy_id)