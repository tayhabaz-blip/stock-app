from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from curl_cffi import requests as crequests
import yfinance as yf

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

session = crequests.Session(impersonate="chrome")

def clean(v):
    try:
        f = float(v)
        return None if f != f else f
    except:
        return None

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    try:
        stock = yf.Ticker(ticker, session=session)
        hist = stock.history(period="1y")
        if hist.empty:
            return {"error": "מניה לא נמצאה"}
        info = stock.info
        closes = [clean(v) for v in hist["Close"].tolist()]
        highs  = [clean(v) for v in hist["High"].tolist()]
        lows    = [clean(v) for v in hist["Low"].tolist()]
        volumes = [clean(v) for v in hist["Volume"].tolist()]
        labels = [str(d.date()) for d in hist.index]
        return {
            "ticker": ticker.upper(),
            "closes": closes, "highs": highs, "lows": lows, "volumes": volumes, "labels": labels,
            "name": info.get("longName", ticker),
            "description": info.get("longBusinessSummary", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": clean(info.get("marketCap")),
            "pe_ratio": clean(info.get("trailingPE")),
            "eps": clean(info.get("trailingEps")),
            "week_high": clean(info.get("fiftyTwoWeekHigh")),
            "week_low": clean(info.get("fiftyTwoWeekLow")),
            "dividend": clean(info.get("dividendYield")),
            "employees": clean(info.get("fullTimeEmployees")),
            "country": info.get("country", ""),
        }
    except Exception as e:
        return {"error": str(e)}

WATCHLIST = ["AAPL","MSFT","NVDA","TSLA","META","SOFI","PLTR","COIN","HOOD","AMD"]

@app.get("/scan")
def scan():
    results = []
    for ticker in WATCHLIST:
        try:
            stock = yf.Ticker(ticker, session=session)
            hist = stock.history(period="3mo")
            if hist.empty or len(hist) < 20:
                continue
            closes = [float(v) for v in hist["Close"].tolist()]
            price = closes[-1]
            ma9  = sum(closes[-9:])  / 9
            ma20 = sum(closes[-20:]) / 20
            gains, losses = 0, 0
            for i in range(-14, 0):
                d = closes[i] - closes[i-1]
                if d > 0: gains += d
                else: losses -= d
            rsi = 100 - 100 / (1 + gains / (losses or 0.0001))
            signals = []
            if rsi < 35: signals.append("RSI נמוך")
            if rsi > 70: signals.append("RSI גבוה")
            if ma9 > ma20: signals.append("MA9 מעל MA20")
            if signals:
                results.append({"ticker": ticker, "price": round(price, 2), "rsi": round(rsi, 1), "signals": signals})
        except:
            continue
    return {"results": results}

@app.get("/sentiment/{ticker}")
def get_sentiment(ticker: str):
    try:
        stock = yf.Ticker(ticker, session=session)
        info = stock.info
        rec = stock.recommendations
        if rec is not None and not rec.empty:
            latest = rec.tail(4)
            strong_buy  = int(latest["strongBuy"].sum())  if "strongBuy"  in latest.columns else 0
            buy         = int(latest["buy"].sum())        if "buy"        in latest.columns else 0
            hold        = int(latest["hold"].sum())       if "hold"       in latest.columns else 0
            sell        = int(latest["sell"].sum())       if "sell"       in latest.columns else 0
            strong_sell = int(latest["strongSell"].sum()) if "strongSell" in latest.columns else 0
        else:
            strong_buy = buy = hold = sell = strong_sell = 0
        total = strong_buy + buy + hold + sell + strong_sell
        short_pct = info.get("shortPercentOfFloat")
        return {
            "ticker": ticker.upper(),
            "bull_pct": round((strong_buy + buy) / total * 100, 1) if total else None,
            "bear_pct": round((sell + strong_sell) / total * 100, 1) if total else None,
            "neutral_pct": round(hold / total * 100, 1) if total else None,
            "votes": {"strong_buy": strong_buy, "buy": buy, "hold": hold, "sell": sell, "strong_sell": strong_sell, "total": total},
            "short_pct_float": round(short_pct * 100, 2) if short_pct else None,
            "recommendation": info.get("recommendationKey", ""),
        }
    except Exception as e:
        return {"error": str(e)}
