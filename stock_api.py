from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

AV_KEY = "J2TC8FW0KVOA52L7"
AV_URL = "https://www.alphavantage.co/query"

def get_overview(ticker):
    r = requests.get(AV_URL, params={"function": "OVERVIEW", "symbol": ticker, "apikey": AV_KEY})
    return r.json()

def get_daily(ticker):
    r = requests.get(AV_URL, params={"function": "TIME_SERIES_DAILY", "symbol": ticker, "outputsize": "compact", "apikey": AV_KEY})
    return r.json()

def clean(v):
    try:
        f = float(v)
        return None if f != f else f
    except:
        return None

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    try:
        overview = get_overview(ticker)
        daily = get_daily(ticker)
        if "Time Series (Daily)" not in daily:
            return {"error": "מניה לא נמצאה"}
        ts = daily["Time Series (Daily)"]
        dates = sorted(ts.keys())[-252:]
        closes = [float(ts[d]["4. close"]) for d in dates]
        highs  = [float(ts[d]["2. high"])  for d in dates]
        lows   = [float(ts[d]["3. low"])   for d in dates]
        labels = dates
        return {
            "ticker": ticker.upper(),
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "labels": labels,
            "name": overview.get("Name", ticker),
            "description": overview.get("Description", ""),
            "sector": overview.get("Sector", ""),
            "industry": overview.get("Industry", ""),
            "market_cap": clean(overview.get("MarketCapitalization")),
            "pe_ratio": clean(overview.get("PERatio")),
            "eps": clean(overview.get("EPS")),
            "week_high": clean(overview.get("52WeekHigh")),
            "week_low": clean(overview.get("52WeekLow")),
            "dividend": clean(overview.get("DividendYield")),
            "employees": clean(overview.get("FullTimeEmployees")),
            "country": overview.get("Country", ""),
        }
    except Exception as e:
        return {"error": str(e)}

WATCHLIST = ["AAPL","MSFT","NVDA","TSLA","META","SOFI","PLTR","COIN","HOOD","AMD"]

@app.get("/scan")
def scan():
    results = []
    for ticker in WATCHLIST[:5]:
        try:
            daily = get_daily(ticker)
            if "Time Series (Daily)" not in daily:
                continue
            ts = daily["Time Series (Daily)"]
            dates = sorted(ts.keys())[-30:]
            closes = [float(ts[d]["4. close"]) for d in dates]
            if len(closes) < 20:
                continue
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
        import yfinance as yf
        stock = yf.Ticker(ticker)
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
        bull_pct    = round((strong_buy + buy) / total * 100, 1) if total else None
        bear_pct    = round((sell + strong_sell) / total * 100, 1) if total else None
        neutral_pct = round(hold / total * 100, 1) if total else None
        short_pct   = info.get("shortPercentOfFloat")
        return {
            "ticker": ticker.upper(),
            "bull_pct": bull_pct, "bear_pct": bear_pct, "neutral_pct": neutral_pct,
            "votes": {"strong_buy": strong_buy, "buy": buy, "hold": hold, "sell": sell, "strong_sell": strong_sell, "total": total},
            "short_pct_float": round(short_pct * 100, 2) if short_pct else None,
            "recommendation": info.get("recommendationKey", ""),
        }
    except Exception as e:
        return {"error": str(e)}
