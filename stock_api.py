import os
import re
import time
import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from curl_cffi import requests as crequests
import yfinance as yf

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stockiq")

app = FastAPI()

# ── CORS: רק המקורות שלנו. פתיחה ל-"*" הופכת את /ai ל-פרוקסי ציבורי
# שמחויב על חשבון מפתח ה-Groq שלנו, ואת /news לצינור ששורף את מכסת Finnhub.
# אפשר להוסיף מקורות דרך משתנה סביבה ALLOWED_ORIGINS (מופרד בפסיקים). ──
DEFAULT_ORIGINS = [
    "https://tayhabaz-blip.github.io",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
]
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
] or DEFAULT_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

session = crequests.Session(impersonate="chrome")

# ── מפתחות מגיעים ממשתני סביבה ב-Render, לא מהקוד ──
GROQ_KEY = os.environ.get("GROQ_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")

# ── מטמון בזיכרון עם גבול עליון. בלי הגבול הוא רק גדל: כל רשומת /stock
# מחזיקה ארבעה מערכים של ~250 ימי מסחר, ואחרי כמה מאות טיקרים המופע
# החינמי של Render (512MB) נגמר וקורס. ──
_cache = {}
_CACHE_MAX = 300
_MAX_TTL = 24 * 3600  # ה-TTL הארוך ביותר שבשימוש (יקום הסריקה)


def cache_get(key, ttl):
    item = _cache.get(key)
    if item and (time.time() - item[0]) < ttl:
        return item[1]
    return None


