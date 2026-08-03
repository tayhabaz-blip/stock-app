/* מקרי הבדיקה ללוגיקת הפרונטאנד.
 *
 * הרקע: כל הלוגיקה שהמשתמש רואה בפועל — זיהוי אזורי תמיכה והתנגדות, מיזוג
 * בין רזולוציות, התקדים ההיסטורי, ספי ה-RSI — חיה ב-index.html ולא הייתה
 * מכוסה בשום בדיקה. האימות היחיד היה עיניים מול צילום מסך.
 */
'use strict';
const { group, test, assert, eq, close } = require('./run.js');
const fs = require('fs');
const path = require('path');
const X = require('./extract.js');

const F = X.load(['zones', 'mergeZones', 'computeTwins', 'rsi', 'aiFailureText']);

/* ---------------------------------------------------------------- */
group('ספי RSI — עקביות בין הדפדפן לשרת');

const jsOversold = X.extractNumericConst('RSI_OVERSOLD');
const jsOverbought = X.extractNumericConst('RSI_OVERBOUGHT');

test('הדפדפן משתמש בספים התקניים 30/70', () => {
  eq(jsOversold, 30, 'RSI_OVERSOLD');
  eq(jsOverbought, 70, 'RSI_OVERBOUGHT');
});

test('הספים בדפדפן ובשרת זהים', () => {
  // זה בדיוק הבאג שנמצא בפרודקשן: הסורק סימן "RSI נמוך" מתחת ל-35 בעוד
  // כרטיס המדד הציג "Neutral" עד 30. שתי שפות, שני קבצים, סף אחד — ובלי
  // הבדיקה הזו שום דבר לא מכריח אותם להסכים.
  const apiPath = path.join(__dirname, '..', '..', 'stock_api.py');
  const py = fs.readFileSync(apiPath, 'utf8');
  const pyOversold = parseFloat(py.match(/RSI_OVERSOLD\s*=\s*(\d+)/)[1]);
  const pyOverbought = parseFloat(py.match(/RSI_OVERBOUGHT\s*=\s*(\d+)/)[1]);
  eq(jsOversold, pyOversold, 'סף מכירת יתר נפרד בין הדפדפן לשרת');
  eq(jsOverbought, pyOverbought, 'סף קניית יתר נפרד בין הדפדפן לשרת');
});

test('אין ספי RSI מקובעים שנשארו בקוד', () => {
  const leftovers = [/rv<35/, /rsiVal<35/, /rv_>70\?/, /rv_<30\?/, /d\.rsi>70/];
  leftovers.forEach(re => assert(!re.test(X.html),
    'נמצא סף מקובע במקום שימוש בקבוע: ' + re));
});

/* ---------------------------------------------------------------- */
group('rsi — החלקת Wilder');

test('מחזיר null עד שיש מספיק נתונים', () => {
  const out = F.rsi([1, 2, 3], 14);
  assert(out.every(v => v === null), 'ערכים מוקדמים אמורים להיות null');
});

test('עלייה רציפה נותנת RSI גבוה מאוד', () => {
  const up = Array.from({ length: 40 }, (_, i) => 100 + i);
  const v = F.rsi(up, 14).filter(x => x !== null).pop();
  assert(v > 95, 'עלייה רציפה אמורה לתת RSI קרוב ל-100, התקבל ' + v);
});

test('ירידה רציפה נותנת RSI נמוך מאוד', () => {
  const down = Array.from({ length: 40 }, (_, i) => 200 - i);
  const v = F.rsi(down, 14).filter(x => x !== null).pop();
  assert(v < 5, 'ירידה רציפה אמורה לתת RSI קרוב ל-0, התקבל ' + v);
});

test('מדלג על ערכים חסרים בלי לקרוס', () => {
  const d = Array.from({ length: 40 }, (_, i) => 100 + (i % 5));
  d[7] = null; d[19] = null;
  const out = F.rsi(d, 14);
  assert(out.filter(v => v !== null).length > 0, 'אמור להחזיר ערכים גם עם חורים');
});

/* ---------------------------------------------------------------- */
group('zones — זיהוי תמיכה והתנגדות');

