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

const W = X.load(['reconcile52', 'computeEventBadges', 'widenLongRange'], PRELUDE);

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

/* ---------------------------------------------------------------- */
group('הטווח הארוך מכיל את הטווח הקצר');

test('שיא של 5 שנים לעולם אינו נמוך מהשיא של 52 השבועות', () => {
  // נמדד בפרודקשן על 12 מניות: ב-4 מהן הסדרה השבועית החזירה שיא נמוך
  // יותר מהשיא השנתי שמוצג בכרטיס באותו מסך — מצב בלתי אפשרי לוגית.
  eq(W.widenLongRange(344.27, 344.57, 'max'), 344.57, 'AAPL');
  eq(W.widenLongRange(551.05, 553.72, 'max'), 553.72, 'MSFT');
  eq(W.widenLongRange(236.26, 236.54, 'max'), 236.54, 'NVDA');
  eq(W.widenLongRange(408.37, 408.61, 'max'), 408.61, 'GOOGL');
});

test('שיא ארוך אמיתי שגבוה מהשנתי נשאר על כנו', () => {
  // BA נמדד עם 267.54 בחמש שנים מול 254.35 בשנה — זה המצב התקין
  eq(W.widenLongRange(267.54, 254.35, 'max'), 267.54, 'BA');
  eq(W.widenLongRange(793.65, 790.8, 'max'), 793.65, 'META');
});

test('השפל הארוך לעולם אינו גבוה מהשפל השנתי', () => {
  eq(W.widenLongRange(121.99, 100.5, 'min'), 100.5, 'שפל שנתי נמוך יותר');
  eq(W.widenLongRange(121.99, 222.96, 'min'), 121.99, 'שפל ארוך נמוך יותר');
});

test('נופל בחזרה על מקור אחד כשהשני חסר', () => {
  eq(W.widenLongRange(null, 344.57, 'max'), 344.57, 'בלי טווח ארוך');
  eq(W.widenLongRange(344.27, null, 'max'), 344.27, 'בלי טווח שנתי');
  eq(W.widenLongRange(null, null, 'max'), null, 'בלי שניהם');
  eq(W.widenLongRange(344.27, 0, 'max'), 344.27, 'אפס אינו שיא אמיתי');
  eq(W.widenLongRange(344.27, NaN, 'max'), 344.27, 'NaN אינו שיא אמיתי');
});

test('אזהרת שיא רב-שנתי אינה נדלקת מוקדם מדי', () => {
  // הבאג: ltHigh=344.27 בזמן שהשיא השנתי 344.57. מחיר 344.30 עבר את
  // הסף של 0.999 והדליק "שיא של חמש שנים" בעוד המניה מתחת לשיא השנתי.
  const before = 344.27, after = W.widenLongRange(344.27, 344.57, 'max');
  const price = 344.30;
  assert(price >= before * 0.999, 'לפני התיקון התג היה נדלק');
  assert(!(price >= after), 'אחרי התיקון המחיר עדיין מתחת לשיא האמיתי');
});

/* ---------------------------------------------------------------- */
group('התבניות בסורק — תיאור, לא איתות');

const P = X.load(['patternNames', 'scanFreshness'], "function seg(v){return v==null||v===''?v:'\u2067'+v+'\u2069';}");

const ROWS = [
  { ticker: 'AAPL', patterns: [{ name: 'תחתית כפולה', dir: 'up', detail: 'שפל ב-100 ושפל ב-101' }] },
  { ticker: 'MSFT', patterns: [{ name: 'תחתית כפולה', dir: 'up', detail: 'x' },
                               { name: 'משולש עולה', dir: 'up', detail: 'y' }] },
  { ticker: 'KO',   patterns: [] },
  { ticker: 'BA' },
];

test('כל תבנית נספרת פעם אחת לכל מניה', () => {
  const n = P.patternNames(ROWS);
  eq(JSON.stringify(n), JSON.stringify([['תחתית כפולה', 2], ['משולש עולה', 1]]), 'ספירה');
});

test('התבניות מסודרות מהנפוצה לנדירה', () => {
  const n = P.patternNames(ROWS);
  assert(n[0][1] >= n[1][1], 'הסדר אינו יורד');
});

