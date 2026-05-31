# sniper_strategy.py

from .base_strategy import BaseStrategy

class SniperStrategy(BaseStrategy):
    def evaluate(self, m):
        is_uptrend = float(m['closed_price']) > float(m['sma'])
        has_momentum = str(m['momentum_ignition']).lower() in ('true', '1', 't')
        not_overbought = float(m['rsi']) < 70
        return "BUY" if (is_uptrend and has_momentum and not_overbought) else "HOLD"