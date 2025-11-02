def analyze_for_signal(df):
    """
    Detect stop-loss hunt + bullish confirmation setup
    Returns dictionary with signal details if found, else None
    """
    import numpy as np
    import pandas as pd

    out = {
        'signal': None,
        'entry_price': None,
        'stoploss': None,
        'target': None,
        'support': None
    }

    # basic checks
    if df is None or len(df) < 5:
        return out

    # latest candle info
    last_candle = df.iloc[-1]

    # support as last 10-candle low
    support = df['Low'].rolling(window=10).min().iloc[-1]

    # safe handling (NaN ya invalid case)
    if pd.isna(support):
        out['support'] = None
        return out
    else:
        support_val = float(support)
        out['support'] = support_val

    # stop-hunt logic
    wick_pct = ((support_val - last_candle['Low']) / support_val) * 100 if support_val > 0 else 0

    # volume spike check
    vol_ma = df['Volume'].rolling(window=10).mean().iloc[-1]
    vol_spike = last_candle['Volume'] > (1.8 * vol_ma if not pd.isna(vol_ma) else 0)

    # bullish candle confirmation
    bullish_confirm = (
        (last_candle['Close'] > last_candle['Open']) and
        (last_candle['Close'] > support_val)
    )

    # final condition
    if (wick_pct > 0.5) and vol_spike and bullish_confirm:
        out['signal'] = "BUY"
        out['entry_price'] = float(last_candle['Close'])
        out['stoploss'] = float(last_candle['Low'])
        out['target'] = float(last_candle['Close'] + (last_candle['Close'] - last_candle['Low']) * 2)

    return out