# factory.py


from .sniper_strategy import SniperStrategy

def get_strategy(strategy_id):
    registry = {
        "sniper_v1": SniperStrategy(tranche_spacing_pct=0.03, max_tranches=3)
    }
    return registry.get(strategy_id)
    
def normalize_strategy_id(symbol: str) -> str:
    """Ensures strategy IDs align with Kraken Pro asset codes."""
    base = symbol.split('/')[0].upper()
    if base == "BTC":
        base = "XBT"
    return f"{base.lower()}_pure"