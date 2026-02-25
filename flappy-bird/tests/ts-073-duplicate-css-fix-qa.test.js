/**
 * TS-073: QA Verification for CD-067 — Duplicate CSS Property Fix
 *
 * Verifies that commit 39545d8 correctly removed duplicate CSS properties
 * (max-width, max-height, box-shadow) from the canvas rule in style.css
 * that were silently introduced during the CD-017 merge (b3511ac).
 */

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STYLE_PATH = path.join(__dirname, '..', 'style.css');
const INDEX_PATH = path.join(__dirname, '..', 'index.html');

let css, html;
let passed = 0;
let failed = 0;
let skipped = 0;
const failures = [];

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ✅ PASS: ${name}`);
  } catch (err) {
    failed++;
    failures.push({ name, error: err.message });
    console.log(`  ❌ FAIL: ${name}`);
    console.log(`          ${err.message}`);
  }
}

function skip(name, reason) {
  skipped++;
  console.log(`  ⏭️  SKIP: ${name} — ${reason}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message || 'Assertion failed');
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message || 'Assertion failed'}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────

function parseCSSRules(cssText) {
  const rules = [];
  const ruleRegex = /([^{}]+)\{([^}]+)\}/g;
  let match;
  while ((match = ruleRegex.exec(cssText)) !== null) {
    const selector = match[1].trim();
    const body = match[2].trim();
    const properties = body
      .split(';')
      .map(p => p.trim())
      .filter(p => p && p.includes(':'))
      .map(p => {
        const colonIdx = p.indexOf(':');
        return {
          name: p.slice(0, colonIdx).trim(),
          value: p.slice(colonIdx + 1).trim(),
        };
      });
    rules.push({ selector, properties });
  }
  return rules;
}

function findRule(rules, selector) {
  return rules.find(r => r.selector === selector);
}

function getDuplicateProperties(properties) {
  const seen = {};
  const dupes = [];
  for (const prop of properties) {
    if (seen[prop.name]) {
      dupes.push(prop.name);
    }
    seen[prop.name] = true;
  }
  return dupes;
}

// ─── Setup ────────────────────────────────────────────────────────────────

console.log('');
console.log('═══════════════════════════════════════════════════════════');
console.log('  TS-073: QA Verification — CD-067 Duplicate CSS Fix');
console.log('═══════════════════════════════════════════════════════════');
console.log('');

try {
  css = fs.readFileSync(STYLE_PATH, 'utf8');
} catch (err) {
  console.error(`FATAL: Cannot read ${STYLE_PATH}: ${err.message}`);
  process.exit(1);
}

try {
  html = fs.readFileSync(INDEX_PATH, 'utf8');
} catch (err) {
  console.error(`FATAL: Cannot read ${INDEX_PATH}: ${err.message}`);
  process.exit(1);
}

const rules = parseCSSRules(css);

// ═══════════════════════════════════════════════════════════════════════════
// GROUP 1: File Existence & Loading
// ═══════════════════════════════════════════════════════════════════════════
console.log('── Group 1: File Existence ──────────────────────────────');

test('style.css exists and is readable', () => {
  assert(css.length > 0, 'style.css is empty');
});

test('index.html exists and is readable', () => {
  assert(html.length > 0, 'index.html is empty');
});

// ═══════════════════════════════════════════════════════════════════════════
// GROUP 2: Overall CSS Structure
// ═══════════════════════════════════════════════════════════════════════════
console.log('');
console.log('── Group 2: CSS Structure ──────────────────────────────');

test('CSS has exactly 3 rules', () => {
  assertEqual(rules.length, 3, 'Rule count mismatch');
});

test('Rule 1 selector is * (universal reset)', () => {
  assertEqual(rules[0].selector, '*', 'First rule selector mismatch');
});

test('Rule 2 selector is body', () => {
  assertEqual(rules[1].selector, 'body', 'Second rule selector mismatch');
});

test('Rule 3 selector is canvas', () => {
  assertEqual(rules[2].selector, 'canvas', 'Third rule selector mismatch');
});

