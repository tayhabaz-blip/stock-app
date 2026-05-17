from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if hist.empty:
        return {"error": "מניה לא נמצאה"}
    info = stock.info
    closes = [round(v, 2) for v in hist["Close"].tolist()]
    labels = [str(d.date()) for d in hist.index]
    return {
        "ticker": ticker.upper(),
        "closes": closes,
        "labels": labels,
        "name": info.get("longName", ticker),
        "description": info.get("longBusinessSummary", ""),
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "market_cap": info.get("marketCap", None),
        "pe_ratio": info.get("trailingPE", None),
        "eps": info.get("trailingEps", None),
        "week_high": info.get("fiftyTwoWeekHigh", None),
        "week_low": info.get("fiftyTwoWeekLow", None),
        "dividend": info.get("dividendYield", None),
        "employees": info.get("fullTimeEmployees", None),
        "website": info.get("website", ""),
        "country": info.get("country", ""),
    }
