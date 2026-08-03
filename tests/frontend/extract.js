/* טוען את פונקציות הפרונטאנד מתוך index.html בלי לשנות שום דבר בפרודקשן.
 *
 * הרעיון: index.html הוא קובץ אחד גדול, וכל הלוגיקה יושבת בתוך תגי <script>.
 * במקום לפרק אותו (שינוי מסוכן), אנחנו שולפים ממנו את הפונקציות הטהורות
 * ומריצים אותן כאן. כך אפשר לבדוק את הלוגיקה של הדפדפן במהירות, בלי לפרוס
 * ובלי דפדפן — וזה בדיוק מה שהיה חסר כשאבחנתי לא נכון את המקרה של INTC.
 */
'use strict';
const fs = require('fs');
const path = require('path');

function findIndexHtml() {
  const candidates = [
    process.env.INDEX_HTML,
    path.join(__dirname, '..', '..', 'site', 'index.html'),   // CI: gh-pages נמשך ל-site/
    path.join(__dirname, '..', '..', '..', 'repo', 'index.html'), // פיתוח מקומי
  ].filter(Boolean);
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  throw new Error(
    'index.html לא נמצא. נוסו: ' + candidates.join(', ') +
    '\nאפשר להצביע עליו ידנית עם משתנה הסביבה INDEX_HTML.'
  );
}

const INDEX_PATH = findIndexHtml();
const html = fs.readFileSync(INDEX_PATH, 'utf8');

/* שליפת הצהרת פונקציה לפי שם, כולל גוף מאוזן בסוגריים מסולסלים. */
function extractFunction(name) {
  const start = html.indexOf('function ' + name + '(');
  if (start === -1) throw new Error('הפונקציה ' + name + ' לא נמצאה ב-index.html');
  let i = html.indexOf('{', start);
  if (i === -1) throw new Error('לא נמצא גוף לפונקציה ' + name);
  let depth = 0;
  for (; i < html.length; i++) {
    const ch = html[i];
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return html.slice(start, i + 1);
    }
  }
  throw new Error('גוף לא מאוזן בפונקציה ' + name);
}

/* שליפת קבוע מספרי שמוגדר ברמה העליונה, למשל: const RSI_OVERSOLD=30, ... */
function extractNumericConst(name) {
  const re = new RegExp('\\b' + name + '\\s*=\\s*(-?\\d+(?:\\.\\d+)?)');
  const m = html.match(re);
  if (!m) throw new Error('הקבוע ' + name + ' לא נמצא ב-index.html');
  return parseFloat(m[1]);
}

function has(snippet) {
  return html.includes(snippet);
}

/* בונה סביבה מבודדת עם הפונקציות המבוקשות. */
function load(names) {
  const src = names.map(extractFunction).join('\n');
  const factory = new Function(src + '\nreturn {' + names.join(',') + '};');
  return factory();
}

module.exports = { load, extractFunction, extractNumericConst, has, html, INDEX_PATH };
