# app.py
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import time
import requests
from datetime import datetime, timedelta

st.set_page_config(page_title="One Day Stock Alerts", layout="wide")
st.title("One Day Stock Alerts — One stock, full day monitor")
st.caption("Salman — stop-loss-hunt + confirmation. Telegram alerts with entry & exit (TP/SL).")

# -------------------------
# --- CONFIG / SECRETS ---
# -------------------------
# Put these in Streamlit secrets:
# [telegram]
# token = "123:ABC..."
# chat_id = "987654321"
try:
    TG_TOKEN = st.secrets["telegram"]["token"]
    TG_CHAT = st.secrets["telegram"]["chat_id"]
except Exception:
    TG_TOKEN = None
    TG_CHAT = None

# -------------------------
# --- Sidebar params ------
# -------------------------
st.sidebar.header("Monitor Settings")
stock_candidates = st.sidebar.text_area("Stock candidates (comma separated tickers)", 
                                        value="RELIANCE.NS, TCS.NS, HDFCBANK.NS, ICICIBANK.NS, INFY.NS")
candidates = [s.strip().upper() for s in stock_candidates.split(",") if s.strip()]

interval = st.sidebar.selectbox("Candle interval", ["1m","2m","5m","15m"], index=2)
lookback_minutes = st.sidebar.number_input("Support lookback (minutes)", value=120, min_value=10)
wick_threshold = st.sidebar.number_input("Wick % below support (eg 0.7)", value=0.7, step=0.1)
vol_spike_mult = st.sidebar.number_input("Volume spike multiplier", value=2.0, step=0.1)
vol_ma_period = st.sidebar.number_input("Volume MA period", value=20, min_value=1)
bullish_confirm = st.sidebar.number_input("Bullish confirm candles", value=1, min_value=1)
atr_period = st.sidebar.number_input("ATR period", value=14, min_value=1)
target_R = st.sidebar.number_input("Target (R multiple)", value=2.0, step=0.1)
refresh_secs = st.sidebar.number_input("Poll every (seconds)", value=30, min_value=10)

st.sidebar.markdown("---")
st.sidebar.markdown("Telegram token & chat_id must be set in Streamlit secrets to send alerts.")
if TG_TOKEN is None or TG_CHAT is None:
    st.sidebar.error("Telegram secrets not found. Alerts will not be sent until secrets configured.")

# -------------------------
# --- Helper functions ----
# -------------------------
def send_telegram(message: str):
    """Send message prefixed with title 'One Day Stock Alerts'"""
    title = "One Day Stock Alerts"
    text = f"*{title}*\n\n{message}"
    if TG_TOKEN is None or TG_CHAT is None:
        # Show in-app fallback if secrets not set
        st.warning("Telegram secrets missing — can't send Telegram. Message preview:")
        st.code(text)
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, data=payload, timeout=10)
        return resp.ok
    except Exception as e:
        st.error(f"Failed to send Telegram: {e}")
        return False