test('מניות בלי תבניות אינן שוברות את הספירה', () => {
  eq(JSON.stringify(P.patternNames([{ ticker: 'X' }])), '[]', 'בלי שדה');
  eq(JSON.stringify(P.patternNames([])), '[]', 'רשימה ריקה');
  eq(JSON.stringify(P.patternNames(null)), '[]', 'null');
});

test('גיל הנתונים מוצג ולא מוסתר', () => {
  // השרת מחזיר תשובה שפג תוקפה כדי לא להשאיר את המשתמש 11 שניות מול
  // מסך טעינה. להציג נתון בן שבע דקות כאילו הוא של עכשיו זו תצוגת שווא.
  assert(P.scanFreshness({ age: 420 }).includes('7 דקות'), 'חסר הגיל בדקות');
  assert(P.scanFreshness({ age: 30 }).includes('פחות מדקה'), 'גיל קצר');
  assert(P.scanFreshness({ age: 420 }).includes('מתעדכנים ברקע'), 'חסר ההסבר');
});

test('הניסוח תקין בעברית בכל טווח', () => {
  // Math.round הפך 30 שניות ל"לפני 1 דקות" — מספר שגוי ועברית שגויה
  assert(!/לפני 1 דקות/.test(P.scanFreshness({ age: 90 })), 'צורת יחיד שבורה');
  assert(P.scanFreshness({ age: 90 }).includes('מלפני דקה'), 'דקה אחת');
  assert(P.scanFreshness({ age: 150 }).includes('שתי דקות'), 'שתי דקות');
  assert(P.scanFreshness({ age: 200 }).includes('3 דקות'), 'שלוש ומעלה');
});

test('נתון טרי אינו מקבל שום הערה', () => {
  eq(P.scanFreshness({}), '', 'בלי age');
  eq(P.scanFreshness({ age: null }), '', 'age ריק');
});

/* ---------------------------------------------------------------- */
group('שדרוג מעטפת אינו משאיר מסך לבן');

const SW = X.load(['swShouldReload']);

test('לשונית שרצה עם מעטפת ישנה מתרעננת כשחדשה משתלטת', () => {
  // נצפה בפועל בנייד אחרי המעבר v34→v36: ה-Service Worker קורא ל-skipWaiting
  // ואז ל-clients.claim ומוחק את המטמונים הישנים, כלומר מעטפת חדשה משתלטת על
  // דף שעדיין מריץ HTML ישן בדיוק כשהנכסים שלו נמחקים. התוצאה הייתה מסך לבן
  // שנפתר רק בניקוי ידני של נתוני האתר.
  assert(SW.swShouldReload(true, false), 'שדרוג חייב לגרור רענון');
});

test('מבקר ראשון אינו חוטף רענון מיותר', () => {
  // בהתקנה ראשונה controllerchange נורה גם הוא, אבל אין מעטפת קודמת
  // ולכן אין מה לסנכרן — רענון שם הוא סתם הבהוב לכל מבקר חדש
  assert(!SW.swShouldReload(false, false), 'התקנה ראשונה אינה שדרוג');
});

test('אין לולאת רענון אינסופית', () => {
  // הרענון עצמו עלול להצית controllerchange נוסף. בלי הדגל הדף
  // היה נכנס ללולאה ומעולם לא מסיים להיטען — גרוע ממסך לבן.
  assert(!SW.swShouldReload(true, true), 'רענון שכבר רץ לא מתחיל עוד אחד');
  assert(!SW.swShouldReload(false, true), 'שני השומרים יחד');
});

test('ערכים חסרים אינם מפילים את ההחלטה', () => {
  eq(SW.swShouldReload(undefined, false), false, 'controller לא ידוע');
  eq(SW.swShouldReload(null, false), false, 'controller ריק');
});

/* ---------------------------------------------------------------- */
group('שרשרת היעדים מרוסנת');

const T = X.load(['trimTargets'],
  'const MAX_TARGET_R=' + X.extractNumericConst('MAX_TARGET_R') +
  ', MAX_TARGETS=' + X.extractNumericConst('MAX_TARGETS') + ';');

// שרשרת NKE האמיתית שנמדדה בפרודקשן: מחיר 42.39, סטופ 40.56, סיכון 4.32%
const NKE = [
  { p: 45.01, pct: 6.18,   rr: 1.43 },
  { p: 46.70, pct: 10.17,  rr: 2.36 },
  { p: 59.85, pct: 41.19,  rr: 9.54 },
  { p: 63.02, pct: 48.67,  rr: 11.27 },
  { p: 65.78, pct: 55.18,  rr: 12.78 },
  { p: 67.69, pct: 59.68,  rr: 13.83 },
  { p: 77.99, pct: 83.98,  rr: 19.45 },
  { p: 165.15, pct: 289.6, rr: 67.08 },
];

