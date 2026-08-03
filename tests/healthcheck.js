#!/usr/bin/env node
/* בדיקת בריאות לפרודקשן החי.
 *
 * למה זה קיים: כל שאר הבדיקות רצות על הקוד. הן לא היו תופסות את התקלה
 * שקרתה ב-3.8.2026, שבה ה-AI היה מושבת לגמרי בגלל חריגה ממכסת Groq —
 * הקוד היה תקין לחלוטין, המוצר לא עבד, והדבר היחיד שגילה את זה היה
 * שהמשתמש שם לב. הסקריפט הזה בודק את המוצר עצמו, לא את הקוד.
 *
 * הרצה:  node tests/healthcheck.js
 * יציאה: 0 = הכול תקין, 1 = נמצאה תקלה (הפירוט מודפס)
 */
'use strict';

const API = process.env.API_BASE || 'https://stock-app-1-dbrs.onrender.com';
const SITE = process.env.SITE_BASE || 'https://tayhabaz-blip.github.io/stock-app';

const problems = [];
const notes = [];
let checks = 0;

function fail(area, msg) { problems.push('[' + area + '] ' + msg); }
function ok(area, msg) { checks++; notes.push('  ✓ ' + area + (msg ? ' — ' + msg : '')); }

async function getJson(url, timeoutMs) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs || 30000);
  try {
    const r = await fetch(url, { signal: ctrl.signal });
    const j = await r.json().catch(() => null);
    return { status: r.status, json: j };
  } finally { clearTimeout(t); }
}

async function postJson(url, body, timeoutMs) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs || 40000);
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    const j = await r.json().catch(() => null);
    return { status: r.status, json: j };
  } finally { clearTimeout(t); }
}

/* ---------- 1. נקודות קצה ---------- */
async function checkEndpoints() {
  const cases = [
    ['/', '/', j => j && j.status === 'ok'],
    ['/stock', '/stock/AAPL', j => j && Array.isArray(j.closes) && j.closes.length > 100],
    ['/price', '/price/AAPL', j => j && typeof j.price === 'number'],
    ['/history שבועי', '/history/AAPL?range=5y&interval=1wk',
      j => j && j.interval === '1wk' && Array.isArray(j.highs) && j.highs.length > 0],
    ['/sentiment', '/sentiment/AAPL', j => j && typeof j === 'object'],
    ['/news לפי מניה', '/news/AAPL', j => j && Array.isArray(j.headlines)],
    ['/scan', '/scan?tickers=AAPL,MSFT', j => j && Array.isArray(j.results)],
  ];
  for (const [name, path, check] of cases) {
    try {
      const { status, json } = await getJson(API + path);
      if (status !== 200) fail('שרת', name + ' החזיר סטטוס ' + status);
      else if (!check(json)) fail('שרת', name + ' החזיר מבנה לא צפוי');
      else ok('שרת ' + name);
    } catch (e) {
      fail('שרת', name + ' נכשל: ' + (e.name === 'AbortError' ? 'timeout' : e.message));
    }
  }
}

/* ---------- 2. ה-AI באמת עונה, ובאיכות ---------- */
const BANNED = ['תמונה מורכבת', 'תמונה מעורבת', 'מצב מורכב', 'יש לזכור',
  'מחייב ניתוח מעמיק', 'חשוב לציין', 'כל משקיע', 'דורש זהירות'];