test('Rules appear in correct order: *, body, canvas', () => {
  const selectors = rules.map(r => r.selector);
  assertEqual(JSON.stringify(selectors), JSON.stringify(['*', 'body', 'canvas']),
    'Rule order mismatch');
});

// ═══════════════════════════════════════════════════════════════════════════
// GROUP 3: Universal Reset Rule (*)
// ═══════════════════════════════════════════════════════════════════════════
console.log('');
console.log('── Group 3: Universal Reset Rule (*) ──────────────────');

const starRule = findRule(rules, '*');

test('* rule has 3 properties', () => {
  assertEqual(starRule.properties.length, 3, 'Property count mismatch');
});

test('* rule has margin: 0', () => {
  const prop = starRule.properties.find(p => p.name === 'margin');
  assert(prop, 'margin property not found');
  assertEqual(prop.value, '0', 'margin value mismatch');
});

test('* rule has padding: 0', () => {
  const prop = starRule.properties.find(p => p.name === 'padding');
  assert(prop, 'padding property not found');
  assertEqual(prop.value, '0', 'padding value mismatch');
});

test('* rule has box-sizing: border-box', () => {
  const prop = starRule.properties.find(p => p.name === 'box-sizing');
  assert(prop, 'box-sizing property not found');
  assertEqual(prop.value, 'border-box', 'box-sizing value mismatch');
});

test('* rule has no duplicate properties', () => {
  const dupes = getDuplicateProperties(starRule.properties);
  assertEqual(dupes.length, 0, `Duplicates found: ${dupes.join(', ')}`);
});

// ═══════════════════════════════════════════════════════════════════════════
// GROUP 4: Body Rule
// ═══════════════════════════════════════════════════════════════════════════
console.log('');
console.log('── Group 4: Body Rule ──────────────────────────────────');

const bodyRule = findRule(rules, 'body');

test('body rule has 8 properties', () => {
  assertEqual(bodyRule.properties.length, 8, 'Property count mismatch');
});

test('body rule has background: #2c2c2c', () => {
  const prop = bodyRule.properties.find(p => p.name === 'background');
  assert(prop, 'background property not found');
  assertEqual(prop.value, '#2c2c2c', 'background value mismatch');
});

test('body rule has display: flex', () => {
  const prop = bodyRule.properties.find(p => p.name === 'display');
  assert(prop, 'display property not found');
  assertEqual(prop.value, 'flex', 'display value mismatch');
});

test('body rule has justify-content: center', () => {
  const prop = bodyRule.properties.find(p => p.name === 'justify-content');
  assert(prop, 'justify-content property not found');
  assertEqual(prop.value, 'center', 'justify-content value mismatch');
});

test('body rule has align-items: center', () => {
  const prop = bodyRule.properties.find(p => p.name === 'align-items');
  assert(prop, 'align-items property not found');
  assertEqual(prop.value, 'center', 'align-items value mismatch');
});

test('body rule has min-height: 100vh', () => {
  const prop = bodyRule.properties.find(p => p.name === 'min-height');
  assert(prop, 'min-height property not found');
  assertEqual(prop.value, '100vh', 'min-height value mismatch');
});

test('body rule has overflow: hidden', () => {
  const prop = bodyRule.properties.find(p => p.name === 'overflow');
  assert(prop, 'overflow property not found');
  assertEqual(prop.value, 'hidden', 'overflow value mismatch');
});

test('body rule has user-select: none', () => {
  const prop = bodyRule.properties.find(p => p.name === 'user-select');
  assert(prop, 'user-select property not found');
  assertEqual(prop.value, 'none', 'user-select value mismatch');
});

test('body rule has -webkit-touch-callout: none', () => {
  const prop = bodyRule.properties.find(p => p.name === '-webkit-touch-callout');
  assert(prop, '-webkit-touch-callout property not found');
  assertEqual(prop.value, 'none', '-webkit-touch-callout value mismatch');
});

