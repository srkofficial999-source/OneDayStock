# app.py
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import threading
import time
import requests
from datetime import datetime, time as dtime, timedelta

st.set_page_config(page_title="Intraday Auto Scanner", layout="wide")

# ---------------- CONFIG ----------------
# Default watchlist (you can edit in UI)
DEFAULT_WATCH = "RELIANCE.NS, TCS.NS, HDFCBANK.NS, INFY.NS, ICICIBANK.NS, LT.NS, SBI.NS"

INTERVAL = "5m"               # yfinance interval
SCAN_INTERVAL = 300           # seconds between scans (5 minutes)
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
MIN_CONFIRM = 3               # how many bullish tokens to treat as BUY
TOP_N_BY_VOLUME = 10         # if auto-select enabled, choose top N by volume

# Secrets (set these in Streamlit Secrets)
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]

# ---------------- TELEGRAM ----------------
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        st.warning("Telegram secrets missing — cannot send alerts.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        st.error(f"Telegram send failed: {e}")

def test_telegram():
    tmsg = f"✅ Telegram test from Intraday Auto Scanner at {datetime.now().strftime('%H:%M:%S')}"
    send_telegram(tmsg)
    st.success("Test message sent. Check Telegram.")

# ---------------- INDICATOR & ANALYSIS ----------------
def safe_fetch(symbol, period="2d", interval=INTERVAL):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, threads=False)
        if df is None or df.empty:
            return None
        df = df[["Open","High","Low","Close","Volume"]].dropna().reset_index(drop=True)
        for c in ["Open","High","Low","Close","Volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna().reset_index(drop=True)
        if df.empty:
            return None
        return df
    except Exception as e:
        st.error(f"yfinance fetch error for {symbol}: {e}")
        return None

def analyze_df(df):
    # expects cleaned df
    close = pd.Series(df["Close"].values, dtype=float)
    df["EMA9"] = ta.trend.ema_indicator(close, window=9)
    df["EMA20"] = ta.trend.ema_indicator(close, window=20)
    df["RSI"] = ta.momentum.rsi(close, window=14)
    df["MACD_diff"] = ta.trend.macd_diff(close)
    df["VWAP"] = (df["Close"] * df["Volume"]).rolling(window=30, min_periods=1).sum() / df["Volume"].rolling(window=30, min_periods=1).sum()
    df["vol_avg20"] = df["Volume"].rolling(window=20, min_periods=1).mean()
    df["vol_spike"] = df["Volume"] > (df["vol_avg20"] * 1.5)
    df = df.dropna().reset_index(drop=True)
    if df.empty:
        return None
    last = df.iloc[-1]
    signals = []
    if last["EMA9"] > last["EMA20"]:
        signals.append("EMA Bullish")
    else:
        signals.append("EMA Bearish")
    if last["RSI"] > 55:
        signals.append("RSI Bullish")
    elif last["RSI"] < 45:
        signals.append("RSI Bearish")
    if last["MACD_diff"] > 0:
        signals.append("MACD Bullish")
    else:
        signals.append("MACD Bearish")
    if last["Close"] > last["VWAP"]:
        signals.append("Above VWAP")
    else:
        signals.append("Below VWAP")
    if last["vol_spike"]:
        signals.append("Volume Spike")
    # decide
    bullish_tokens = sum(1 for tok in signals if "Bullish" in tok or "Above VWAP" in tok or "Volume Spike" in tok)
    bearish_tokens = sum(1 for tok in signals if "Bearish" in tok or "Below VWAP" in tok)
    action = "HOLD"
    if bullish_tokens >= MIN_CONFIRM:
        action = "BUY"
    elif bearish_tokens >= MIN_CONFIRM:
        action = "SELL"
    # compute sl/target
    price = float(last["Close"])
    if action == "BUY":
        sl = float(min(last["Low"], df.iloc[-2]["Low"]))
        risk = price - sl if price > sl else price * 0.005
        target = price + max(0.012*price, 1.5*risk)
    elif action == "SELL":
        sl = float(max(last["High"], df.iloc[-2]["High"]))
        risk = sl - price if sl > price else price * 0.005
        target = price - max(0.012*price, 1.5*risk)
    else:
        sl = None; target = None
    return {
        "action": action,
        "price": round(price,2),
        "sl": round(sl,2) if sl else None,
        "target": round(target,2) if target else None,
        "signals": signals,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

# ---------------- BACKGROUND SCANNER ----------------
class AutoScanner(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self.active_trades = {}  # symbol -> trade dict
        self.history = []        # closed trades
        self.log_lines = []      # for UI
    def stop(self):
        self._stop.set()
    def stopped(self):
        return self._stop.is_set()
    def log(self, txt):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {txt}"
        self.log_lines.insert(0, line)
        if len(self.log_lines) > 200:
            self.log_lines.pop()
    def run(self):
        self.log("AutoScanner started.")
        while not self.stopped():
            now = datetime.now().time()
            # only run in market hours
            if MARKET_OPEN <= now <= MARKET_CLOSE:
                try:
                    # Determine stock list: either user-specified or auto-top-by-volume
                    raw = st.session_state.get("watchlist_raw", DEFAULT_WATCH)
                    full_list = [s.strip() for s in raw.split(",") if s.strip()]
                    # If user selected 'auto_top' use top by volume
                    if st.session_state.get("auto_top", False):
                        # fetch short snapshot for all and compute today's vol
                        vols = {}
                        for sym in full_list:
                            df = safe_fetch(sym, period="1d", interval=INTERVAL)
                            if df is not None and not df.empty:
                                vols[sym] = int(df["Volume"].sum())
                        sorted_syms = sorted(vols.items(), key=lambda x: x[1], reverse=True)
                        symbols = [x[0] for x in sorted_syms[:TOP_N_BY_VOLUME]]
                        self.log(f"Auto-top selected: {', '.join(symbols)}")
                    else:
                        symbols = full_list

                    for sym in symbols:
                        if self.stopped():
                            break
                        df = safe_fetch(sym, period="1d", interval=INTERVAL)
                        if df is None:
                            continue
                        out = analyze_df = analyze_df = None
                        try:
                            out = analyze_df = analyze_df = None
                            out = analyze_df(df) if df is not None else None
                        except Exception as e:
                            # fallback: call analyze_df safely (keeping consistent name)
                            try:
                                out = analyze_df(df)
                            except Exception as e2:
                                self.log(f"Analyze error {sym}: {e2}")
                                continue
                        if out and out["action"] != "HOLD":
                            # if new trade not already active, add
                            if sym not in self.active_trades:
                                trade = {
                                    "symbol": sym,
                                    "action": out["action"],
                                    "entry": out["price"],
                                    "sl": out["sl"],
                                    "target": out["target"],
                                    "signals": out["signals"],
                                    "entry_time": out["time"]
                                }
                                self.active_trades[sym] = trade
                                msg = (f"🕒 {trade['entry_time']}\n📈 {sym} — {trade['action']}\n💰 Entry: ₹{trade['entry']}\n🎯 Target: ₹{trade['target']} | 🛑 SL: ₹{trade['sl']}\n🧠 Reason: {', '.join(trade['signals'][:3])}")
                                send_telegram(msg)
                                self.log(f"Signal {sym} {trade['action']} @ {trade['entry']}")
                        # check active trades for hits
                        if sym in self.active_trades:
                            # fetch latest price
                            live = safe_fetch(sym, period="1d", interval=INTERVAL)
                            if live is None or live.empty:
                                continue
                            live_price = float(live["Close"].iloc[-1])
                            status = None
                            t = self.active_trades[sym]
                            if t["action"] == "BUY":
                                if live_price >= t["target"]:
                                    status = "TARGET"
                                elif live_price <= t["sl"]:
                                    status = "SL"
                            elif t["action"] == "SELL":
                                if live_price <= t["target"]:
                                    status = "TARGET"
                                elif live_price >= t["sl"]:
                                    status = "SL"
                            if status:
                                exit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                pnl = (t["target"] - t["entry"]) / t["entry"] * 100 if status == "TARGET" and t["action"]=="BUY" else \
                                      (t["entry"] - t["target"]) / t["entry"] * 100 if status=="TARGET" and t["action"]=="SELL" else \
                                      (t["sl"] - t["entry"])/t["entry"]*100 if status=="SL" and t["action"]=="BUY" else \
                                      (t["entry"] - t["sl"])/t["entry"]*100
                                res_msg = (f"{'✅' if status=='TARGET' else '❌'} {sym} — { 'Target Hit' if status=='TARGET' else 'Stop Loss Hit' }\n"
                                           f"⏱ Entry: ₹{t['entry']} → Exit: ₹{t['target'] if status=='TARGET' else t['sl']}\n"
                                           f"🕒 {exit_time}\n"
                                           f"🔢 P/L pct (est): {round(pnl,2)}%")
                                send_telegram(res_msg)
                                self.log(f"{sym} {status} at {exit_time}")
                                # move to history
                                hist = {**t, "exit": t["target"] if status=="TARGET" else t["sl"], "exit_time": exit_time, "result": status}
                                self.history.insert(0, hist)
                                del self.active_trades[sym]
                        time.sleep(1)  # small pause between symbols to avoid hammering yfinance
                except Exception as e_main:
                    self.log(f"Scanner error: {e_main}")
            else:
                self.log("Outside market hours; sleeping until market opens.")
            # loop sleep
            for _ in range(max(1, int(SCAN_INTERVAL))):
                if self.stopped():
                    break
                time.sleep(1)
        self.log("AutoScanner stopped.")

# ---------------- STREAMLIT UI & Controls ----------------
st.title("🔁 Intraday Auto Scanner (Streamlit Background)")

col1, col2 = st.columns([2,1])

with col1:
    st.markdown("### Watchlist / Auto-top")
    wl = st.text_area("Symbols (comma separated) — or use Auto Top by Volume:", value=DEFAULT_WATCH, height=110)
    st.session_state["watchlist_raw"] = wl
    auto_top = st.checkbox("Auto-select top volume stocks from this list", value=False)
    st.session_state["auto_top"] = auto_top
    st.markdown("### Run Config")
    interval_in = st.number_input("Scan interval (seconds)", value=SCAN_INTERVAL, step=60)
    st.session_state["scan_interval"] = interval_in

with col2:
    st.markdown("### Telegram")
    if st.button("Test Telegram"):
        test_telegram()
    st.write("Make sure TELEGRAM_TOKEN & TELEGRAM_CHAT_ID are set in Streamlit Secrets.")

# controls to start/stop background scanner
if "scanner" not in st.session_state:
    st.session_state.scanner = None

start_button = st.button("▶️ Start Live Scanner")
stop_button = st.button("⏹ Stop Live Scanner")

if start_button and (st.session_state.scanner is None or not st.session_state.scanner.is_alive()):
    # create & start
    SCAN_INTERVAL = st.session_state.get("scan_interval", SCAN_INTERVAL)
    scanner = AutoScanner()
    st.session_state.scanner = scanner
    scanner.start()
    st.success("Live scanner started. Keep this browser/tab open (Streamlit Cloud may sleep; see notes).")

if stop_button and st.session_state.scanner is not None:
    st.session_state.scanner.stop()
    st.session_state.scanner = None
    st.success("Live scanner stopped.")

# show logs, active trades, history
if "scanner" in st.session_state and st.session_state.scanner is not None:
    scanner = st.session_state.scanner
    st.markdown("## Logs (latest first)")
    for line in scanner.log_lines[:50]:
        st.write(line)
    st.markdown("## Active Trades")
    if scanner.active_trades:
        df_act = pd.DataFrame(list(scanner.active_trades.values()))
        st.dataframe(df_act)
    else:
        st.write("No active trades")
    st.markdown("## Closed History")
    if scanner.history:
        st.dataframe(pd.DataFrame(scanner.history))
    else:
        st.write("No closed trades yet")
else:
    st.info("Scanner is not running. Click ▶️ Start Live Scanner to begin.")

# ---------------- DEPLOYMENT NOTES ----------------
st.markdown("""
**Deployment notes**
- Streamlit Community Cloud (free) may put the app to sleep after inactivity — background scanner stops when app sleeps.
- To run fully live all day, either:
  1. Use Streamlit's paid 'always-on' feature for this app, or
  2. Deploy to an always-on host (small VPS / Render / Railway / Replit with always-on) and run this script there, or
  3. Use a keep-alive / ping service **and** paid Streamlit plan (free plan usually not enough).
""")