def cache_set(key, val):
    if key not in _cache and len(_cache) >= _CACHE_MAX:
        now = time.time()
        # קודם זורקים כל מה שממילא פג תוקפו לחלוטין
        for k in [k for k, (ts, _) in _cache.items() if now - ts > _MAX_TTL]:
            _cache.pop(k, None)
        # אם זה לא הספיק — זורקים את הרבע הישן ביותר
        if len(_cache) >= _CACHE_MAX:
            oldest = sorted(_cache, key=lambda k: _cache[k][0])[: _CACHE_MAX // 4]
            for k in oldest:
                _cache.pop(k, None)
    _cache[key] = (time.time(), val)
    return val


# ── הגבלת קצב פשוטה לפי IP. Render מריץ מופע יחיד, אז מונה בזיכרון מספיק.
# בלי זה /ai הוא פרוקסי חינמי ל-Groq שכל אחד יכול להריץ בלולאה. ──
_rate = {}
_RATE_MAX_KEYS = 5000


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_ok(request: Request, bucket: str, limit: int, window: int) -> bool:
    now = time.time()
    key = bucket + ":" + _client_ip(request)
    hits = [t for t in _rate.get(key, []) if now - t < window]
    if len(hits) >= limit:
        _rate[key] = hits
        return False
    hits.append(now)
    _rate[key] = hits
    if len(_rate) > _RATE_MAX_KEYS:
        for k in [k for k, v in list(_rate.items()) if not v or now - v[-1] > 3600]:
            _rate.pop(k, None)
    return True


# ── תקרה יומית גלובלית לקריאות בתשלום ל-Groq.
# ההגבלה לפי IP חוסמת משתמש בודד, אבל היא לכל כתובת בנפרד — 12 לדקה
# הם מעל 17,000 ליום מכתובת אחת, ומי שמגוון את גוף הבקשה עוקף גם את
# המטמון. זו התקרה שמגבילה את החשיפה הכספית בפועל, ללא תלות במקור.
# שים לב: המונה יושב בזיכרון ומתאפס בכל דפלוי או הערה מרדמה של Render,
# ולכן הוא רשת ביטחון ולא תחליף לתקרת הוצאה בצד הספק.
AI_DAILY_MAX = int(os.environ.get("AI_DAILY_MAX", "1000"))
_ai_day = {"day": None, "count": 0}


def ai_budget_ok() -> bool:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _ai_day["day"] != today:
        _ai_day["day"] = today
        _ai_day["count"] = 0
    if _ai_day["count"] >= AI_DAILY_MAX:
        return False
    _ai_day["count"] += 1
    return True


# ── ולידציית טיקר: חוסמת מחרוזות שרירותיות שרק גורמות לנו להפציץ את Yahoo ──
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


def norm_ticker(t: str):
    t = (t or "").strip().upper()
    return t if TICKER_RE.match(t) else None


def err(status: int, msg: str):
    """שגיאה עם קוד HTTP אמיתי. שומרים על שדה error בגוף התשובה כדי
    שגרסאות קודמות של הפרונטאנד ימשיכו לעבוד."""
    return JSONResponse(status_code=status, content={"error": msg})


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
def get_stock(ticker: str, request: Request):
    ticker = norm_ticker(ticker)
    if not ticker:
        return err(400, "טיקר לא תקין")
    if not rate_ok(request, "stock", 60, 60):
        return err(429, "יותר מדי בקשות — נסה שוב בעוד רגע")
    key = "stock:" + ticker
    cached = cache_get(key, 300)
    if cached:
        return cached
    try:
        stock = yf.Ticker(ticker, session=session)
        hist = stock.history(period="1y")
        if hist.empty:
            return err(404, "מניה לא נמצאה")
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
            "ticker": ticker,
            "closes": closes, "highs": highs, "lows": lows,
            "volumes": volumes, "labels": labels,
            "name": info.get("longName", ticker),
            "description": info.get("longBusinessSummary", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": clean(info.get("marketCap")),
            "pe_ratio": clean(info.get("trailingPE")),
            "eps": clean(info.get("trailingEps")),
            "earnings_growth": clean(info.get("earningsGrowth")),
            "revenue_growth": clean(info.get("revenueGrowth")),
            "growth_source": ("earnings" if clean(info.get("earningsGrowth"))
                              else ("revenue" if clean(info.get("revenueGrowth")) else None)),
            "week_high": clean(info.get("fiftyTwoWeekHigh")),
            "week_low": clean(info.get("fiftyTwoWeekLow")),
            "dividend_pct": dividend_pct,
            "employees": clean(info.get("fullTimeEmployees")),
            "country": info.get("country", ""),
        }
        return cache_set(key, result)
    except Exception:
        log.exception("get_stock failed for %s", ticker)
        return err(502, "שגיאה בשליפת נתוני המניה")


# ── מחיר בלבד — קל משקל, לרענון כל 30 שניות (מטמון 30 שניות) ──
def _extended_hours_window():
    """True כשעדיין לא/כבר לא נמצאים בשעות המסחר הרגילות של ניו יורק —
    כלומר טרום-מסחר או מסחר לאחר סגירה. משמש כדי לא לבצע קריאה נוספת
    ומיותרת ל-yfinance בשעות המסחר הרגילות, שם ממילא אין מסחר מורחב."""
    try:
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return False
    if now_et.weekday() >= 5:
        return False
    t = now_et.time()
    return dtime(4, 0) <= t < dtime(9, 30) or dtime(16, 0) <= t <= dtime(20, 0)


@app.get("/price/{ticker}")
def get_price(ticker: str, request: Request):
    ticker = norm_ticker(ticker)
    if not ticker:
        return err(400, "טיקר לא תקין")
    if not rate_ok(request, "price", 120, 60):
        return err(429, "יותר מדי בקשות — נסה שוב בעוד רגע")
    key = "price:" + ticker
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
        if price is None:
            return err(404, "מניה לא נמצאה")
        result = {"ticker": ticker, "price": price, "prev": prev}

        # ── מסחר מורחב (טרום-מסחר / לאחר סגירה) — רק בחלון הזמן הרלוונטי,
        # כדי לא להכביד על yfinance עם קריאה נוספת בשעות המסחר הרגילות ──
        if _extended_hours_window():
            try:
                info = stock.info
                state = info.get("marketState")
                ah_price = None
                ref = None  # המחיר שמולו מחשבים את השינוי באחוזים
                if state == "PRE":
                    ah_price = clean(info.get("preMarketPrice"))
                    ref = prev  # מול הסגירה הקודמת
                elif state in ("POST", "POSTPOST"):
                    ah_price = clean(info.get("postMarketPrice"))
                    ref = price  # מול סגירת המסחר הרגיל היום
                # אחוז השינוי מחושב עצמאית מהמחירים המוצגים —
                # השדה של yahoo לא עקבי בפורמט (פעם אחוז, פעם שבר), וזו הדרך הבטוחה שמתאימה תמיד למחירים שמוצגים בפועל
                if ah_price is not None and ref:
                    result["afterHours"] = {
                        "state": state,
                        "price": ah_price,
                        "changePct": round((ah_price - ref) / ref * 100, 2),
                    }
            except Exception:
                pass

        return cache_set(key, result)
    except Exception:
        log.exception("get_price failed for %s", ticker)
        return err(502, "שגיאה בשליפת המחיר")


# ── מדדים עולמיים. הלקוח שולח ?ids= ומקבל רק את מה שהוא באמת מציג,
# כך שהעלות נגזרת מהבחירה של המשתמש ולא מגודל הרשימה כאן. שמות התצוגה
# יושבים בפרונטאנד (הם בעברית ועניין של הצגה) — כאן רק המיפוי לסימבול. ──
WORLD_INDICES = {
    # ארה"ב
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "dow": "^DJI",
    "russell": "^RUT",
    "vix": "^VIX",
    # אירופה
    "dax": "^GDAXI",
    "ftse": "^FTSE",
    "cac": "^FCHI",
    "stoxx": "^STOXX50E",
    "ibex": "^IBEX",
    "smi": "^SSMI",
    # אסיה ופסיפיק
    "nikkei": "^N225",
    "hangseng": "^HSI",
    "shanghai": "000001.SS",
    "kospi": "^KS11",
    "sensex": "^BSESN",
    "asx": "^AXJO",
    # ישראל — ת"א 35 הוא ללא ^ ביאהו, בניגוד לת"א 125. אומת מול
    # התשובה החיה: ^TA35.TA מחזיר ריק ולכן המדד נשמט מהרצועה.
    "ta35": "TA35.TA",
    "ta125": "^TA125.TA",
}
DEFAULT_INDICES = ["sp500", "nasdaq", "dow", "russell", "vix"]
MAX_INDICES = 12


@app.get("/indices")
def get_indices(request: Request, ids: str = ""):
    if not rate_ok(request, "indices", 20, 60):
        return err(429, "יותר מדי בקשות")
    # מסננים מול הרשימה המוכרת — כך ש-?ids= לא יכול להפוך את השרת
    # למשיכת סימבולים שרירותיים מיאהו מטעם מי שקורא לנו.
    wanted = []
    for raw in ids.split(","):
        iid = raw.strip().lower()
        if iid in WORLD_INDICES and iid not in wanted:
            wanted.append(iid)
        if len(wanted) >= MAX_INDICES:
            break
    if not wanted:
        wanted = list(DEFAULT_INDICES)
    # מפתח מטמון לפי הקבוצה המבוקשת, אחרת בחירות שונות דורסות זו את זו
    key = "indices:" + ",".join(sorted(wanted))
    cached = cache_get(key, 60)
    if cached is not None:
        return cached
    results = []
    for iid in wanted:
        try:
            t = yf.Ticker(WORLD_INDICES[iid], session=session)
            fi = t.fast_info
            price = clean(fi["last_price"])
            prev = clean(fi["previous_close"])
            if price is None:
                continue
            pct = round((price - prev) / prev * 100, 2) if prev else 0.0
            results.append({"id": iid, "price": price, "pct": pct})
        except Exception:
            log.debug("indices: skip %s", iid)
    if results:
        cache_set(key, results)
    return results


CORE_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "SOFI", "PLTR", "COIN", "HOOD", "SQ", "PYPL", "AFRM", "NU", "UBER", "ABNB",
    "SHOP", "CRWD", "SNOW", "DDOG", "NET", "MDB", "PANW", "ZS", "ARM", "MU",
    "INTC", "QCOM", "MRVL", "SMCI", "DELL", "ORCL", "ADBE", "CRM", "NOW", "INTU",
    "DIS", "BA", "JPM", "BAC", "V", "MA", "WMT", "COST", "PEP", "KO",
    "XOM", "CVX", "LLY", "UNH", "RIVN"
]


