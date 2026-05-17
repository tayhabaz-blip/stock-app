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
