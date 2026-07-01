import os
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from curl_cffi import requests as crequests
import yfinance as yf

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

session = crequests.Session(impersonate="chrome")

# ── מפתחות מגיעים ממשתני סביבה ב-Render, לא מהקוד ──
GROQ_KEY = os.environ.get("GROQ_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")

# ── מטמון פשוט בזיכרון כדי לא להעמיס על Yahoo ──
_cache = {}


def cache_get(key, ttl):
    item = _cache.get(key)
    if item and (time.time() - item[0]) < ttl:
        return item[1]
    return None


def cache_set(key, val):
    _cache[key] = (time.time(), val)
    return val


def clean(v):
    try:
        f = float(v)
        return None if f != f else f
    except Exception:
        return None


@app.get("/")
def root():
    return {"status": "ok", "service": "StockIQ API"}


# ── נתוני מניה מלאים (מטמון 5 דקות) ──
@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    key = "stock:" + ticker.upper()
    cached = cache_get(key, 300)
    if cached:
        return cached
    try:
        stock = yf.Ticker(ticker, session=session)
        hist = stock.history(period="1y")
        if hist.empty:
            return {"error": "מניה לא נמצאה"}
        info = stock.info
        closes = [clean(v) for v in hist["Close"].tolist()]
        highs = [clean(v) for v in hist["High"].tolist()]
        lows = [clean(v) for v in hist["Low"].tolist()]
        volumes = [clean(v) for v in hist["Volume"].tolist()]
        labels = [str(d.date()) for d in hist.index]
        last_price = next((c for c in reversed(closes) if c is not None), None)

        # ── דיבידנד מחושב נכון: סכום שנתי / מחיר (ולא הכפלה עיוורת ב-100) ──
        div_rate = clean(info.get("dividendRate"))
        if div_rate and last_price:
            dividend_pct = round(div_rate / last_price * 100, 2)
        else:
            dy = clean(info.get("dividendYield"))
            if dy:
                # yfinance מחזיר לפעמים אחוז ולפעמים שבר — מנרמלים
                dividend_pct = round(dy if dy >= 1 else dy * 100, 2)
            else:
                dividend_pct = None

        result = {
            "ticker": ticker.upper(),
            "closes": closes, "highs": highs, "lows": lows,
            "volumes": volumes, "labels": labels,
            "name": info.get("longName", ticker),
            "description": info.get("longBusinessSummary", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": clean(info.get("marketCap")),
            "pe_ratio": clean(info.get("trailingPE")),
            "eps": clean(info.get("trailingEps")),
            "earnings_growth": clean(info.get("earningsGrowth")) or clean(info.get("revenueGrowth")),
            "week_high": clean(info.get("fiftyTwoWeekHigh")),
            "week_low": clean(info.get("fiftyTwoWeekLow")),
            "dividend_pct": dividend_pct,
            "employees": clean(info.get("fullTimeEmployees")),
            "country": info.get("country", ""),
        }
        return cache_set(key, result)
    except Exception as e:
        return {"error": str(e)}


# ── מחיר בלבד — קל משקל, לרענון כל 30 שניות (מטמון 30 שניות) ──
@app.get("/price/{ticker}")
def get_price(ticker: str):
    key = "price:" + ticker.upper()
    cached = cache_get(key, 30)
    if cached:
        return cached
    try:
        stock = yf.Ticker(ticker, session=session)
        price = prev = None
        try:
            fi = stock.fast_info
            price = clean(fi["last_price"])
            prev = clean(fi["previous_close"])
        except Exception:
            pass
        if price is None:
            hist = stock.history(period="5d")
            cl = [clean(v) for v in hist["Close"].tolist() if clean(v) is not None]
            price = cl[-1] if cl else None
            prev = cl[-2] if len(cl) > 1 else price
        result = {"ticker": ticker.upper(), "price": price, "prev": prev}
        return cache_set(key, result)
    except Exception as e:
        return {"error": str(e)}


WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "META", "SOFI", "PLTR", "COIN", "HOOD", "AMD"]