test('היעד של 290% נעלם', () => {
  // "יעד 8 (שיא 5 שנים): $165.15 · +289.6% · סיכוי מול סיכון 67.08:1" —
  // רמה שרחוקה שנים, שהוצגה כיעד לעסקה עם יחס שקורא לזה הימור משתלם
  const kept = T.trimTargets(NKE);
  assert(!kept.some(t => t.pct > 30), 'שרד יעד מופרך: ' + JSON.stringify(kept.map(t => t.pct)));
  eq(kept.length, 2, 'מספר היעדים ל-NKE');
  eq(kept[kept.length - 1].pct, 10.17, 'היעד הרחוק ביותר ששרד');
});

test('שרשרת ארוכה נחתכת לשלושה יעדים לכל היותר', () => {
  const many = [1, 2, 2.5, 3, 3.5, 4].map((rr, i) => ({ p: i, pct: i, rr: rr }));
  eq(T.trimTargets(many).length, 3, 'תקרת מספר היעדים');
});

test('יעדים סבירים אינם נפגעים', () => {
  // שש מתוך 13 המניות שנמדדו לא השתנו כלל אחרי הקיצוץ
  const sane = [{ p: 1, pct: 4.9, rr: 1.2 }];
  eq(T.trimTargets(sane).length, 1, 'AAPL נשארת כשהייתה');
});

test('מניה בלי אף יעד מתחת לתקרה מקבלת רשימה ריקה', () => {
  // INTC: יעד יחיד של +69.3% מעל 5 יחידות סיכון. "אין יעד סביר"
  // הוא תשובה נכונה יותר מיעד מומצא.
  eq(T.trimTargets([{ p: 1, pct: 69.33, rr: 8.4 }]).length, 0, 'INTC');
});

test('הסף הוא בדיוק חמש יחידות סיכון', () => {
  // נבחר במדידה: ב-5 היעד הגרוע ביותר הוא 25.8%, ב-6 הוא קופץ ל-69.3%
  eq(T.trimTargets([{ p: 1, pct: 20, rr: 5 }]).length, 1, 'בדיוק על הסף נשאר');
  eq(T.trimTargets([{ p: 1, pct: 20, rr: 5.01 }]).length, 0, 'מעל הסף יוצא');
});

test('יעד בלי יחס מחושב אינו נזרק', () => {
  // riskAmt<=0 משאיר rr ריק, וזו אינה סיבה למחוק את היעד
  eq(T.trimTargets([{ p: 1, pct: 5, rr: null }]).length, 1, 'rr ריק');
});

test('קלט פגום אינו מפיל את התוכנית', () => {
  eq(T.trimTargets([]).length, 0, 'רשימה ריקה');
  eq(T.trimTargets(null).length, 0, 'null');
  eq(T.trimTargets([null, { p: 1, pct: 5, rr: 1 }]).length, 1, 'איבר ריק');
});

/* ── התדריך שלנו לידיעה ─────────────────────────────────────────────
 * הכלל שנשמר כאן: מה שמוצג אינו הכתבה. פסקה שאנחנו מנסחים, מספרים
 * שאנחנו מחשבים, וקישור למקור — תמיד.
 */
