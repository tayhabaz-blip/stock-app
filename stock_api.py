from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import math

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def clean(v):
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except:
        return None

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if hist.empty:
        return {"error": "מניה לא נמצאה"}
    info = stock.info
    closes = [clean(v) for v in hist["Close"].tolist()]
    labels = [str(d.date()) for d in hist.index]
    return {
        "ticker": ticker.upper(),
        "closes": closes,
        "labels": labels,
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
        "website": info.get("website", ""),
        "country": info.get("country", ""),
    }

WATCHLIST = [
    "AAPL","MSFT","NVDA","TSLA","META","AMZN","GOOGL","AMD",
    "NFLX","PYPL","INTC","SOFI","PLTR","RIVN","CIFR",
    "MARA","RIOT","COIN","HOOD","SNAP","UBER","SQ",
    "SHOP","TEVA","MRNA","PFE","BABA","NIO"
]

@app.get("/scan")
def scan():
    results = []
    for ticker in WATCHLIST:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="3mo")
            if hist.empty or len(hist) < 20:
                continue
            closes = [float(v) for v in hist["Close"].tolist() if v == v]
            if len(closes) < 20:
                continue
            price = closes[-1]
            ma9 = sum(closes[-9:]) / 9
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
            recent = sorted(closes[-30:])
            support = recent[int(len(recent)*0.15)]
            resist = recent[int(len(recent)*0.85)]
            if abs(price - support) / price < 0.03: signals.append("קרוב לתמיכה")
            if abs(price - resist) / price < 0.03: signals.append("קרוב להתנגדות")
            if signals:
                results.append({
                    "ticker": ticker,
                    "price": round(price, 2),
                    "rsi": round(rsi, 1),
                    "signals": signals
                })
        except:
            continue
    return {"results": results}

