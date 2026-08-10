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

/* ---------------------------------------------------------------- */
group('בידוד דו-כיווני — מספרים בממשק עברי');

const B = X.load(['num', 'seg']);
const LRI = '⁦', RLI = '⁧', PDI = '⁩';

test('num עוטף ערך מספרי בבידוד שמאל-לימין', () => {
  eq(B.num('+9.08%'), LRI + '+9.08%' + PDI, 'num');
  eq(B.num('$300.26–$337.30'), LRI + '$300.26–$337.30' + PDI, 'num טווח');
});

test('seg עוטף פריט מעורב בבידוד ימין-לשמאל', () => {
  eq(B.seg('MA9 מעל MA20'), RLI + 'MA9 מעל MA20' + PDI, 'seg');
});

test('ערך ריק נשאר ריק ולא הופך לתווי בקרה', () => {
  // בלי התנאי הזה תא ריק בטבלה היה מקבל שני תווים בלתי נראים ונראה "מלא"
  eq(B.num(''), '', 'מחרוזת ריקה');
  eq(B.num(null), null, 'null');
  eq(B.seg(undefined), undefined, 'undefined');
});

test('שורת האיתותים הטכניים מבודדת כל פריט בנפרד', () => {
  // הבאג שנצפה על המסך: הרצף "MA9 מעל MA20 · RSI ניטרלי" הוצג בעברית
  // כ-"מעל MA9 · MA20 · RSI" — המשתמש קרא "מעל MA9" בזמן שהמחיר היה
  // 7% מתחת ל-MA9. הסיבה: המפריד " · " ניטרלי, ולכן ריצות לטיניות
  // משני צדדיו התמזגו לריצה אחת וסודרו מחדש.
  assert(X.has("esc(sg.rs.map(seg).join(' · '))"),
    'שורת האיתותים חזרה להיות join בלי בידוד');
});

test('אין סימן + צמוד לאחוז בלי בידוד בתוכנית המסחר', () => {
  // "+9.08%" הוצג כ-"9.08%+" — הסימן קפץ לקצה ההפוך
  assert(!X.has("' · +'+t.pct+'%'"), 'יעד ללא בידוד');
  assert(X.has("num('+'+t.pct+'%')"), 'היעד אינו עטוף ב-num');
});

test('יחס סיכוי-סיכון מוצג בסדר שתואם לתווית שלו', () => {
  // היה: תווית "סיכוי/סיכון" עם המספרים בסדר ההפוך — "1:0.9" —
  // כלומר נראה כאילו הסיכוי גדול מהסיכון בזמן שההפך הוא הנכון.
  assert(!X.has("' · סיכוי/סיכון 1:'+t.rr"), 'נשאר הסדר ההפוך');
  assert(X.has("num(t.rr+':1')"), 'היחס אינו מוצג כ-rr:1');
});

/* ---------------------------------------------------------------- */
group('assess — מיקום המחיר מול הממוצעים');

const PRELUDE =
  'const RSI_OVERSOLD=' + jsOversold + ', RSI_OVERBOUGHT=' + jsOverbought + ';\n' +
  X.html.match(/const SECTOR_PE=\{[\s\S]*?\};/)[0] + '\n' +
  X.html.match(/const SECTOR_PE_DEFAULT=\d+;/)[0];
const A = X.load(['assess'], PRELUDE);
// מחירים סביב ממוצעים של 100/98/95/90 כדי שכל תנאי יהיה חד-משמעי
const HEALTHY = () => A.assess(110, 100, 98, 95, 90, 50, 20, 'Technology');
const FALLEN  = () => A.assess(85,  100, 98, 95, 90, 50, 20, 'Technology');

test('מניה מעל כל הממוצעים מקבלת ניקוד חיובי', () => {
  const r = HEALTHY();
  assert(r.rs.includes('המחיר מעל MA20'), 'חסר האיתות "המחיר מעל MA20"');
  assert(r.rs.includes('MA9 מעל MA20'), 'ההצלבה לא נספרה');
  assert(r.score >= 6, 'ציון ' + r.score + ' נמוך מדי למניה במגמה בריאה');
});