group('התדריך לידיעה — שלנו, לא של המפרסם');
{
  // safeUrl נשען על location של הדפדפן — כאן מספיק בסיס כלשהו
  const N = X.load(['briefHtml', 'nextOpenBrief', 'briefWorthCaching',
                    'esc', 'seg', 'safeUrl'],
                   "var location={href:'https://stockiq.example/'};");

  const FULL = {
    what: 'מחירי הנפט עלו על רקע חשש להיצע.',
    impact: [{ ticker: 'XOM', lines: ['המניה נסחרת ב-$110 — עלייה של 1.2% מאז הסגירה הקודמת.',
                                      'המחיר נמצא ב-66% מטווח 52 השבועות (שפל $90, שיא $120).'] }],
    source: 'Reuters',
    url: 'https://reuters.com/x'
  };

  test('הפסקה שלנו מוצגת', () => {
    assert(N.briefHtml(FULL).includes('מחירי הנפט עלו'));
  });

  test('המספרים שלנו מוצגים תחת הטיקר', () => {
    const h = N.briefHtml(FULL);
    assert(h.includes('XOM'), 'הטיקר חייב להופיע');
    assert(h.includes('52 השבועות'), 'שורת הטווח חייבת להופיע');
  });

  test('תמיד יש קישור לכתבה המקורית', () => {
    const h = N.briefHtml(FULL);
    assert(h.includes('https://reuters.com/x'), 'הקישור למקור הוא חובה');
    assert(h.includes('Reuters'), 'שם המקור מופיע בקישור');
  });

  test('גם כשאין תדריך — הקישור למקור נשאר', () => {
    const h = N.briefHtml({ what: '', impact: [], source: 'Reuters', url: 'https://reuters.com/x' });
    assert(h.includes('לא הצלחנו'), 'חייבת להיות הודעה במקום פאנל ריק');
    assert(h.includes('https://reuters.com/x'), 'הקישור נשאר גם בכישלון');
  });

  test('כשיש תוכן, ההסתייגות על מקור המספרים מוצגת', () => {
    const h = N.briefHtml(FULL);
    assert(h.includes('נכתב אצלנו'), 'הקורא חייב לדעת שהטקסט שלנו');
    assert(h.includes('לא מהכתבה'), 'והמספרים אינם מהכתבה');
  });

  test('בלי תוכן אין הסתייגות שמתייחסת לתוכן שאינו קיים', () => {
    const h = N.briefHtml({ what: '', impact: [], url: '' });
    assert(!h.includes('נכתב אצלנו'));
  });

  test('טקסט מהשרת עובר בריחת תווים', () => {
    const h = N.briefHtml({ what: '<img src=x onerror=alert(1)>', impact: [], url: '' });
    assert(!h.includes('<img'), 'אסור שתגית מהשרת תיכנס ל-DOM');
    assert(h.includes('&lt;img'), 'היא חייבת להופיע כטקסט');
  });

  test('כתובת שאינה http נחסמת', () => {
    const h = N.briefHtml({ what: 'א', impact: [], url: 'javascript:alert(1)' });
    assert(!h.includes('javascript:'), 'סכימה מסוכנת לא מגיעה ל-href');
  });

  test('שורות עטופות בבידוד דו-כיווני', () => {
    // בלי זה "$110 — עלייה של 1.2%" מוצג הפוך במסך RTL
    const h = N.briefHtml(FULL);
    assert(h.includes('⁧'), 'שורה מעורבת עברית+לטינית חייבת בידוד');
  });

  test('מבנה חלקי מהשרת אינו מפיל את התצוגה', () => {
    assert(typeof N.briefHtml(null) === 'string');
    assert(typeof N.briefHtml({}) === 'string');
    assert(typeof N.briefHtml({ impact: 'לא מערך' }) === 'string');
    assert(typeof N.briefHtml({ impact: [{ ticker: 'A' }] }) === 'string');
  });

  test('פריט השפעה בלי שורות אינו מייצר כותרת ריקה', () => {
    const h = N.briefHtml({ what: 'א', impact: [{ ticker: 'ZZZ', lines: [] }], url: '' });
    assert(!h.includes('ZZZ'));
  });

  test('לחיצה חוזרת על אותה ידיעה סוגרת אותה', () => {
    eq(N.nextOpenBrief(null, 'a'), 'a');
    eq(N.nextOpenBrief('a', 'a'), null);
    eq(N.nextOpenBrief('a', 'b'), 'b');
  });

  test('תדריך ריק אינו נשמר במטמון', () => {
    // אחרת כישלון רגעי ננעל עד לרענון הדף
    eq(N.briefWorthCaching({ what: '', impact: [] }), false);
    eq(N.briefWorthCaching(null), false);
    eq(N.briefWorthCaching({ what: 'א', impact: [] }), true);
    eq(N.briefWorthCaching({ what: '', impact: [{ ticker: 'A', lines: ['x'] }] }), true);
  });
}