async function checkAI() {
  const body = {
    ticker: 'HEALTHCHECK', trend: 'עולה', rsiNum: 32,
    bullPct: 61, bearPct: 9, sector: 'טכנולוגיה', peRatio: 34.2,
    weekPos: 71, distToBreakPct: 2.4, change5dPct: -1.8, relVolume: 2.2,
    invalidationLevel: 284.31, invalidationPct: 4.2, invalidationStr: 'חזקה',
    nextSupportLevel: 262.5, nextSupportPct: 11.6,
  };
  let res;
  try {
    res = await postJson(API + '/ai', body);
  } catch (e) {
    fail('AI', 'הקריאה נכשלה: ' + (e.name === 'AbortError' ? 'timeout' : e.message));
    return;
  }
  if (res.status !== 200) { fail('AI', 'סטטוס ' + res.status); return; }

  const j = res.json || {};
  const text = (j.text || '').trim();

  if (!text) {
    // זו התקלה שקרתה בפועל — הבחנה בין הסיבות חשובה לתגובה הנכונה
    const reason = j.reason || 'לא ידוע';
    if (reason === 'rate_limited') fail('AI', 'מושבת: מכסת Groq נוצלה במלואה');
    else if (reason === 'budget') fail('AI', 'מושבת: התקרה היומית של האפליקציה מוצתה');
    else if (reason === 'unavailable') fail('AI', 'מושבת: מפתח Groq אינו מוגדר בשרת');
    else fail('AI', 'החזיר טקסט ריק, סיבה: ' + reason);
    return;
  }
  ok('AI מחזיר ניתוח', text.length + ' תווים');

  const found = BANNED.filter(b => text.includes(b));
  if (found.length) fail('איכות', 'ביטויי מילוי אסורים: ' + found.join(', '));
  else ok('אין ביטויי מילוי');

  if (/[֐-׿][A-Za-z]|[A-Za-z][֐-׿]/.test(text)) fail('איכות', 'אותיות לטיניות בתוך מילה עברית');
  else ok('אין אנגלית בתוך עברית');

  // RSI 32 אינו מכירת יתר לפי הסף התקני 30 — זו הייתה טעות מקצועית אמיתית
  if (/מכירת יתר/.test(text)) fail('איכות', 'RSI 32 סווג בטעות כמכירת יתר');
  else ok('סיווג RSI נכון');

  if (!/[.!?]\s*$/.test(text)) fail('איכות', 'התשובה נקטעה באמצע');
  else ok('התשובה שלמה');

  if (/ %/.test(text) || text.includes('‑')) fail('איכות', 'טיפוגרפיה לא מנורמלת');
  else ok('טיפוגרפיה תקינה');

  if (/באותו רמה|תתמוך תמיכה|איזור/.test(text)) fail('איכות', 'שגיאת עברית שאמורה להיות חסומה');
  else ok('אין שגיאות עברית ידועות');
}

/* ---------- 3. האתר מגיש את הגרסה הנוכחית ---------- */
async function checkSite() {
  try {
    const r = await fetch(SITE + '/index.html?healthcheck=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) { fail('אתר', 'index.html החזיר ' + r.status); return; }
    const html = await r.text();
    const markers = ['structureBox', 'mergedSupports', 'rate_limited', 'RSI_OVERSOLD', 'twinStats'];
    const missing = markers.filter(m => !html.includes(m));
    if (missing.length) fail('אתר', 'הגרסה המוגשת חסרה: ' + missing.join(', '));
    else ok('האתר מגיש את הגרסה הנוכחית');

    const m = html.match(/const RSI_OVERSOLD=(\d+), RSI_OVERBOUGHT=(\d+)/);
    if (!m) fail('אתר', 'ספי RSI לא נמצאו');
    else if (m[1] !== '30' || m[2] !== '70') fail('אתר', 'ספי RSI השתנו: ' + m[1] + '/' + m[2]);
    else ok('ספי RSI 30/70');
  } catch (e) {
    fail('אתר', 'לא ניתן לטעון: ' + e.message);
  }
}

/* ---------- 4. תזכורת על מודל מיושן ---------- */
async function checkModelDeadline() {
  // llama-3.3-70b נסגר ב-16.08.2026. אם מישהו יחזיר אותו בטעות, שנדע.
  try {
    const r = await fetch('https://raw.githubusercontent.com/tayhabaz-blip/stock-app/main/stock_api.py');
    if (!r.ok) return;
    const src = await r.text();
    const m = src.match(/AI_MODEL\s*=\s*"([^"]+)"/);
    if (m && /llama-3\.3-70b|llama-3\.1-8b/.test(m[1])) {
      fail('מודל', 'המודל ' + m[1] + ' מיושן ונסגר ב-16.08.2026');
    } else if (m) ok('המודל בשימוש', m[1]);
  } catch (e) { /* לא קריטי */ }
}

/* ---------- הרצה ---------- */
(async () => {
  await checkEndpoints();
  await checkAI();
  await checkSite();
  await checkModelDeadline();

  console.log('StockIQ — בדיקת בריאות פרודקשן');
  console.log('='.repeat(52));
  notes.forEach(n => console.log(n));

  if (problems.length === 0) {
    console.log('\n✅ הכול תקין (' + checks + ' בדיקות)');
    process.exit(0);
  }
  console.log('\n❌ נמצאו ' + problems.length + ' תקלות:');
  problems.forEach(p => console.log('  • ' + p));
  process.exit(1);
})();