function synthetic(n) {
  const closes = [], highs = [], lows = [];
  for (let i = 0; i < n; i++) {
    const base = 100 + 10 * Math.sin(i / 7);
    closes.push(base);
    highs.push(base + 1);
    lows.push(base - 1);
  }
  return { closes, highs, lows };
}

test('מסווג לפי המחיר החי ולא לפי הסגירה האחרונה', () => {
  const { closes, highs, lows } = synthetic(120);
  const zs = F.zones(closes, highs, lows, 100);
  zs.forEach(z => {
    if (z.p < 100) eq(z.t, 'תמיכה', 'רמה מתחת למחיר');
    else eq(z.t, 'התנגדות', 'רמה מעל המחיר');
  });
});

test('כל אזור נשען על לפחות שתי נגיעות', () => {
  const { closes, highs, lows } = synthetic(150);
  F.zones(closes, highs, lows, 100).forEach(z =>
    assert(z.touches >= 2, 'אזור עם נגיעה אחת בלבד אינו אזור'));
});

test('סדרה קצרה מדי מחזירה רשימה ריקה ולא קורסת', () => {
  const out = F.zones([1, 2, 3], [1, 2, 3], [1, 2, 3], 2);
  assert(Array.isArray(out), 'אמור להחזיר מערך');
});

/* ---------------------------------------------------------------- */
group('mergeZones — מיזוג יומי ושבועי');

test('רמה שמופיעה בשתי הרזולוציות מסומנת כמאושרת', () => {
  const daily = [{ p: 120.0, touches: 2 }];
  const long = [{ p: 121.5, touches: 3 }];
  const m = F.mergeZones(daily, long, 100);
  eq(m.length, 1, 'שתי רמות קרובות אמורות להתמזג לאחת');
  eq(m[0].confirmed, true, 'רמה משותפת אמורה להיות מסומנת כמאושרת');
  eq(m[0].touches, 5, 'הנגיעות אמורות להצטבר');
});

test('אישור בשתי רזולוציות מעלה את דירוג העוצמה', () => {
  const confirmed = F.mergeZones([{ p: 120, touches: 2 }], [{ p: 121, touches: 2 }], 100)[0];
  const single = F.mergeZones([], [{ p: 160, touches: 2 }], 100)[0];
  assert(confirmed.eff > single.eff,
    'רמה שהחזיקה גם בקצר וגם בארוך אמורה לדרג גבוה יותר');
});

test('רמה שבועית רחוקה נשמרת ואינה נבלעת', () => {
  const m = F.mergeZones([{ p: 120, touches: 2 }], [{ p: 160, touches: 4 }], 100);
  assert(m.some(z => z.p === 160), 'הרמה הרחוקה היא בדיוק היעד שחיפשנו');
});

test('רמה יומית ייחודית נשמרת', () => {
  const m = F.mergeZones([{ p: 88, touches: 2 }], [{ p: 160, touches: 3 }], 100);
  assert(m.some(z => z.p === 88), 'רמה יומית בלעדית לא אמורה להיעלם');
});

test('התוצאה ממוינת מהחזק לחלש', () => {
  const m = F.mergeZones(
    [{ p: 88, touches: 2 }, { p: 120, touches: 2 }],
    [{ p: 121, touches: 4 }, { p: 160, touches: 2 }], 100);
  for (let i = 1; i < m.length; i++) {
    assert(m[i - 1].eff >= m[i].eff, 'המיון לפי עוצמה נשבר');
  }
});

test('קלט ריק אינו קורס', () => {
  assert(Array.isArray(F.mergeZones([], [], 100)), 'אמור להחזיר מערך');
  assert(Array.isArray(F.mergeZones(null, null, 100)), 'אמור לעמוד ב-null');
});

/* ---------------------------------------------------------------- */
group('computeTwins — התקדים ההיסטורי');

function repeating(n) {
  const closes = [], labels = [];
  for (let i = 0; i < n; i++) {
    closes.push(100 + 10 * Math.sin(i / 9) + i * 0.05);
    labels.push('d' + i);
  }
  return { closes, labels };
}

