from strategies.base_strategy import BaseStrategy

class SniperStrategy(BaseStrategy):
    def __init__(self, tranche_spacing_pct=0.015, max_tranches=3, **kwargs):
        super().__init__(tranche_spacing_pct=tranche_spacing_pct, max_tranches=max_tranches)
        # Strategy-specific execution thresholds
        self.name = "sniper_v1"
        self.rsi_oversold = 40.0      # Generates alpha on lower-bound exhaustions
        self.rsi_overbought = 70.0     # Cap limits
        self.max_tranches = 3          # Aligns with your dashboard telemetry config
        self.tranche_spacing_pct = kwargs.get('tranche_spacing_pct', 0.015)

    def evaluate(self, market_data: dict) -> str:
        """Processes real-time indicator streams to trigger initial entries (Tranche 1). Expects keys: price, rsi, sma, momentum_ignition, is_hunting"""
        try:
            current_price = float(market_data.get('price', 0))
            rsi = float(market_data.get('rsi', 50))
            sma = float(market_data.get('sma', current_price))
            momentum_ignition = bool(market_data.get('momentum_ignition', False))
            
            # --- STRATEGY BUY RULES ---
            # 1. Price is trading below the baseline SMA (mean-reversion discount)
            # 2. RSI is indicating an oversold territory or accumulation zone
            # 3. Momentum Ignition hasn't blown off top yet (not chasing vertical spikes)
            if current_price < sma and rsi <= self.rsi_oversold and not momentum_ignition:
                return "BUY"
                
            # If explicit hunting/momentum criteria match your advanced conditions:
            if bool(market_data.get('is_hunting', False)) and rsi < 45.0:
                return "BUY"

            return "HOLD"
            
        except Exception as e:
            # Prevent background threads from crashing if a pair sends a bad string format
            return "HOLD"

    def calculate_brackets(self, entry_price: float, atr_pct: float) -> dict:
        """
        Calculates hard risk brackets for Kraken Pro execution based on asset volatility.
        Uses ATR (Average True Range) to make stops dynamic rather than fixed percentages.
        """
        # Fallback to a baseline 1.5% volatility buffer if ATR is missing or compressed
        volatility_buffer = atr_pct if atr_pct > 0.001 else 0.015
        
        # Stop Loss: Placed 2x ATR below entry price to survive market noise
        sl_price = entry_price * (1.0 - (volatility_buffer * 2.0))
        
        # Split Profit Targets (Scaling Out dynamically)
        tp1_price = entry_price * (1.0 + (volatility_buffer * 1.5))  # First take-profit
        tp2_price = entry_price * (1.0 + (volatility_buffer * 3.0))  # Runner tranche
        tp3_price = entry_price * (1.0 + (volatility_buffer * 4.5))  # Moon bag
        
        return {
            "sl_price": round(sl_price, 5),
            "tp1_price": round(tp1_price, 5),
            "tp2_price": round(tp2_price, 5),
            "tp3_price": round(tp3_price, 5)
        }

    def evaluate_scale_in(self, current_price: float, avg_entry_price: float, current_tranche: int, atr_pct: float) -> bool:
        """
        Determines if an open position qualifies for a Tranche 2 or Tranche 3 scale-in.
        """
        if current_tranche >= self.max_tranches:
            return False  # Max risk allocation reached for this asset block
            
        volatility_buffer = atr_pct if atr_pct > 0.001 else 0.015
        # Scale in rule: Price drops 1.5x ATR below your average cost (Dollar Cost Averaging into value)
        drop_threshold = avg_entry_price * (1.0 - (volatility_buffer * 1.5))
        
        return current_price <= drop_threshold