def _fetch_trending():
    """מביא פעם ביום עד 25 מהמניות הכי נסחרות היום מיאהו, כדי שהסריקה
    תשקף גם מה שקורה עכשיו בשוק, לא רק רשימה קבועה. אם זה נכשל מכל
    סיבה — פשוט חוזרים לרשימה הקבועה בלבד, הסריקה לא נשברת."""
    try:
        res = yf.screen("most_actives", count=25)
        quotes = res.get("quotes", []) if isinstance(res, dict) else []
        tickers = []
        for q in quotes:
            sym = q.get("symbol")
            if sym and sym.replace(".", "").replace("-", "").isalnum() and len(sym) <= 6:
                tickers.append(sym.upper())
        return tickers
    except Exception:
        return []


def get_universe():
    """הרשימה הקבועה + עד 25 מניות חמות של היום, ברענון של פעם ב-24 שעות."""
    cached = cache_get("trending_universe", 24 * 3600)
    if cached is not None:
        trending = cached.get("tickers", [])
    else:
        trending = _fetch_trending()
        cache_set("trending_universe", {"tickers": trending})
    merged = list(CORE_UNIVERSE)
    for t in trending:
        if t not in merged:
            merged.append(t)
    return merged

# ── סורק מניות (מטמון 5 דקות) ──
def _scan_one(ticker, hist):
    hist = hist.dropna(subset=["Close"])  # מונע שורה אחרונה ללא מחיר סגירה (שכיחה במשיכה מרוכזת) מקלקלת את החישובים
    if len(hist) < 20:
        return None
    closes = [float(v) for v in hist["Close"].tolist()]
    highs = [float(v) for v in hist["High"].tolist()]
    lows = [float(v) for v in hist["Low"].tolist()]
    price = closes[-1]
    if price != price:  # NaN safety (NaN != NaN מחזיר True)
        return None
    ma9 = sum(closes[-9:]) / 9
    ma20 = sum(closes[-20:]) / 20

    # ── RSI בהחלקת Wilder — התקן המקובל בפלטפורמות מסחר (TradingView וכו').
    # ממוצע פשוט על 14 ימים נותן מספר שונה מהותית ולעיתים חוצה את סף ה-70. ──
    period = 14
    if len(closes) < period + 1:
        return None
    gain_sum = loss_sum = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gain_sum += d
        else:
            loss_sum -= d
    avg_gain = gain_sum / period
    avg_loss = loss_sum / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + (d if d > 0 else 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + (-d if d < 0 else 0.0)) / period
    rsi = 100 - 100 / (1 + avg_gain / (avg_loss or 0.0001))

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

    if not signals:
        return None

    # ── ציון חוזק ההזדמנות (למיון) ──
    # RSI גבוה הוא סימן אזהרה (מניה "מתוחה"), לא הזדמנות — לכן הוא לא
    # מוסיף לציון. הוא מסומן בנפרד כדי שהמשתמש יבחין בזה במבט מהיר.
    score = 0
    if dist_to_break is not None and dist_to_break <= 5:
        score += (5 - dist_to_break) * 3   # ככל שקרוב יותר לפריצה — חזק יותר
    if rsi < 35:
        score += (35 - rsi) / 5            # אזור מכירת יתר — פוטנציאל להיפוך
    if ma9 > ma20:
        score += 1

    overbought = rsi > 70

    # ── מיני-גרף: 20 נקודות אחרונות בלבד, לתצוגה בכרטיס הסריקה ──
    spark = [round(v, 2) for v in closes[-20:]]

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "rsi": round(rsi, 1),
        "dist_to_break": dist_to_break,
        "signals": signals,
        "score": round(score, 1),
        "overbought": overbought,
        "spark": spark,
    }