def fetch_ohlcv(sym, interval, period_days=7):
    """Fetch OHLCV using yfinance"""
    try:
        df = yf.download(tickers=sym, period=f"{period_days}d", interval=interval, progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.dropna()
        return df
    except Exception as e:
        st.error(f"Data fetch error for {sym}: {e}")
        return pd.DataFrame()

def atr(df, n=14):
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def detect_support(df, lookback_bars=50):
    return df['Low'].iloc[-lookback_bars:].min()

def analyze_for_signal(df):
    out = {}
    if df.shape[0] < max(vol_ma_period, atr_period)+10:
        out['ok'] = False
        out['reason'] = "Not enough data"
        return out
    df = df.copy()
    df['vol_ma'] = df['Volume'].rolling(vol_ma_period).mean()
    df['ATR'] = atr(df, atr_period)
    # compute lookback bars approx
    # Get seconds per candle
    try:
        delta = (df.index[-1] - df.index[-2]).total_seconds()
        secs = max(1, int(delta))
    except Exception:
        secs = 60
    lookback_bars = int((lookback_minutes * 60) / secs)
    lookback_bars = max(10, min(lookback_bars, len(df)-2))
    support = detect_support(df, lookback_bars)
    cand_idx = None
    # scan last 8 candles for stop-hunt like behavior
    for i in range(len(df)-2, max(len(df)-10, 0), -1):
        c = df.iloc[i]
        wick_pct = np.where((support.fillna(0) > 0), ((support - c['Low'])/support)*100, 0)
        vol_baseline = df['vol_ma'].iloc[i] if df['vol_ma'].iloc[i]>0 else 1
        vol_mult = c['Volume'] / vol_baseline
        if wick_pct >= wick_threshold and vol_mult >= vol_spike_mult:
            cand_idx = i
            break
    out['support'] = float(support) if not np.isnan(support) else None
    out['cand_idx'] = cand_idx
    if cand_idx is None:
        out['ok'] = False
        out['reason'] = "No stop-hunt candle"
        return out
    # confirm bullish candles after cand_idx
    confirm_ok = True
    for j in range(1, bullish_confirm+1):
        idx = cand_idx + j
        if idx >= len(df):
            confirm_ok = False
            break
        c = df.iloc[idx]
        if not (c['Close'] > c['Open'] and c['Close'] > support):
            confirm_ok = False
            break
    if not confirm_ok:
        out['ok'] = False
        out['reason'] = "No bullish confirmation"
        return out
    # form entry/sl/target
    entry_idx = cand_idx + bullish_confirm
    entry_price = float(df.iloc[entry_idx]['Close'])
    atr_val = float(df['ATR'].iloc[-1])
    sl = float(df.iloc[cand_idx]['Low']) - 0.0  # buffer 0 (user can tune)
    if sl >= entry_price:
        # fallback: use ATR-based SL
        sl = entry_price - 1.5 * atr_val
    rr_dist = entry_price - sl
    target = entry_price + target_R * rr_dist
    out.update({
        'ok': True,
        'entry_price': entry_price,
        'stop_loss': float(sl),
        'target': float(target),
        'atr': atr_val,
        'entry_time': str(df.index[entry_idx]),
        'cand_index': cand_idx
    })
    return out

# -------------------------
# --- Stock selection -----
# -------------------------
def choose_best_stock(candidates):
    # simple: pick stock with highest average volume last 5 days and positive short-term momentum
    scores = []
    for s in candidates:
        df = fetch_ohlcv(s, interval, period_days=7)
        if df.empty:
            continue
        # avg volume
        avg_vol = float(df['Volume'].tail(5*60 if interval.endswith("m") else 20).mean() if len(df)>5 else df['Volume'].mean())
        # momentum = return last 3 closes
        try:
            momentum = (df['Close'].iloc[-1] - df['Close'].iloc[-5]) / df['Close'].iloc[-5]
        except Exception:
            momentum = 0.0
        scores.append((s, avg_vol, momentum))
    if not scores:
        return None
    # rank by avg_vol * (1 + momentum)
    scored = [(s, v*(1+max(0, m))) for (s,v,m) in scores]
    scored = sorted(scored, key=lambda x: x[1], reverse=True)
    return scored[0][0]

# -------------------------
# --- App state / UI -----
# -------------------------
if 'selected_stock' not in st.session_state:
    st.session_state['selected_stock'] = None
if 'active_trade' not in st.session_state:
    st.session_state['active_trade'] = None  # dict with entry, sl, target, time, qty, entry_index
if 'monitoring' not in st.session_state:
    st.session_state['monitoring'] = False

col1, col2 = st.columns([2,1])
with col1:
    st.header("Monitor")
    if st.button("Choose best stock (auto)"):
        chosen = choose_best_stock(candidates)
        st.session_state['selected_stock'] = chosen
    manual = st.text_input("Or enter stock manually (eg RELIANCE.NS)", value="")
    if manual.strip():
        st.session_state['selected_stock'] = manual.strip().upper()
    st.markdown(f"**Selected stock:** `{st.session_state.get('selected_stock')}`")
    if st.button("Start monitoring"):
        st.session_state['monitoring'] = True
    if st.button("Stop monitoring"):
        st.session_state['monitoring'] = False

    st.markdown("### Live chart (Close)")
    chart_area = st.empty()
    info_area = st.empty()

with col2:
    st.header("Active Trade / Alerts")
    if st.session_state['active_trade']:
        tr = st.session_state['active_trade']
        st.success(f"Active Entry at {tr['entry_price']:.2f} (SL {tr['stop_loss']:.2f}, Target {tr['target']:.2f})")
        st.write(tr)
    else:
        st.info("No active trade currently.")

# -------------------------
# --- Monitoring loop -----
# -------------------------
def monitor_once(symbol):
    df = fetch_ohlcv(symbol, interval, period_days=7)
    if df.empty:
        return
    chart_area.line_chart(df['Close'])
    res = analyze_for_signal(df)
    info_area.json({k:v for k,v in res.items() if k in ('ok','reason','entry_price','stop_loss','target','atr')})
    # If no active trade, check for entry
    if st.session_state['active_trade'] is None and res.get('ok'):
        # record trade
        entry = res['entry_price']
        sl = res['stop_loss']
        tgt = res['target']
        entry_time = res['entry_time']
        trade = {
            "symbol": symbol,
            "entry_price": entry,
            "stop_loss": sl,
            "target": tgt,
            "entry_time": entry_time,
            "atr": res.get('atr'),
            "status": "open"
        }
        st.session_state['active_trade'] = trade
        msg = (f"ENTRY detected for `{symbol}`\nEntry: {entry:.2f}\nSL: {sl:.2f}\nTarget: {tgt:.2f}\n"
               f"Time: {entry_time}\n\n*If you took this trade, monitor position.*")
        send_telegram(msg)
    # If active trade exists, check for TP/SL hit in latest candle(s)
    if st.session_state['active_trade'] is not None:
        tr = st.session_state['active_trade']
        if tr['status'] != 'open':
            return
        # check if target or sl hit in last N bars (use last 10 bars)
        recent = df.tail(10)
        hit_target = (recent['High'] >= tr['target']).any()
        hit_sl = (recent['Low'] <= tr['stop_loss']).any()
        if hit_target or hit_sl:
            # determine which hit first by scanning forward
            outcome = None
            hit_price = None
            for idx, row in recent.iterrows():
                if row['High'] >= tr['target']:
                    outcome = 'TP'
                    hit_price = float(min(row['High'], tr['target']))  # use target price for calculation
                    break
                if row['Low'] <= tr['stop_loss']:
                    outcome = 'SL'
                    hit_price = float(max(row['Low'], tr['stop_loss']))
                    break
            # compute profit per share
            entry_price = tr['entry_price']
            if outcome == 'TP':
                profit_per_share = hit_price - entry_price
            else:
                profit_per_share = hit_price - entry_price
            profit_pct = (profit_per_share / entry_price) * 100
            tr['status'] = 'closed'
            tr['exit_price'] = hit_price
            tr['exit_time'] = str(idx)
            tr['outcome'] = outcome
            tr['profit_per_share'] = profit_per_share
            tr['profit_pct'] = profit_pct
            st.session_state['active_trade'] = tr
            # send telegram
            if outcome == 'TP':
                msg = (f"🎯 TARGET HIT for `{tr['symbol']}`\nEntry: {entry_price:.2f}\nTarget: {tr['target']:.2f}\n"
                       f"Exit price: {hit_price:.2f}\nProfit per share: {profit_per_share:.2f} ( {profit_pct:.2f}% )\nTime: {tr['exit_time']}")
            else:
                msg = (f"❌ STOP LOSS HIT for `{tr['symbol']}`\nEntry: {entry_price:.2f}\nSL: {tr['stop_loss']:.2f}\n"
                       f"Exit price: {hit_price:.2f}\nLoss per share: {profit_per_share:.2f} ( {profit_pct:.2f}% )\nTime: {tr['exit_time']}")
            send_telegram(msg)
            st.success(f"Trade closed: {outcome} — P/L per share {profit_per_share:.2f} ({profit_pct:.2f}%)")

# main loop runner (non-blocking by refresh)
if st.session_state['monitoring'] and st.session_state.get('selected_stock'):
    st.markdown("**Monitoring active — app will poll data periodically.**")
    # single run and then use st.experimental_rerun with sleep to simulate loop
    monitor_once(st.session_state['selected_stock'])
    # Sleep then rerun for continuous behavior (this keeps Streamlit responsive)
    time.sleep(refresh_secs)
    st.experimental_rerun()
else:
    st.markdown("Monitoring stopped. Click *Start monitoring* to run.")
