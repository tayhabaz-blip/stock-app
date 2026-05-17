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
    closes = [round(v, 2) for v in hist["Close"].tolist()]
    labels = [str(d.date()) for d in hist.index]
    return {"ticker": ticker.upper(), "closes": closes, "labels": labels}