MAX_SCAN_TICKERS = 60
# תקרה למספר המשיכות הבודדות כשהמשיכה המרוכזת נכשלת חלקית.
# בלי תקרה, כשל מלא של bulk הופך את הסריקה ל-80 קריאות סדרתיות ל-Yahoo,
# מה שגורר timeout ב-Render ומסתיים בלי שום תוצאה.
MAX_INDIVIDUAL_FETCHES = 15


@app.get("/scan")
def scan(request: Request, tickers: str = ""):
    if not rate_ok(request, "scan", 10, 60):
        return err(429, "יותר מדי סריקות — נסה שוב בעוד רגע")

    # ── רשימה מותאמת (רשימת המעקב של המשתמש) או היקום המלא ──
    custom = []
    if tickers:
        for raw in tickers.split(","):
            t = norm_ticker(raw)
            if t and t not in custom:
                custom.append(t)
        custom = custom[:MAX_SCAN_TICKERS]
        if not custom:
            return err(400, "לא נמצאו טיקרים תקינים")

    cache_key = "scan:" + (",".join(sorted(custom)) if custom else "__universe__")
    cached = cache_get(cache_key, 300)
    if cached:
        return cached

    universe = custom or get_universe()

    # ── משיכה מרוכזת: בקשה אחת ליקום כולו במקום אחת לכל מניה ──
    bulk = None
    try:
        bulk = yf.download(
            tickers=" ".join(universe),
            period="6mo",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
            session=session,
        )
        if bulk is None or len(bulk) == 0:
            bulk = None
    except Exception:
        bulk = None

    results = []
    individual_fetches = 0
    for ticker in universe:
        try:
            hist = None
            if bulk is not None:
                try:
                    hist = bulk[ticker].dropna(how="all")
                    if hist is None or hist.empty or len(hist) < 20:
                        hist = None  # הטיקר קיים במבנה אבל ריק — ננסה משיכה בודדת
                except Exception:
                    hist = None

            if hist is None and individual_fetches < MAX_INDIVIDUAL_FETCHES:
                # גיבוי: משיכה בודדת לטיקר הזה בלבד, כמו ב-/stock/{ticker}.
                # מוגבל בכמות כדי שכשל רחב לא יהפוך את הסריקה ל-timeout.
                individual_fetches += 1
                try:
                    hist = yf.Ticker(ticker, session=session).history(period="6mo")
                except Exception:
                    hist = None

            if hist is None or hist.empty or len(hist) < 20:
                continue

            row = _scan_one(ticker, hist)
            if row:
                results.append(row)
        except Exception:
            log.warning("scan failed for %s", ticker, exc_info=True)
            continue

    # ── מיון לפי חוזק ההזדמנות (הגבוה ביותר קודם) ──
    results.sort(key=lambda r: r["score"], reverse=True)
    return cache_set(cache_key, {"results": results})