test('winRate עקבי עם התקדימים שנבחרו', () => {
  const { closes, labels } = repeating(400);
  const tw = F.computeTwins(closes, labels, 20, 10, 3);
  const expected = Math.round(tw.top.filter(c => c.fwdReturn > 0).length / tw.top.length * 100);
  eq(tw.winRate, expected, 'winRate אינו תואם את התקדימים');
});

test('avgFwd הוא הממוצע בפועל', () => {
  const { closes, labels } = repeating(400);
  const tw = F.computeTwins(closes, labels, 20, 10, 3);
  const mean = tw.top.reduce((s, c) => s + c.fwdReturn, 0) / tw.top.length;
  close(tw.avgFwd, mean, 1e-9, 'avgFwd');
});

test('מחזיר בדיוק topK תקדימים', () => {
  const { closes, labels } = repeating(400);
  eq(F.computeTwins(closes, labels, 20, 10, 3).top.length, 3);
});

test('היסטוריה קצרה מדי מחזירה null ולא נתון מטעה', () => {
  eq(F.computeTwins([1, 2, 3], ['a', 'b', 'c'], 20, 10, 3), null);
});

test('עומד בערכים חסרים בתוך הסדרה', () => {
  const { closes, labels } = repeating(400);
  const withHoles = closes.slice();
  withHoles[5] = null; withHoles[50] = null;
  assert(F.computeTwins(withHoles, labels, 20, 10, 3) !== null, 'לא אמור לקרוס על חורים');
});

test('החלון קדימה לעולם אינו חורג מהנתונים', () => {
  const { closes, labels } = repeating(200);
  const tw = F.computeTwins(closes, labels, 20, 10, 3);
  tw.top.forEach(c => assert(c.end + 10 < closes.length,
    'תקדים שמסתמך על נתונים שאינם קיימים'));
});

/* ---------------------------------------------------------------- */
group('ספי "אין מבנה קרוב"');

const FAR_BREAK = X.extractNumericConst('FAR_BREAK_PCT');
const FAR_SUPPORT = X.extractNumericConst('FAR_SUPPORT_PCT');

test('הספים מוגדרים ב-20%', () => {
  eq(FAR_BREAK, 20);
  eq(FAR_SUPPORT, 20);
});

test('המקרה של INTC מפעיל את האזהרה', () => {
  // מחיר 90.20, התנגדות 141.90 (57.32% מעל), תמיכה 42.93% מתחת
  assert(57.32 > FAR_BREAK && 42.93 > FAR_SUPPORT,
    'המקרה שהתגלה בפרודקשן חייב להפעיל את האזהרה');
});

test('המקרה של NVDA אינו מפעיל אותה', () => {
  assert(!(6.14 > FAR_BREAK) && !(2.51 > FAR_SUPPORT),
    'מניה עם מבנה תקין לא אמורה לקבל אזהרה');
});

/* ---------------------------------------------------------------- */
group('aiFailureText — הודעות כשל');

test('חריגת מכסה אינה מבטיחה "בעוד רגע"', () => {
  const msg = F.aiFailureText({ reason: 'rate_limited' });
  assert(!msg.includes('בעוד רגע'),
    'מכסה נמדדת לאורך שעות — הבטחה לרגע היא פשוט לא נכונה');
  assert(msg.length > 10, 'ההודעה אמורה להיות אינפורמטיבית');
});

test('לכל סיבה יש הודעה ייחודית', () => {
  const seen = new Set();
  ['budget', 'rate_limited', 'transient'].forEach(r => {
    const m = F.aiFailureText({ reason: r });
    assert(!seen.has(m), 'שתי סיבות מחזירות אותה הודעה: ' + r);
    seen.add(m);
  });
});

test('סיבה לא מוכרת נופלת להודעה כללית', () => {
  assert(F.aiFailureText({ reason: 'משהו-חדש' }).length > 0);
  assert(F.aiFailureText(null).length > 0, 'גם null אמור להחזיר הודעה');
});

test('שגיאה מפורשת מהשרת גוברת', () => {
  eq(F.aiFailureText({ error: 'שגיאה מהשרת' }), 'שגיאה מהשרת');
});