test('מחיר מתחת ל-MA20 גורע ניקוד', () => {
  // זה הבאג שנצפה: AAPL ב-303.42 מול MA9 של 326.83 ו-MA20 של 323.91
  // קיבלה "פוטנציאל קנייה" כי נבדק רק היחס בין הממוצעים זה לזה.
  const r = FALLEN();
  assert(r.rs.includes('המחיר מתחת MA20'), 'חסר האיתות "המחיר מתחת MA20"');
  assert(r.score < HEALTHY().score, 'נפילה מתחת ל-MA20 לא הורידה את הציון');
});

test('הצלבה שהמחיר נטש אינה מזכה בניקוד', () => {
  const r = FALLEN();
  const stale = r.rs.some(x => x.startsWith('MA9 מעל MA20 —'));
  assert(stale, 'ההצלבה הישנה לא סומנה כנטושה');
  assert(!r.rs.includes('MA9 מעל MA20'), 'ההצלבה נספרה למרות שהמחיר מתחתיה');
});

test('הבאנר והאות לעולם אינם סותרים זה את זה', () => {
  // שתי מערכות ניקוד נפרדות כבר גרמו פעם ל-"פוטנציאל קנייה" ליד האות C
  [HEALTHY(), FALLEN(), A.assess(50, 100, 98, 95, 90, 75, 60, 'Technology')]
    .forEach(r => {
      const green = r.c === 'green';
      assert(green === (r.l === 'פוטנציאל קנייה'), 'צבע ותווית לא תואמים');
      assert(green === (r.grade === 'A' || r.grade === 'B'),
        'באנר ירוק עם אות ' + r.grade);
    });
});

test('מניה חלשה בכל פרמטר מקבלת D', () => {
  const r = A.assess(50, 90, 100, 95, 120, 75, 60, 'Technology');
  eq(r.grade, 'D', 'אות');
  eq(r.l, 'זהירות — חולשה', 'תווית');
});

test('ממוצעים חסרים אינם מפילים את החישוב', () => {
  const r = A.assess(110, null, null, null, null, null, null, null);
  eq(r.score, 0, 'ציון בלי נתונים');
  assert(r.rs.includes('RSI לא זמין'), 'חסר ציון ל-RSI חסר');
});

test('מגמה מעורבת אינה נספרת לאף צד בקרב ה-AI', () => {
  // "עולה" ו-"יורד" הם המקרים החד-משמעיים. כשהממוצעים אומרים דבר אחד
  // והמחיר אומר אחר, אין למי לתת את הנקודה — וזה עדיף על הכרעה שרירותית.
  const S = X.load(['computeBattleScore'], PRELUDE);
  const base = { rsiNum: 45, weekPos: 50, bullPct: 50, bearPct: 50 };
  const mixed = S.computeBattleScore(
    Object.assign({ trend: 'עולה, אך המחיר מתחת לשני הממוצעים הקצרים' }, base));
  eq(mixed.bullPts, 0, 'נקודה שורית על מגמה מעורבת');
  eq(mixed.bearPts, 0, 'נקודה דובית על מגמה מעורבת');
  eq(S.computeBattleScore(Object.assign({ trend: 'עולה' }, base)).bullPts, 1, 'מגמה עולה');
  eq(S.computeBattleScore(Object.assign({ trend: 'יורד' }, base)).bearPts, 1, 'מגמה יורדת');
});

/* ---------------------------------------------------------------- */
group('הסורק ומסך הניתוח לא מתחזים זה לזה');

test('הסורק אינו מציג אותיות ציון כמו מסך הניתוח', () => {
  // נמדד בפרודקשן: UNH הוצגה בסורק כ-A ובמסך הניתוח כ-D, ו-MSFT כ-C מול B.
  // שני המספרים מודדים דברים שונים לגמרי — ציון הסורק הוא קרבה לפריצה,
  // ולא איכות החברה — ולכן אסור להם לחלוק את אותה שפה חזותית.
  const S = X.load(['scanSignal']);
  const grades = ['A', 'B', 'C', 'D'];
  [0, 1, 5, 10, 20].forEach(score => {
    const out = S.scanSignal({ score, overbought: false });
    assert(!grades.includes(out.g),
      'ציון ' + score + ' החזיר אות ' + out.g + ' — הסורק חזר להתחזות לציון בריאות');
    assert(out.l && out.l.includes('איתות'),
      'התווית "' + out.l + '" לא מבהירה שמדובר בעוצמת איתות');
  });
});