# ── סנטימנט אנליסטים (מטמון שעה) ──
@app.get("/sentiment/{ticker}")
def get_sentiment(ticker: str, request: Request):
    ticker = norm_ticker(ticker)
    if not ticker:
        return err(400, "טיקר לא תקין")
    if not rate_ok(request, "sent", 60, 60):
        return err(429, "יותר מדי בקשות — נסה שוב בעוד רגע")
    key = "sent:" + ticker
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
            "ticker": ticker,
            "bull_pct": round((strong_buy + buy) / total * 100, 1) if total else None,
            "bear_pct": round((sell + strong_sell) / total * 100, 1) if total else None,
            "neutral_pct": round(hold / total * 100, 1) if total else None,
            "votes": {"strong_buy": strong_buy, "buy": buy, "hold": hold,
                      "sell": sell, "strong_sell": strong_sell, "total": total},
            "short_pct_float": round(short_pct * 100, 2) if short_pct else None,
            "recommendation": info.get("recommendationKey", ""),
        }
        return cache_set(key, result)
    except Exception:
        log.exception("get_sentiment failed for %s", ticker)
        return err(502, "שגיאה בשליפת נתוני אנליסטים")


# ── פרוקסי ל-Groq: המפתח נשאר בשרת, המודל כותב רק ניסוח מגמה ──
@app.post("/ai")
async def ai_analysis(req: Request):
    # ── הגבלה הדוקה קודם כל: זו הנקודה היחידה שעולה לנו כסף אמיתי בכל
    # קריאה, ולכן המונה חייב לרוץ עוד לפני כל בדיקה אחרת. ──
    if not rate_ok(req, "ai", 12, 60):
        return err(429, "יותר מדי בקשות ניתוח — נסה שוב בעוד רגע")
    if not GROQ_KEY:
        return {"text": ""}
    try:
        body = await req.json()
    except Exception:
        body = {}
    ticker = body.get("ticker", "")
    trend = body.get("trend", "")
    rsi_txt = body.get("rsiTxt", "")
    rsi_num = body.get("rsiNum")
    bull = body.get("bullPct", "N/A")
    bear = body.get("bearPct", "N/A")
    sector = body.get("sector")
    pe = body.get("peRatio")
    week_pos = body.get("weekPos")       # 0-100: מיקום המחיר בטווח 52 השבועות
    dist_break = body.get("distToBreakPct")

    # ── בניית רשימת עובדות מדויקות — ככל שיש יותר נתונים אמיתיים, הניתוח פחות כללי ──
    facts = ["מניית " + str(ticker) + (" בסקטור " + str(sector) if sector else "") + "."]
    facts.append("מגמה טכנית (ממוצעים נעים): " + str(trend) + ".")
    facts.append("RSI: " + (str(rsi_num) if rsi_num is not None else "לא זמין") + " (" + str(rsi_txt) + ").")
    if pe:
        facts.append("מכפיל רווח P/E: " + str(pe) + ".")
    if week_pos is not None:
        facts.append("מיקום המחיר בטווח 52 השבועות: " + str(week_pos) + "% (100% = שיא שנתי, 0% = שפל שנתי).")
    if dist_break is not None:
        facts.append("מרחק מההתנגדות הקרובה ביותר: " + str(dist_break) + "%.")
    facts.append("סנטימנט אנליסטים: " + str(bull) + "% שוריים, " + str(bear) + "% דוביים.")

    # ── מטמון: אותה מניה עם אותה תמונה טכנית מחזירה את אותו ניתוח.
    # בלי זה כל צפייה חוזרת היא קריאה נוספת בתשלום ל-Groq. ──
    ai_key = "ai:" + "|".join(str(x) for x in [
        ticker, trend, rsi_txt,
        round(rsi_num) if isinstance(rsi_num, (int, float)) else rsi_num,
        bull, bear, sector, pe, week_pos,
        round(dist_break) if isinstance(dist_break, (int, float)) else dist_break,
    ])
    cached = cache_get(ai_key, 3600)
    if cached:
        return cached

    # התקרה נבדקת רק אחרי המטמון — תשובה שכבר שילמנו עליה לא נספרת שוב.
    # בחריגה מחזירים טקסט ריק: הכרטיס נופל לניתוח המחושב מקומית והאפליקציה
    # ממשיכה לעבוד, במקום להציג שגיאה או להמשיך לחייב.
    if not ai_budget_ok():
        log.warning("AI daily budget of %s reached; serving empty text", AI_DAILY_MAX)
        return {"text": ""}

    prompt = (
        "אתה אנליסט מניות מנוסה. הנה נתונים עובדתיים בלבד על מניה:\n"
        + "\n".join(facts) +
        "\n\nכתוב ניתוח קצר בעברית, 3-4 משפטים בלבד: "
        "משפט אחד על מה שתומך בתמונה החיובית, משפט אחד על הסיכון או החולשה המרכזית שכדאי להיות מודעים אליה, "
        "ומשפט סיכום שמחבר בין התמונה הטכנית לנתונים הפונדמנטליים. "
        "אל תכתוב מספרי מחיר, כניסה, סטופ או יעד — אלה כבר מחושבים בנפרד. "
        "אל תמליץ לקנות או למכור, ואל תשתמש במילים כמו 'כדאי' או 'מומלץ' — רק תאר את התמונה במאוזן."
    )
    try:
        r = crequests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + GROQ_KEY,
                "Content-Type": "application/json",
            },
            json={
                # gpt-oss-120b הוא מודל reasoning ודורש max_completion_tokens.
                # השם max_tokens נדחה על ידיו, ובלי reasoning_effort נמוך
                # טוקני החשיבה בולעים את התקציב והתוכן חוזר ריק.
                "model": "openai/gpt-oss-120b",
                "max_completion_tokens": 600,
                "reasoning_effort": "low",
                "messages": [{"role": "user", "content": prompt}],
            },
            impersonate="chrome",
            timeout=20,
        )
        d = r.json()
        if "choices" not in d:
            log.warning("groq returned no choices: %s", str(d)[:400])
            return {"text": ""}
        text = (d["choices"][0]["message"].get("content") or "").strip()
        if not text:
            return {"text": ""}
        return cache_set(ai_key, {"text": text})
    except Exception:
        log.exception("ai_analysis failed for %s", ticker)
        return {"text": ""}