# ── סורק מניות (מטמון 5 דקות) ──
@app.get("/scan")
def scan():
    cached = cache_get("scan", 300)
    if cached:
        return cached
    results = []
    for ticker in WATCHLIST:
        try:
            stock = yf.Ticker(ticker, session=session)
            hist = stock.history(period="6mo")
            if hist.empty or len(hist) < 20:
                continue
            closes = [float(v) for v in hist["Close"].tolist()]
            highs = [float(v) for v in hist["High"].tolist()]
            lows = [float(v) for v in hist["Low"].tolist()]
            price = closes[-1]
            ma9 = sum(closes[-9:]) / 9
            ma20 = sum(closes[-20:]) / 20

            # ── RSI ──
            gains, losses = 0, 0
            for i in range(-14, 0):
                d = closes[i] - closes[i - 1]
                if d > 0:
                    gains += d
                else:
                    losses -= d
            rsi = 100 - 100 / (1 + gains / (losses or 0.0001))

            # ── זיהוי אזורי תמיכה/התנגדות לפי נגיעות (clustering) ──
            lookback = 5
            pivots = []
            for i in range(lookback, len(closes) - lookback):
                is_high = all(highs[j] <= highs[i] for j in range(i - lookback, i + lookback + 1) if j != i)
                is_low = all(lows[j] >= lows[i] for j in range(i - lookback, i + lookback + 1) if j != i)
                if is_high:
                    pivots.append(highs[i])
                if is_low:
                    pivots.append(lows[i])
            tol = 0.025
            clusters = []
            for p in sorted(pivots):
                placed = False
                for c in clusters:
                    if abs(c["level"] - p) / c["level"] <= tol:
                        c["points"].append(p)
                        c["level"] = sum(c["points"]) / len(c["points"])
                        placed = True
                        break
                if not placed:
                    clusters.append({"level": p, "points": [p]})
            zones = [{"p": round(c["level"], 2), "touches": len(c["points"])}
                     for c in clusters if len(c["points"]) >= 2]

            # ── קרבה לפריצת התנגדות ──
            resists = sorted([z for z in zones if z["p"] > price], key=lambda z: z["p"])
            dist_to_break = None
            if resists:
                break_level = resists[0]["p"]
                dist_to_break = round((break_level - price) / price * 100, 2)

            # ── בניית איתותים ──
            signals = []
            if dist_to_break is not None and dist_to_break <= 5:
                signals.append("🎯 קרוב לפריצה " + str(dist_to_break) + "%")
            if rsi < 35:
                signals.append("RSI נמוך")
            if rsi > 70:
                signals.append("RSI גבוה")
            if ma9 > ma20:
                signals.append("MA9 מעל MA20")

            if signals:
                # ── ציון חוזק ההזדמנות (למיון) ──
                score = 0
                if dist_to_break is not None and dist_to_break <= 5:
                    score += (5 - dist_to_break) * 3   # ככל שקרוב יותר לפריצה — חזק יותר
                if rsi < 35:
                    score += (35 - rsi) / 5
                if rsi > 70:
                    score += (rsi - 70) / 5
                if ma9 > ma20:
                    score += 1
                results.append({
                    "ticker": ticker,
                    "price": round(price, 2),
                    "rsi": round(rsi, 1),
                    "dist_to_break": dist_to_break,
                    "signals": signals,
                    "score": round(score, 1),
                })
        except Exception:
            continue
    # ── מיון לפי חוזק ההזדמנות (הגבוה ביותר קודם) ──
    results.sort(key=lambda r: r["score"], reverse=True)
    return cache_set("scan", {"results": results})

