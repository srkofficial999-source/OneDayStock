import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import ta
import requests
from datetime import datetime
import time

# ================== SETTINGS ==================
INTERVAL = "5m"  # Intraday interval
CHECK_EVERY = 300  # seconds (5 mins)

# For Streamlit Cloud, put these in "Secrets" section
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]

# ================== TELEGRAM ALERT ==================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        params = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.get(url, params=params)
    except Exception as e:
        st.warning(f"Telegram Error: {e}")

# ================== TEST TELEGRAM ==================
def test_telegram_connection():
    test_msg = f"✅ Telegram connected successfully at {datetime.now().strftime('%H:%M:%S')}"
    send_telegram(test_msg)
    st.success("Test message sent to Telegram!")

# ================== ANALYZE STOCK ==================
def analyze_stock(symbol):
    try:
        data = yf.download(symbol, period="1d", interval=INTERVAL, progress=False)
        if data is None or len(data) < 30:
            return None

        # ✅ Clean data properly
        data = data[["Open", "High", "Low", "Close", "Volume"]].copy()
        data = data.dropna().reset_index(drop=True)

        for col in ["Open", "High", "Low", "Close", "Volume"]:
            data[col] = pd.to_numeric(data[col], errors="coerce")
        data = data.dropna()

        if data.empty:
            return None

        close_series = pd.Series(data["Close"].values, dtype=float)

        # ✅ Calculate Indicators
        data["EMA9"] = ta.trend.ema_indicator(close_series, window=9)
        data["EMA20"] = ta.trend.ema_indicator(close_series, window=20)
        data["RSI"] = ta.momentum.rsi(close_series, window=14)
        data["MACD"] = ta.trend.macd_diff(close_series)
        data["VWAP"] = ta.volume.volume_weighted_average_price(
            high=data["High"],
            low=data["Low"],
            close=data["Close"],
            volume=data["Volume"]
        )

        data = data.dropna().reset_index(drop=True)
        if data.empty:
            return None

        latest = data.iloc[-1]
        signals = []

        # === LOGIC ===
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

        return {
            "symbol": symbol,
            "price": round(latest["Close"], 2),
            "signal": signal_type,
            "sl": sl,
            "target": target,
            "reason": ", ".join(signals[:3]),
            "time": datetime.now().strftime("%H:%M:%S")
        }

    except Exception as e:
        st.error(f"⚠️ Error analyzing {symbol}: {e}")
        send_telegram(f"⚠️ Error analyzing {symbol}: {e}")
        return None

# ================== ALERT ON TARGET / SL ==================
def check_hit_conditions(stock_data, live_price):
    if stock_data["signal"] == "BUY":
        if live_price >= stock_data["target"]:
            return "🎯 Target Hit!"
        elif live_price <= stock_data["sl"]:
            return "❌ Stop Loss Hit!"
    elif stock_data["signal"] == "SELL":
        if live_price <= stock_data["target"]:
            return "🎯 Target Hit!"
        elif live_price >= stock_data["sl"]:
            return "❌ Stop Loss Hit!"
    return None

# ================== STREAMLIT UI ==================
st.title("📊 Intraday Stock AI System V2.3")
st.caption("Powered by YFinance + Telegram + EMA/RSI/MACD/VWAP logic")

stocks_input = st.text_area("Enter Stock Symbols (comma separated):", "RELIANCE.NS, TCS.NS, HDFCBANK.NS")
stocks = [s.strip() for s in stocks_input.split(",") if s.strip()]

if st.button("🔔 Test Telegram Connection"):
    test_telegram_connection()

if st.button("🚀 Run Intraday Scan"):
    results = []
    st.info("Analyzing... Please wait 1–2 minutes.")

    for s in stocks:
        result = analyze_stock(s)
        if result:
            results.append(result)
            msg = (
                f"📈 {result['signal']} Alert for {result['symbol']}\n"
                f"💰 Price: {result['price']}\n"
                f"🎯 Target: {result['target']} | 🛑 SL: {result['sl']}\n"
                f"📊 Reason: {result['reason']}\n"
                f"🕒 Time: {result['time']}"
            )
            send_telegram(msg)
            time.sleep(1)

    if results:
        df = pd.DataFrame(results)
        st.dataframe(df)

        # === Monitor live price to detect hit ===
        st.write("⏱ Checking live prices for Target/SL hit...")
        for stock in results:
            live_data = yf.download(stock["symbol"], period="1d", interval=INTERVAL, progress=False)
            if not live_data.empty:
                live_price = round(live_data["Close"].iloc[-1], 2)
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