test('body rule has no duplicate properties', () => {
  const dupes = getDuplicateProperties(bodyRule.properties);
  assertEqual(dupes.length, 0, `Duplicates found: ${dupes.join(', ')}`);
});

// ═══════════════════════════════════════════════════════════════════════════
// GROUP 5: Canvas Rule — CORE VERIFICATION (CD-067 Fix)
// ═══════════════════════════════════════════════════════════════════════════
console.log('');
console.log('── Group 5: Canvas Rule (CD-067 Core Fix) ─────────────');

const canvasRule = findRule(rules, 'canvas');

test('canvas rule has exactly 5 properties', () => {
  assertEqual(canvasRule.properties.length, 5,
    `Expected 5 properties, got ${canvasRule.properties.length}: ${canvasRule.properties.map(p => p.name).join(', ')}`);
});

test('canvas rule has display: block', () => {
  const prop = canvasRule.properties.find(p => p.name === 'display');
  assert(prop, 'display property not found');
  assertEqual(prop.value, 'block', 'display value mismatch');
});

test('canvas rule has touch-action: none', () => {
  const prop = canvasRule.properties.find(p => p.name === 'touch-action');
  assert(prop, 'touch-action property not found');
  assertEqual(prop.value, 'none', 'touch-action value mismatch');
});

test('canvas rule has max-width: 100vw', () => {
  const prop = canvasRule.properties.find(p => p.name === 'max-width');
  assert(prop, 'max-width property not found');
  assertEqual(prop.value, '100vw', 'max-width value mismatch');
});

test('canvas rule has max-height: 100vh', () => {
  const prop = canvasRule.properties.find(p => p.name === 'max-height');
  assert(prop, 'max-height property not found');
  assertEqual(prop.value, '100vh', 'max-height value mismatch');
});

test('canvas rule has box-shadow: 0 0 20px rgba(0, 0, 0, 0.5)', () => {
  const prop = canvasRule.properties.find(p => p.name === 'box-shadow');
  assert(prop, 'box-shadow property not found');
  assertEqual(prop.value, '0 0 20px rgba(0, 0, 0, 0.5)', 'box-shadow value mismatch');
});

test('[CD-067] canvas rule has NO duplicate properties', () => {
  const dupes = getDuplicateProperties(canvasRule.properties);
  assertEqual(dupes.length, 0,
    `REGRESSION: Duplicate properties still present: ${dupes.join(', ')}`);
});

test('[CD-067] canvas max-width appears exactly once', () => {
  const count = canvasRule.properties.filter(p => p.name === 'max-width').length;
  assertEqual(count, 1, `max-width appears ${count} times`);
});

test('[CD-067] canvas max-height appears exactly once', () => {
  const count = canvasRule.properties.filter(p => p.name === 'max-height').length;
  assertEqual(count, 1, `max-height appears ${count} times`);
});

test('[CD-067] canvas box-shadow appears exactly once', () => {
  const count = canvasRule.properties.filter(p => p.name === 'box-shadow').length;
  assertEqual(count, 1, `box-shadow appears ${count} times`);
});

// ═══════════════════════════════════════════════════════════════════════════
// GROUP 6: Global Duplicate Check
// ═══════════════════════════════════════════════════════════════════════════
console.log('');
console.log('── Group 6: Global Duplicate Check ────────────────────');

test('No rule in the file has duplicate properties', () => {
  const allDupes = [];
  for (const rule of rules) {
    const dupes = getDuplicateProperties(rule.properties);
    if (dupes.length > 0) {
      allDupes.push(`${rule.selector}: ${dupes.join(', ')}`);
    }
  }
  assertEqual(allDupes.length, 0,
    `Duplicate properties found: ${allDupes.join('; ')}`);
});

test('Total property count across all rules is 16 (3+8+5)', () => {
  const total = rules.reduce((sum, r) => sum + r.properties.length, 0);
  assertEqual(total, 16, `Expected 16 total properties, got ${total}`);
});

// ═══════════════════════════════════════════════════════════════════════════
// GROUP 7: HTML Entry Point Verification
// ═══════════════════════════════════════════════════════════════════════════
console.log('');
console.log('── Group 7: HTML Entry Point ───────────────────────────');

