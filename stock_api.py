from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FH_KEY = "d8uha31r01qinhui0algd8uha31r01qinhui0am0"
FH_URL = "https://finnhub.io/api/v1"

def fh(endpoint, params):
    params["token"] = FH_KEY
    r = requests.get(FH_URL + endpoint, params=params, timeout=10)
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
        candles = fh("/stock/candle", {"symbol": ticker, "resolution": "D", "from": 1700000000, "to": 9999999999})
        if candles.get("s") != "ok":
            return {"error": "מניה לא נמצאה"}
        closes = candles["c"]
        highs  = candles["h"]
        lows   = candles["l"]
        import datetime
        labels = [str(datetime.date.fromtimestamp(t)) for t in candles["t"]]
        profile = fh("/stock/profile2", {"symbol": ticker})
        metric  = fh("/stock/metric", {"symbol": ticker, "metric": "all"})
        m = metric.get("metric", {})
        return {
            "ticker": ticker.upper(),
            "closes": closes, "highs": highs, "lows": lows, "labels": labels,
            "name": profile.get("name", ticker),
            "description": "",
            "sector": profile.get("finnhubIndustry", ""),
            "industry": profile.get("finnhubIndustry", ""),
            "market_cap": clean(profile.get("marketCapitalization")),
            "pe_ratio": clean(m.get("peNormalizedAnnual")),
            "eps": clean(m.get("epsNormalizedAnnual")),
            "week_high": clean(m.get("52WeekHigh")),
            "week_low": clean(m.get("52WeekLow")),
            "dividend": clean(m.get("dividendYieldIndicatedAnnual")),
            "employees": clean(profile.get("employeeTotal")),
            "country": profile.get("country", ""),
        }
    except Exception as e:
        return {"error": str(e)}

WATCHLIST = ["AAPL","MSFT","NVDA","TSLA","META","SOFI","PLTR","COIN","HOOD","AMD"]

@app.get("/scan")
def scan():
    results = []
    for ticker in WATCHLIST:
        try:
            candles = fh("/stock/candle", {"symbol": ticker, "resolution": "D", "from": 1700000000, "to": 9999999999})
            if candles.get("s") != "ok":
                continue
            closes = candles["c"][-30:]
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
        rec = fh("/stock/recommendation", {"symbol": ticker})
        if not rec:
            return {"error": "אין נתונים"}
        latest = rec[0]
        strong_buy  = latest.get("strongBuy", 0)
        buy         = latest.get("buy", 0)
        hold        = latest.get("hold", 0)
        sell        = latest.get("sell", 0)
        strong_sell = latest.get("strongSell", 0)
        total = strong_buy + buy + hold + sell + strong_sell
        return {
            "ticker": ticker.upper(),
            "bull_pct": round((strong_buy + buy) / total * 100, 1) if total else None,
            "bear_pct": round((sell + strong_sell) / total * 100, 1) if total else None,
            "neutral_pct": round(hold / total * 100, 1) if total else None,
            "votes": {"strong_buy": strong_buy, "buy": buy, "hold": hold, "sell": sell, "strong_sell": strong_sell, "total": total},
            "short_pct_float": None,
            "recommendation": "",
        }
    except Exception as e:
        return {"error": str(e)}