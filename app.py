import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import time
import requests
from datetime import datetime

# ==============================
# 🔹 CONFIGURATION
# ==============================
st.set_page_config(page_title="Intraday AI System V2.1", layout="wide")

STOCKS = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS"]
INTERVAL = "5m"
REFRESH_MINUTES = 5

# Telegram credentials (Streamlit Secrets)
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]

# ==============================
# 🔹 TELEGRAM FUNCTIONS
# ==============================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        r = requests.post(url, data=data)
        if r.status_code != 200:
            st.warning(f"⚠️ Telegram send failed: {r.text}")
    except Exception as e:
        st.error(f"❌ Telegram error: {e}")

def test_telegram_connection():
    test_msg = f"✅ Telegram Connected Successfully!\n🕒 {datetime.now().strftime('%H:%M:%S')}"
    send_telegram(test_msg)
    st.success("Telegram test message sent — check your Telegram app 📱")

# ==============================
# 🔹 SIGNAL ANALYSIS FUNCTION
# ==============================
def analyze_stock(symbol):
    data = yf.download(symbol, period="1d", interval=INTERVAL, progress=False)
    if len(data) < 30:
        return None

    data["EMA9"] = ta.trend.ema_indicator(data["Close"], window=9)
    data["EMA20"] = ta.trend.ema_indicator(data["Close"], window=20)
    data["RSI"] = ta.momentum.rsi(data["Close"], window=14)
    data["MACD"] = ta.trend.macd_diff(data["Close"])
    data["VWAP"] = ta.volume.volume_weighted_average_price(
        high=data["High"], low=data["Low"], close=data["Close"], volume=data["Volume"]
    )

    latest = data.iloc[-1]
    signals = []

    if latest["EMA9"] > latest["EMA20"]:
        signals.append("EMA Bullish")
    if latest["RSI"] > 55:
        signals.append("RSI Strong")
    if latest["MACD"] > 0:
        signals.append("MACD Bullish")
    if latest["Close"] > latest["VWAP"]:
        signals.append("Above VWAP")
    if latest["Volume"] > data["Volume"].mean() * 1.5:
        signals.append("Volume Spike")

    if len(signals) >= 3:
        signal_type = "BUY"
        sl = round(latest["Close"] * 0.985, 2)
        target = round(latest["Close"] * 1.015, 2)
    elif len(signals) <= 2:
        signal_type = "SELL"
        sl = round(latest["Close"] * 1.015, 2)
        target = round(latest["Close"] * 0.985, 2)
    else:
        return None

    reason = ", ".join(signals[:3])
    return {
        "symbol": symbol,
        "price": round(latest["Close"], 2),
        "signal": signal_type,
        "sl": sl,
        "target": target,
        "reason": reason,
        "time": datetime.now().strftime("%H:%M:%S")
    }

# ==============================
# 🔹 STREAMLIT UI
# ==============================
st.title("📈 Intraday AI System V2.1")
st.caption("Real-time Intraday Signals with AI Indicators & Telegram Alerts")

if st.button("📡 Test Telegram Connection"):
    test_telegram_connection()

st.divider()

st.subheader("🔍 Live Signal Analysis")

placeholder = st.empty()
active_trades = {}

# ==============================
# 🔹 MAIN LOOP (Run manually inside Streamlit)
# ==============================
run_app = st.checkbox("✅ Run Live Analysis (refresh every 5 min)")

while run_app:
    table_data = []
    for s in STOCKS:
        result = analyze_stock(s)
        if result:
            table_data.append(result)

            if s not in active_trades:
                msg = (
                    f"🕒 {result['time']}\n"
                    f"📈 {result['symbol']} — {result['signal']}\n"
                    f"💰 Price: ₹{result['price']}\n"
                    f"🎯 Target: ₹{result['target']} | 🛑 SL: ₹{result['sl']}\n"
                    f"🧠 Reason: {result['reason']}"
                )
                send_telegram(msg)
                active_trades[s] = result

        # --- Check for Target/SL hit ---
        elif s in active_trades:
            live_data = yf.download(s, period="1d", interval=INTERVAL, progress=False)
            last_price = live_data["Close"].iloc[-1]
            trade = active_trades[s]

            if trade["signal"] == "BUY":
                if last_price >= trade["target"]:
                    msg = (
                        f"✅ {s} — Target Hit 🎯\n"
                        f"⏱ Entry: ₹{trade['price']} → Exit: ₹{trade['target']} (+1.5%)\n"
                        f"🕒 {datetime.now().strftime('%H:%M:%S')}"
                    )
                    send_telegram(msg)
                    del active_trades[s]

                elif last_price <= trade["sl"]:
                    msg = (
                        f"❌ {s} — Stop Loss Hit 🛑\n"
                        f"⏱ Entry: ₹{trade['price']} → Exit: ₹{trade['sl']} (-1.5%)\n"
                        f"🕒 {datetime.now().strftime('%H:%M:%S')}"
                    )
                    send_telegram(msg)
                    del active_trades[s]

            elif trade["signal"] == "SELL":
                if last_price <= trade["target"]:
                    msg = (
                        f"✅ {s} — Target Hit 🎯\n"
                        f"⏱ Entry: ₹{trade['price']} → Exit: ₹{trade['target']} (+1.5%)\n"
                        f"🕒 {datetime.now().strftime('%H:%M:%S')}"
                    )
                    send_telegram(msg)
                    del active_trades[s]

                elif last_price >= trade["sl"]:
                    msg = (
                        f"❌ {s} — Stop Loss Hit 🛑\n"
                        f"⏱ Entry: ₹{trade['price']} → Exit: ₹{trade['sl']} (-1.5%)\n"
                        f"🕒 {datetime.now().strftime('%H:%M:%S')}"
                    )
                    send_telegram(msg)
                    del active_trades[s]

    if table_data:
        df = pd.DataFrame(table_data)
        placeholder.dataframe(df)
    else:
        st.info("No clear signal yet. Waiting for next refresh...")

    time.sleep(REFRESH_MINUTES * 60)