group('החדשות מובילות לתדריך ולא החוצה');
{
  test('כרטיס עם מזהה פותח תדריך במקום לנווט', () => {
    assert(X.has('card.addEventListener(\'click\',()=>toggleBrief(n,card,panel))'),
      'הלחיצה חייבת לפתוח את הפאנל');
  });

  test('רענון החדשות מאפס את הסימון הפתוח', () => {
    // הפאנלים נמחקים עם ה-innerHTML; בלי איפוס, לחיצה ראשונה אחרי רענון
    // הייתה נחשבת "סגירה" ולא הייתה פותחת כלום
    assert(X.has('_openBrief=null;'), 'הסימון חייב להתאפס יחד עם הכרטיסים');
  });
}

/* ── התאום: מספר נכון בהקשר שהופך אותו למטעה ─────────────────────────
 * המדידה שלנו: 20 מניות, 2,344 מקרים, פגיעה בכיוון 50.8% מול 54.1%
 * למי שהימר תמיד על עלייה. לכן הסטטיסטיקה נשארת על המסך רק לצד שיעור
 * הבסיס ולצד המשפט שאומר שאין כאן יתרון.
 */
group('התאום — סטטיסטיקה עם הקשר, לא איתות');
{
  const TW = X.load(['computeTwins', 'twinNoEdgeText', 'num'],
                    "var TWIN_STUDY={stocks:20,cases:2344,hit:50.8,base:54.1};");

  function series(n) {
    const c = [], lb = [];
    let p = 100;
    for (let i = 0; i < n; i++) {
      p *= 1 + (Math.sin(i / 3) + Math.cos(i / 7)) / 100;
      c.push(p);
      lb.push('d' + i);
    }
    return { c, lb };
  }

  test('שיעור הבסיס של המניה מוחזר לצד הממוצע', () => {
    const s = series(300);
    const tw = TW.computeTwins(s.c, s.lb, 20, 10, 3);
    assert(tw !== null, 'צריך להימצא תקדים');
    assert(typeof tw.baseFwd === 'number', 'baseFwd חייב להיות מספר');
    assert(typeof tw.baseWin === 'number', 'baseWin חייב להיות מספר');
  });

  test('שיעור הבסיס מחושב על כל חלונות המניה', () => {
    const s = series(300);
    const tw = TW.computeTwins(s.c, s.lb, 20, 10, 3);
    let sum = 0, cnt = 0;
    for (let i = 0; i + 10 < s.c.length; i++) { sum += (s.c[i + 10] / s.c[i] - 1) * 100; cnt++; }
    close(tw.baseFwd, sum / cnt, 1e-9, 'baseFwd');
  });

  test('מספר התקדימים החיוביים מוחזר כספירה ולא רק כאחוז', () => {
    // "100%" מתוך שלושה נשמע כמו ידע; "3 מתוך 3" הוא מה שבאמת נמדד
    const s = series(300);
    const tw = TW.computeTwins(s.c, s.lb, 20, 10, 3);
    eq(tw.hits, tw.top.filter(c => c.fwdReturn > 0).length, 'hits');
    assert(tw.hits <= tw.top.length, 'אי אפשר יותר חיוביים מתקדימים');
  });

  test('הממוצע והספירה עקביים זה עם זה', () => {
    const s = series(300);
    const tw = TW.computeTwins(s.c, s.lb, 20, 10, 3);
    close(tw.avgFwd, tw.top.reduce((a, c) => a + c.fwdReturn, 0) / tw.top.length, 1e-9, 'avgFwd');
    eq(tw.winRate, Math.round(tw.hits / tw.top.length * 100), 'winRate');
  });

  test('המשפט המדוד נוקב במספרים האמיתיים של המחקר', () => {
    const t = TW.twinNoEdgeText();
    assert(t.includes('50.8'), 'שיעור הפגיעה חייב להופיע');
    assert(t.includes('54.1'), 'שיעור הבסיס חייב להופיע');
    assert(t.includes('2,344') || t.includes('2344'), 'מספר המקרים חייב להופיע');
  });

  test('המשפט אומר במפורש שאין יתרון', () => {
    const t = TW.twinNoEdgeText();
    assert(t.includes('אין יתרון'), 'זו כל הנקודה של המשפט');
    assert(!t.includes('חיזוי אמין') && !t.includes('מנבא'), 'אסור שיישמע כמו הבטחה');
  });

  test('המסך מציג את שיעור הבסיס ואת המשפט', () => {
    assert(X.has('חלון אקראי באותה מניה'), 'הממוצע חייב להופיע מול שיעור בסיס');
    assert(X.has('twinNoEdgeText()'), 'המשפט המדוד חייב להיות מוצג');
    assert(X.has('היו חיוביים'), 'ספירה במקום אחוז מתוך שלושה');
  });

  test('אחוז ניצחון עירום כבר אינו מוצג ככותרת', () => {
    // הניסוח הישן: "אחוז ניצחון: 100%" על שלושה מקרים
    assert(!X.has('אחוז ניצחון: <b>'), 'הניסוח המטעה הוסר');
  });
}