test('מניה מתוחה מסומנת בנפרד ולא כאיתות חזק', () => {
  const S = X.load(['scanSignal']);
  const hot = S.scanSignal({ score: 20, overbought: true });
  eq(hot.g, '!', 'סימון מניה מתוחה');
  assert(hot.l.includes('מתוחה'), 'התווית לא מזכירה שהמניה מתוחה');
});

test('עוצמת האיתות עולה מונוטונית עם הציון', () => {
  const S = X.load(['scanSignal']);
  const len = sc => S.scanSignal({ score: sc, overbought: false }).g.length;
  assert(len(0) <= len(1) && len(1) <= len(5) && len(5) <= len(10),
    'ציון גבוה יותר לא נתן איתות חזק יותר');
});

test('אין אימוג׳י שנטען כתו טקסט מונוכרום', () => {
  // ⚔️ נראה על המסך כמו ✕ צמוד לכותרת המודל — כלומר כמו כפתור סגירה שני.
  // הרשימה כאן היא תווים שברירת המחדל שלהם ביוניקוד היא טקסט ולא אימוג׳י.
  const needsVS = ['⏳', '⚡', '⬇', '⚠', '⏸'];
  needsVS.forEach(ch => {
    const bare = X.html.split(ch).length - 1;
    const dressed = X.html.split(ch + '️').length - 1;
    eq(bare, dressed, 'התו ' + ch + ' מופיע בלי VS16 ולכן עלול להיטען כטקסט');
  });
  assert(!X.has('⚔'), 'הסמל ⚔ חזר לקוד — הוא נראה כמו כפתור סגירה');
});

/* ---------------------------------------------------------------- */
group('דיוק המודלים הסטטיסטיים');

const M = X.load(['computeMonteCarlo', 'computeTwins']);

/* סדרה סינתטית דטרמיניסטית — אותם מספרים בכל הרצה, בלי תלות בשעון או באקראי */
function series(n) {
  const c = [], lb = [];
  let p = 100;
  for (let i = 0; i < n; i++) {
    p *= 1 + (Math.sin(i / 7) * 0.012 + Math.sin(i / 31) * 0.008);
    c.push(parseFloat(p.toFixed(4)));
    lb.push('d' + i);
  }
  return { c, lb };
}

test('אותה מניה באותו מחיר מחזירה בדיוק אותה סימולציה', () => {
  // נמדד בפרודקשן לפני התיקון: שש הרצות על NVDA נתנו אחוזון 90 בין
  // 260.06 ל-270.61 — פער של 4.7% מהמחיר, והמספר הוצג עד רמת הסנט.
  // משתמש שפתח את החלון פעמיים ראה שתי תשובות שונות לאותה שאלה.
  const { c } = series(400);
  const a = M.computeMonteCarlo(c, c[c.length - 1], 3000, 30, 'NVDA');
  const b = M.computeMonteCarlo(c, c[c.length - 1], 3000, 30, 'NVDA');
  const last = a.days.length - 1;
  ['p10', 'p25', 'p50', 'p75', 'p90'].forEach(k =>
    eq(a[k][last], b[k][last], 'האחוזון ' + k + ' השתנה בין שתי הרצות זהות'));
});

test('מניות שונות אינן מקבלות את אותה סימולציה', () => {
  const { c } = series(400);
  const a = M.computeMonteCarlo(c, c[c.length - 1], 3000, 30, 'NVDA');
  const b = M.computeMonteCarlo(c, c[c.length - 1], 3000, 30, 'AMD');
  const last = a.days.length - 1;
  assert(a.p90[last] !== b.p90[last], 'הזרע אינו תלוי בטיקר');
});

test('האחוזונים שומרים על הסדר בכל יום', () => {
  const { c } = series(400);
  const mc = M.computeMonteCarlo(c, c[c.length - 1], 1000, 30, 'T');
  mc.days.forEach((_, d) => {
    assert(mc.p10[d] <= mc.p25[d] && mc.p25[d] <= mc.p50[d]
        && mc.p50[d] <= mc.p75[d] && mc.p75[d] <= mc.p90[d],
      'סדר האחוזונים נשבר ביום ' + d);
  });
});

test('הסימולציה מתחילה מהמחיר הנוכחי בדיוק', () => {
  const { c } = series(400);
  const price = c[c.length - 1];
  const mc = M.computeMonteCarlo(c, price, 500, 10, 'T');
  ['p10', 'p50', 'p90'].forEach(k => eq(mc[k][0], price, 'יום 0 של ' + k));
});

