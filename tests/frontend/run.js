/* רתמת בדיקות לפרונטאנד — ללא תלויות, רץ ב-node בלבד.
 * הרצה:  node tests/frontend/run.js
 */
'use strict';

let passed = 0;
const failures = [];
let currentGroup = '';

function group(name) {
  currentGroup = name;
  console.log('\n' + name);
}

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log('  ✓ ' + name);
  } catch (err) {
    failures.push({ group: currentGroup, name, message: err.message });
    console.log('  ✗ ' + name + '\n      ' + err.message);
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || 'הציפייה נכשלה');
}

function eq(actual, expected, msg) {
  if (actual !== expected) {
    throw new Error((msg || 'אי-התאמה') + ': התקבל ' + JSON.stringify(actual) +
      ', צפוי ' + JSON.stringify(expected));
  }
}

function close(actual, expected, tol, msg) {
  if (Math.abs(actual - expected) > tol) {
    throw new Error((msg || 'אי-התאמה מספרית') + ': התקבל ' + actual +
      ', צפוי ~' + expected);
  }
}

function summary() {
  console.log('\n' + '='.repeat(58));
  if (failures.length === 0) {
    console.log(passed + ' בדיקות עברו');
    process.exit(0);
  }
  console.log(passed + ' עברו, ' + failures.length + ' נכשלו:');
  failures.forEach(f => console.log('  - [' + f.group + '] ' + f.name + ': ' + f.message));
  process.exit(1);
}

module.exports = { group, test, assert, eq, close, summary };

if (require.main === module) {
  require('./cases.js');
  summary();
}