# ── סנטימנט אנליסטים (מטמון שעה) ──
@app.get("/sentiment/{ticker}")
def get_sentiment(ticker: str):
    key = "sent:" + ticker.upper()
    cached = cache_get(key, 3600)
    if cached:
        return cached
    try:
        stock = yf.Ticker(ticker, session=session)
        info = stock.info
        rec = stock.recommendations
        if rec is not None and not rec.empty:
            latest = rec.tail(4)
            strong_buy = int(latest["strongBuy"].sum()) if "strongBuy" in latest.columns else 0
            buy = int(latest["buy"].sum()) if "buy" in latest.columns else 0
            hold = int(latest["hold"].sum()) if "hold" in latest.columns else 0
            sell = int(latest["sell"].sum()) if "sell" in latest.columns else 0
            strong_sell = int(latest["strongSell"].sum()) if "strongSell" in latest.columns else 0
        else:
            strong_buy = buy = hold = sell = strong_sell = 0
        total = strong_buy + buy + hold + sell + strong_sell
        short_pct = info.get("shortPercentOfFloat")
        result = {
            "ticker": ticker.upper(),
            "bull_pct": round((strong_buy + buy) / total * 100, 1) if total else None,
            "bear_pct": round((sell + strong_sell) / total * 100, 1) if total else None,
            "neutral_pct": round(hold / total * 100, 1) if total else None,
            "votes": {"strong_buy": strong_buy, "buy": buy, "hold": hold,
                      "sell": sell, "strong_sell": strong_sell, "total": total},
            "short_pct_float": round(short_pct * 100, 2) if short_pct else None,
            "recommendation": info.get("recommendationKey", ""),
        }
        return cache_set(key, result)
    except Exception as e:
        return {"error": str(e)}


# ── פרוקסי ל-Groq: המפתח נשאר בשרת, המודל כותב רק משפט מגמה ──
@app.post("/ai")
async def ai_analysis(req: Request):
    if not GROQ_KEY:
        return {"text": ""}
    try:
        body = await req.json()
    except Exception:
        body = {}
    ticker = body.get("ticker", "")
    trend = body.get("trend", "")
    rsi_txt = body.get("rsiTxt", "")
    bull = body.get("bullPct", "N/A")
    bear = body.get("bearPct", "N/A")
    prompt = (
        "אתה אנליסט מניות. כתוב ניתוח קצר בעברית (2-3 משפטים) על מגמת המניה " + str(ticker) +
        ". נתונים: מגמה " + str(trend) + ", RSI " + str(rsi_txt) +
        ", שורים " + str(bull) + "%, דובים " + str(bear) + "%. "
        "התייחס למשמעות של ה-RSI ולסנטימנט האנליסטים, וסיים בשורת תובנה אחת. "
        "אל תכתוב מספרים של מחיר/כניסה/סטופ/יעד — רק ניתוח מגמה במילים."
    )
    try:
        r = crequests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + GROQ_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b-instant",
                "max_tokens": 220,
                "messages": [{"role": "user", "content": prompt}],
            },
            impersonate="chrome",
            timeout=20,
        )
        d = r.json()
        text = d["choices"][0]["message"]["content"].strip()
        return {"text": text}
    except Exception as e:
        return {"text": "", "error": str(e)}


# ── פרוקסי לחדשות Finnhub: הטוקן נשאר בשרת (מטמון 5 דקות) ──
@app.get("/news")
def get_news():
    cached = cache_get("news", 300)
    if cached:
        return cached
    if not FINNHUB_KEY:
        return {"news": []}
    try:
        r = crequests.get(
            "https://finnhub.io/api/v1/news?category=general&token=" + FINNHUB_KEY,
            impersonate="chrome",
            timeout=15,
        )
        data = r.json()[:8]
        slim = [{
            "headline": n.get("headline", ""),
            "url": n.get("url", ""),
            "source": n.get("source", ""),
            "datetime": n.get("datetime", 0),
        } for n in data]
        return cache_set("news", {"news": slim})
    except Exception as e:
        return {"news": [], "error": str(e)}