test('index.html has DOCTYPE declaration', () => {
  assert(html.trim().startsWith('<!DOCTYPE html>'), 'Missing DOCTYPE');
});

test('index.html has lang="en" attribute', () => {
  assert(html.includes('lang="en"'), 'Missing lang attribute');
});

test('index.html links to style.css', () => {
  assert(html.includes('href="style.css"'), 'Missing stylesheet link');
});

test('index.html has canvas element with id="gameCanvas"', () => {
  assert(html.includes('id="gameCanvas"'), 'Missing canvas element');
});

test('index.html canvas has width="400" height="600"', () => {
  assert(html.includes('width="400"') && html.includes('height="600"'),
    'Canvas dimensions mismatch');
});

test('index.html loads game.js script', () => {
  assert(html.includes('src="game.js"'), 'Missing game.js script');
});

test('index.html has viewport meta tag', () => {
  assert(html.includes('name="viewport"'), 'Missing viewport meta tag');
});

test('index.html viewport disables user scaling', () => {
  assert(html.includes('user-scalable=no'), 'Missing user-scalable=no');
});

// ═══════════════════════════════════════════════════════════════════════════
// GROUP 8: DOM-based Canvas Rendering (BUG-CD017-001)
// ═══════════════════════════════════════════════════════════════════════════
console.log('');
console.log('── Group 8: DOM-based Canvas Rendering (BUG-CD017-001) ─');

test('DOM-based canvas rendering test (BUG-CD017-001)', () => {
  // Load HTML with CSS in JSDOM environment
  const htmlPath = path.join(__dirname, '..', 'index.html');
  const cssPath = path.join(__dirname, '..', 'style.css');

  const htmlContent = fs.readFileSync(htmlPath, 'utf8');
  const cssContent = fs.readFileSync(cssPath, 'utf8');

  // Create JSDOM instance with CSS loaded
  const dom = new JSDOM(htmlContent, {
    resources: 'usable',
    url: 'http://localhost/'
  });

  const { window } = dom;
  const { document } = window;

  // Inject CSS into the document
  const styleElement = document.createElement('style');
  styleElement.textContent = cssContent;
  document.head.appendChild(styleElement);

  // Verify canvas element exists
  const canvas = document.getElementById('gameCanvas');
  assert(canvas, 'Canvas element not found in DOM');

  // Verify canvas attributes
  assertEqual(canvas.tagName, 'CANVAS', 'Element is not a canvas');
  assertEqual(canvas.getAttribute('width'), '400', 'Canvas width attribute mismatch');
  assertEqual(canvas.getAttribute('height'), '600', 'Canvas height attribute mismatch');

  // Verify canvas computed styles
  const computedStyle = window.getComputedStyle(canvas);

  // Verify display property is applied
  assert(computedStyle.display !== '', 'Canvas display property not set');

  // Verify canvas is rendered in DOM context
  const body = document.body;
  assert(body.contains(canvas), 'Canvas not in DOM body');

  // Verify CSS rules are applied to canvas
  const displayValue = computedStyle.getPropertyValue('display');
  assert(displayValue, 'Display property not computed');
});

// ═══════════════════════════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════════════════════════

console.log('');
console.log('═══════════════════════════════════════════════════════════');
console.log(`  RESULTS: ${passed} passed, ${failed} failed, ${skipped} skipped`);
console.log(`  TOTAL:   ${passed + failed + skipped} tests`);
console.log('═══════════════════════════════════════════════════════════');

if (failures.length > 0) {
  console.log('');
  console.log('  FAILURES:');
  for (const f of failures) {
    console.log(`    • ${f.name}: ${f.error}`);
  }
}

console.log('');
if (failed === 0) {
  console.log('  🎉 CD-067 FIX VERIFIED — All checks passed!');
} else {
  console.log('  ⚠️  CD-067 VERIFICATION FAILED — See failures above');
}
console.log('');

process.exit(failed > 0 ? 1 : 0);
