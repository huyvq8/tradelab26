
def run_strategies(prices):

    signals = []

    for symbol, price in prices.items():
        if price % 2 > 1:   # placeholder logic
            signals.append({
                "symbol": symbol,
                "action": "buy",
                "confidence": 0.6
            })

    return signals