test('היסטוריה קצרה מדי אינה מחזירה סימולציה מדומה', () => {
  const { c } = series(15);
  eq(M.computeMonteCarlo(c, c[c.length - 1], 500, 30, 'T'), null, 'סדרה קצרה');
});

test('שני תקדימים לעולם אינם אותו קטע היסטוריה', () => {
  // נצפה ב-NVDA: מתוך שלושת התקדימים שניים היו 2025-11-28 ו-2025-12-01,
  // כלומר אותו אירוע בהזזה של יום מסחר אחד — ושניהם נספרו גם בממוצע
  // התשואה וגם באחוז ההצלחה, מה שניפח את גודל המדגם.
  const { c, lb } = series(400);
  const tw = M.computeTwins(c, lb, 20, 10, 3);
  assert(tw && tw.top.length === 3, 'לא נמצאו שלושה תקדימים');
  tw.top.forEach((a, i) => tw.top.slice(i + 1).forEach(b => {
    assert(Math.abs(a.start - b.start) >= 20,
      'התקדימים ' + a.startLabel + ' ו-' + b.startLabel + ' חופפים');
  }));
});

test('אחוז ההצלחה נגזר בדיוק מהתקדימים שנבחרו', () => {
  const { c, lb } = series(400);
  const tw = M.computeTwins(c, lb, 20, 10, 3);
  const wins = tw.top.filter(t => t.fwdReturn > 0).length;
  eq(tw.winRate, Math.round(wins / tw.top.length * 100), 'אחוז ניצחון');
  close(tw.avgFwd, tw.top.reduce((s, t) => s + t.fwdReturn, 0) / tw.top.length, 1e-9, 'ממוצע');
});

/* ---------------------------------------------------------------- */
group('שיא ושפל 52 שבועות אינם סותרים את הגרף');

const W = X.load(['reconcile52', 'computeEventBadges'], PRELUDE);

test('שפל שמופיע בגרף גובר על ערך גבוה יותר של הספק', () => {
  // נמדד בפרודקשן: הכרטיס הציג שפל 223.78 בזמן שהגרף באותו מסך ירד ל-218.38
  // (AAPL), 65.35 מול 64.04 (KO). שני מספרים נכונים כל אחד לשיטתו, אבל
  // המשתמש רואה את שניהם יחד ומסיק שהאפליקציה לא מדויקת.
  eq(W.reconcile52(223.78, [230, 218.38, 240], 'min'), 218.38, 'שפל');
  eq(W.reconcile52(553.72, [500, 550.24, 510], 'max'), 553.72, 'שיא של הספק גבוה יותר');
  eq(W.reconcile52(540, [500, 550.24, 510], 'max'), 550.24, 'שיא מהגרף גבוה יותר');
});

test('נופל בחזרה על מקור אחד כשהשני חסר', () => {
  eq(W.reconcile52(null, [10, 5, 8], 'min'), 5, 'בלי ערך ספק');
  eq(W.reconcile52(7, [], 'min'), 7, 'בלי היסטוריה');
  eq(W.reconcile52(null, [], 'min'), null, 'בלי שניהם');
  eq(W.reconcile52(0, [10, 5], 'min'), 5, 'אפס מהספק אינו שפל אמיתי');
});

test('מתעלם מערכים חסרים בתוך הסדרה', () => {
  eq(W.reconcile52(100, [null, 90, undefined, NaN, 80], 'min'), 80, 'ערכים חסרים');
});

test('תג שפל 52 שבועות נדלק רק בתחתית אמיתית', () => {
  // עם השפל של הספק בלבד (223.78) מחיר 223 היה מדליק "שפל 52 שבועות"
  // למרות שהמניה ירדה ל-218 באותה שנה — תג שקרי.
  const low = W.reconcile52(223.78, [230, 218.38, 240], 'min');
  const at223 = W.computeEventBadges(223, 400, low, 222, null, null, 50);
  assert(!at223.some(b => b.text === 'שפל 52 שבועות'), 'תג שפל שקרי');
  const at218 = W.computeEventBadges(218, 400, low, 222, null, null, 50);
  assert(at218.some(b => b.text === 'שפל 52 שבועות'), 'תג שפל אמיתי לא נדלק');
});
