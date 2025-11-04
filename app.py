import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import ta
import requests
import time
from datetime import datetime

# ================== SETTINGS ==================
INTERVAL = "5m"       # Intraday time frame
MIN_CONFIRM = 3       # Minimum bullish/bearish confirmations for signal
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]

# ================== TELEGRAM FUNCTION ==================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        params = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.get(url, params=params)
    except Exception as e:
        st.warning(f"⚠️ Telegram Error: {e}")

# ================== TEST TELEGRAM ==================
def test_telegram_connection():
    test_msg = f"✅ Telegram connected successfully at {datetime.now().strftime('%H:%M:%S')}"
    send_telegram(test_msg)
    st.success("Test message sent to Telegram!")

# ================== SAFE FETCH ==================
def safe_fetch(symbol, period="2d", interval=INTERVAL):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, threads=False)
        if df is None or df.empty:
            return None

        # Flatten multilevel columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols].dropna().reset_index(drop=True)

        for c in cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df = df.dropna().reset_index(drop=True)
        if df.empty or "Close" not in df.columns:
            return None

        return df
    except Exception as e:
        st.warning(f"⚠️ Error fetching {symbol}: {e}")
        return None

# ================== ANALYZE FUNCTION ==================
def analyze_df(df):
    try:
        if df is None or df.empty:
            return None

        # Ensure 1D float Series
        close = pd.Series(df["Close"].astype(float).values, dtype=float)
        if close.isna().all():
            return None

        # Indicators
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

        # Logic for signals
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

        bullish_tokens = sum(1 for s in signals if "Bullish" in s or "Above" in s or "Volume" in s)
        bearish_tokens = sum(1 for s in signals if "Bearish" in s or "Below" in s)

        action = "HOLD"
        if bullish_tokens >= MIN_CONFIRM:
            action = "BUY"
        elif bearish_tokens >= MIN_CONFIRM:
            action = "SELL"

        price = float(last["Close"])
        if action == "BUY":
            sl = float(min(last["Low"], df.iloc[-2]["Low"]))
            risk = price - sl if price > sl else price * 0.005
            target = price + max(0.012 * price, 1.5 * risk)
        elif action == "SELL":
            sl = float(max(last["High"], df.iloc[-2]["High"]))
            risk = sl - price if sl > price else price * 0.005
            target = price - max(0.012 * price, 1.5 * risk)
        else:
            sl = None
            target = None

        return {
            "action": action,
            "price": round(price, 2),
            "sl": round(sl, 2) if sl else None,
            "target": round(target, 2) if target else None,
            "signals": signals,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    except Exception as e:
        st.warning(f"⚠️ Error analyzing data: {e}")
        return None

# ================== ALERT CHECK ==================
def check_hit_conditions(stock_data, live_price):
    if stock_data["action"] == "BUY":
        if live_price >= stock_data["target"]:
            return "🎯 Target Hit!"
        elif live_price <= stock_data["sl"]:
            return "❌ Stop Loss Hit!"
    elif stock_data["action"] == "SELL":
        if live_price <= stock_data["target"]:
            return "🎯 Target Hit!"
        elif live_price >= stock_data["sl"]:
            return "❌ Stop Loss Hit!"
    return None

# ================== STREAMLIT UI ==================
st.title("📊 Intraday AI System V3")
st.caption("Real-time Intraday Analysis using EMA, RSI, MACD, VWAP + Telegram Alerts")

stocks_input = st.text_area("Enter Stock Symbols (comma separated):", "RELIANCE.NS, TCS.NS, HDFCBANK.NS")
stocks = [s.strip() for s in stocks_input.split(",") if s.strip()]

if st.button("🔔 Test Telegram Connection"):
    test_telegram_connection()

if st.button("🚀 Run Intraday Scan"):
    results = []
    st.info("Analyzing stocks... please wait.")

    for s in stocks:
        df = safe_fetch(s)
        if df is not None:
            result = analyze_df(df)
            if result and result["action"] in ["BUY", "SELL"]:
                results.append({
                    "symbol": s,
                    **result
                })
                msg = (
                    f"📈 {result['action']} Alert for {s}\n"
                    f"💰 Price: {result['price']}\n"
                    f"🎯 Target: {result['target']} | 🛑 SL: {result['sl']}\n"
                    f"📊 Reason: {', '.join(result['signals'])}\n"
                    f"🕒 Time: {result['time']}"
                )
                send_telegram(msg)
                time.sleep(1)

    if results:
        df_results = pd.DataFrame(results)
        st.dataframe(df_results)

        # Target/SL live check
        st.write("⏱ Checking for Target/SL hit...")
        for stock in results:
            live_df = yf.download(stock["symbol"], period="1d", interval=INTERVAL, progress=False)
            if not live_df.empty:
                live_price = round(live_df["Close"].iloc[-1], 2)
                status = check_hit_conditions(stock, live_price)
                if status:
                    hit_msg = (
                        f"{status}\n"
                        f"{stock['symbol']} | Live: {live_price} | Entry: {stock['price']}\n"
                        f"🎯 Target: {stock['target']} | 🛑 SL: {stock['sl']}\n"
                        f"🕒 {datetime.now().strftime('%H:%M:%S')}"
                    )
                    send_telegram(hit_msg)
                    st.warning(hit_msg)
            time.sleep(1)
    else:
        st.warning("No strong intraday signals found.")