group('התקדים נוסע ל-AI עם שיעור הבסיס');
{
  test('twinStats כולל את שיעור הבסיס', () => {
    assert(X.has('baseFwd:(tw.baseFwd!=null)?parseFloat(tw.baseFwd.toFixed(1)):null'),
      'בלי זה השרת מקבל מספר בלי נקודת השוואה');
  });

  test('הגוף שנשלח ל-AI כולל twinBaseFwd', () => {
    assert(X.has('twinBaseFwd:twinStats?twinStats.baseFwd:null'));
  });
}

/* ── חדשות רשימת המעקב ──────────────────────────────────────────────
 * כאן לכל ידיעה יש מניה ידועה, ולכן שורת המטא מציגה אותה, והתדריך
 * שנפתח מגיע עם המספרים של המניה הנכונה.
 */
group('חדשות רשימת המעקב');
{
  const W = X.load(['newsAgeText', 'newsMetaText']);

  const HOUR = 3600000;
  function ts(hoursAgo) { return Math.floor((Date.now() - hoursAgo * HOUR) / 1000); }

  test('גיל הידיעה מנוסח בעברית תקינה', () => {
    eq(W.newsAgeText(ts(0.5)), 'לפני פחות משעה');
    eq(W.newsAgeText(ts(5)), 'לפני 5 שעות');
    eq(W.newsAgeText(ts(50)), 'לפני 2 ימים');
  });

  test('חותמת זמן חסרה לא מייצרת טקסט שבור', () => {
    // null*1000 הוא 0, ולפני התיקון זה הוצג כ-"לפני 20692 ימים"
    eq(W.newsAgeText(undefined), '');
    eq(W.newsAgeText(null), '');
    eq(W.newsAgeText(0), '');
    eq(W.newsAgeText('1700000000'), '');
  });

  test('שורת המטא מציגה את המניה שהידיעה שייכת לה', () => {
    const m = W.newsMetaText({ datetime: ts(3), source: 'Reuters', tickers: ['NVDA'] });
    assert(m.includes('NVDA'), 'הטיקר הוא כל ההבדל מהפיד הכללי');
    assert(m.includes('Reuters'));
    assert(m.includes('לפני 3 שעות'));
  });

  test('שתי מניות על אותה ידיעה מוצגות שתיהן', () => {
    const m = W.newsMetaText({ datetime: ts(1), source: 'Reuters', tickers: ['AAPL', 'MSFT'] });
    assert(m.includes('AAPL') && m.includes('MSFT'));
  });

  test('ידיעה בלי מניה לא מייצרת מפריד מיותם', () => {
    const m = W.newsMetaText({ datetime: ts(2), source: 'CNBC' });
    assert(!m.endsWith('·') && !m.includes('· ·'), 'התקבל: ' + m);
  });

  test('שדות חסרים לגמרי אינם מפילים את השורה', () => {
    eq(typeof W.newsMetaText({}), 'string');
    eq(typeof W.newsMetaText(null), 'string');
  });

  test('שני הפידים משתמשים באותו כרטיס', () => {
    // שני מסכים שמציגים את אותו דבר בשתי צורות נפרדים זה מזה עם הזמן
    assert(X.has('items.forEach(n=>appendNewsCard(box,n));'), 'הפיד הכללי');
    assert(X.has('items.forEach(n=>appendNewsCard(box,n));'), 'פיד המעקב');
    assert(X.has('function appendNewsCard(box,n)'), 'הכרטיס מוגדר פעם אחת');
  });

  test('מסך המעקב טוען את החדשות', () => {
    assert(X.has('renderWL();loadWatchlistNews();'));
  });

  test('הסרת מניה מרעננת את החדשות', () => {
    assert(X.has('renderWL();loadWatchlistNews();}'), 'אחרת נשארות ידיעות על מניה שהוסרה');
  });

  test('רשימה ריקה אינה שולחת בקשה לשרת', () => {
    assert(X.has("הוסף מניות לרשימה כדי לראות חדשות עליהן"));
  });
}