def _translate(txt: str, budget_left: float):
    """תרגום כותרת בודדת לעברית. נקודת הקצה של גוגל אינה רשמית, ולכן
    הכישלון כאן חייב להיות רך — מחזירים None והלקוח יציג את המקור."""
    if not txt or budget_left <= 0:
        return None
    try:
        r = crequests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "he", "dt": "t", "q": txt},
            impersonate="chrome",
            timeout=min(4, budget_left),
        )
        parts = r.json()[0]
        return "".join(p[0] for p in parts if p and p[0]) or None
    except Exception:
        return None


# ── פרוקסי לחדשות Finnhub: הטוקן נשאר בשרת (מטמון 5 דקות).
# התרגום נעשה כאן ולא בדפדפן: כך זו קריאה אחת לכל 5 דקות עבור כל
# המבקרים יחד, במקום שמונה קריאות אצל כל מבקר בנפרד. ──
@app.get("/news")
def get_news(request: Request):
    if not rate_ok(request, "news", 30, 60):
        return err(429, "יותר מדי בקשות — נסה שוב בעוד רגע")
    cached = cache_get("news", 300)
    if cached:
        return cached
    if not FINNHUB_KEY:
        return {"news": []}
    try:
        r = crequests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": FINNHUB_KEY},
            impersonate="chrome",
            timeout=15,
        )
        data = r.json()
        if not isinstance(data, list):
            log.warning("finnhub returned unexpected payload: %s", str(data)[:200])
            return {"news": []}
        data = data[:8]

        deadline = time.time() + 10  # תקציב זמן כולל לתרגום
        slim = []
        for n in data:
            headline = n.get("headline", "")
            slim.append({
                "headline": headline,
                "headline_he": _translate(headline, deadline - time.time()),
                "url": n.get("url", ""),
                "source": n.get("source", ""),
                "datetime": n.get("datetime", 0),
            })
        return cache_set("news", {"news": slim})
    except Exception:
        log.exception("get_news failed")
        return {"news": []}
