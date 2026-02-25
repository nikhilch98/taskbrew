/**
 * QA Test Suite for TS-020: HTML/CSS Entry Point (CD-017)
 * Commit: c9c2162
 * Branch: feature/cd-017-html-css-entry-point
 *
 * Tests acceptance criteria AC1-AC7 by parsing index.html and style.css.
 * No external dependencies — runs on plain Node.js.
 *
 * Usage:  node html-css-qa.test.js
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ───────────────────────── Helpers ─────────────────────────

const COMMIT = 'c9c2162';
const BASE = path.resolve(__dirname);

/** Read file content from the specific commit */
function readCommitted(relPath) {
  try {
    return execSync(`git show ${COMMIT}:flappy-bird/${relPath}`, {
      cwd: path.resolve(BASE, '..'),
      encoding: 'utf-8',
    });
  } catch {
    // Fallback to working directory
    return fs.readFileSync(path.join(BASE, relPath), 'utf-8');
  }
}

const html = readCommitted('index.html');
const css  = readCommitted('style.css');

let passed = 0;
let failed = 0;
let warnings = 0;
const results = [];

function assert(condition, label, detail) {
  if (condition) {
    passed++;
    results.push({ status: 'PASS', label, detail });
  } else {
    failed++;
    results.push({ status: 'FAIL', label, detail });
  }
}

function warn(label, detail) {
  warnings++;
  results.push({ status: 'WARN', label, detail });
}

// ───────────────────── AC-1: Centered 400×600 canvas on dark background ─────────────────────

console.log('\n═══════════════════════════════════════════════════');
console.log('  QA Test Suite: HTML/CSS Entry Point (CD-017)');
console.log('  Commit: ' + COMMIT);
console.log('═══════════════════════════════════════════════════\n');

console.log('── AC-1: Centered 400×600 canvas on #2c2c2c background ──');

// 1a. Canvas element exists with correct width/height
const canvasMatch = html.match(/<canvas[^>]*>/i);
assert(canvasMatch !== null, 'AC-1.1: Canvas element exists', canvasMatch ? canvasMatch[0] : 'NOT FOUND');

const widthMatch = html.match(/<canvas[^>]*width\s*=\s*["']?400["']?/i);
assert(widthMatch !== null, 'AC-1.2: Canvas width is 400', widthMatch ? widthMatch[0] : 'width attribute missing or incorrect');

const heightMatch = html.match(/<canvas[^>]*height\s*=\s*["']?600["']?/i);
assert(heightMatch !== null, 'AC-1.3: Canvas height is 600', heightMatch ? heightMatch[0] : 'height attribute missing or incorrect');

// 1b. Canvas has id="gameCanvas"
const canvasId = html.match(/<canvas[^>]*id\s*=\s*["']gameCanvas["']/i);
assert(canvasId !== null, 'AC-1.4: Canvas id="gameCanvas"', canvasId ? 'found' : 'missing');

// 1c. Dark background
assert(css.includes('#2c2c2c'), 'AC-1.5: Body background is #2c2c2c', css.includes('#2c2c2c') ? 'found #2c2c2c' : 'NOT FOUND');

// 1d. Flex centering
assert(css.includes('display: flex') || css.includes('display:flex'),
  'AC-1.6: Body uses flexbox', 'display: flex');
assert(css.includes('justify-content: center') || css.includes('justify-content:center'),
  'AC-1.7: Horizontal centering', 'justify-content: center');
assert(css.includes('align-items: center') || css.includes('align-items:center'),
  'AC-1.8: Vertical centering', 'align-items: center');

// 1e. Full viewport height
assert(css.includes('min-height: 100vh') || css.includes('min-height:100vh'),
  'AC-1.9: Full viewport height (min-height: 100vh)', 'min-height: 100vh');

// ───────────────── AC-2: Viewport meta tag ─────────────────

console.log('\n── AC-2: Viewport meta has maximum-scale=1.0, user-scalable=no ──');

const viewportMeta = html.match(/<meta[^>]*name\s*=\s*["']viewport["'][^>]*content\s*=\s*["']([^"']+)["'][^>]*>/i);
assert(viewportMeta !== null, 'AC-2.1: Viewport meta tag exists', viewportMeta ? viewportMeta[0] : 'NOT FOUND');

const viewportContent = viewportMeta ? viewportMeta[1] : '';
assert(viewportContent.includes('width=device-width'),
  'AC-2.2: viewport has width=device-width', viewportContent);
assert(viewportContent.includes('initial-scale=1.0'),
  'AC-2.3: viewport has initial-scale=1.0', viewportContent);
assert(viewportContent.includes('maximum-scale=1.0'),
  'AC-2.4: viewport has maximum-scale=1.0', viewportContent);
assert(viewportContent.includes('user-scalable=no'),
  'AC-2.5: viewport has user-scalable=no', viewportContent);

// ───────────────── AC-3: Canvas responsive scaling ─────────────────

console.log('\n── AC-3: Canvas scales down on narrow viewports (<400px) ──');

assert(css.includes('max-width: 100vw') || css.includes('max-width:100vw') || css.includes('max-width: 100%') || css.includes('max-width:100%'),
  'AC-3.1: Canvas has max-width constraint', 'Prevents overflow on narrow viewports');

assert(css.includes('max-height: 100vh') || css.includes('max-height:100vh') || css.includes('max-height: 100%') || css.includes('max-height:100%'),
  'AC-3.2: Canvas has max-height constraint', 'Prevents overflow on short viewports');

// Verify overflow: hidden on body prevents any remaining scrollbar
assert(css.includes('overflow: hidden') || css.includes('overflow:hidden'),
  'AC-3.3: Body has overflow: hidden', 'Fallback to prevent scrollbar');

// ───────────────── AC-4: No external dependencies ─────────────────

console.log('\n── AC-4: No CDN links, module imports, type="module" ──');

// 4a. No external <script> or <link> referencing CDN
const cdnPatterns = [
  /https?:\/\//i,
  /cdn\./i,
  /cdnjs\./i,
  /unpkg\.com/i,
  /jsdelivr/i,
];
let hasCDN = false;
for (const p of cdnPatterns) {
  if (p.test(html)) {
    hasCDN = true;
    break;
  }
}
assert(!hasCDN, 'AC-4.1: No external CDN links in HTML', hasCDN ? 'CDN link found!' : 'None found');

// 4b. No type="module" on script tag
const scriptTag = html.match(/<script[^>]*>/gi) || [];
const hasTypeModule = scriptTag.some(s => /type\s*=\s*["']module["']/i.test(s));
assert(!hasTypeModule, 'AC-4.2: No type="module" on script tag', hasTypeModule ? 'type="module" found' : 'None found');

// 4c. Script references game.js as a local file
const scriptSrc = html.match(/<script[^>]*src\s*=\s*["']([^"']+)["']/i);
assert(scriptSrc && scriptSrc[1] === 'game.js', 'AC-4.3: Script src is "game.js"',
  scriptSrc ? scriptSrc[1] : 'NOT FOUND');

// 4d. Stylesheet references local style.css
const linkHref = html.match(/<link[^>]*href\s*=\s*["']([^"']+)["']/i);
assert(linkHref && linkHref[1] === 'style.css', 'AC-4.4: Stylesheet href is "style.css"',
  linkHref ? linkHref[1] : 'NOT FOUND');

// 4e. No import/export in game.js (check committed version)
let gameJs = '';
try {
  gameJs = readCommitted('game.js');
} catch { /* game.js may not be part of this PR */ }
if (gameJs) {
  const hasImport = /^\s*(import\s|export\s)/m.test(gameJs);
  assert(!hasImport, 'AC-4.5: No import/export in game.js', hasImport ? 'import/export found!' : 'None found');
} else {
  warn('AC-4.5: game.js not checked', 'Could not read game.js from commit');
}

// ───────────────── AC-5: CSS prevents selection/touch ─────────────────

console.log('\n── AC-5: CSS prevents text selection, touch callout, touch gestures ──');

// 5a. user-select: none on body
assert(css.includes('user-select: none') || css.includes('user-select:none'),
  'AC-5.1: user-select: none (prevents text selection)', 'Found in CSS');

// 5b. -webkit-touch-callout: none on body
assert(css.includes('-webkit-touch-callout: none') || css.includes('-webkit-touch-callout:none'),
  'AC-5.2: -webkit-touch-callout: none (prevents iOS callout)', 'Found in CSS');

// 5c. touch-action: none on canvas
assert(css.includes('touch-action: none') || css.includes('touch-action:none'),
  'AC-5.3: touch-action: none on canvas (prevents browser gestures)', 'Found in CSS');

// 5d. Verify these are applied to the correct selectors
// Parse simple CSS blocks
function getCSSBlock(css, selector) {
  // Simple regex to extract the block for a given selector
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(escaped + '\\s*\\{([^}]+)\\}', 'i');
  const m = css.match(re);
  return m ? m[1] : null;
}

const bodyBlock = getCSSBlock(css, 'body');
const canvasBlock = getCSSBlock(css, 'canvas');

if (bodyBlock) {
  assert(bodyBlock.includes('user-select: none') || bodyBlock.includes('user-select:none'),
    'AC-5.4: user-select: none is on BODY selector', 'Correct placement');
  assert(bodyBlock.includes('-webkit-touch-callout: none') || bodyBlock.includes('-webkit-touch-callout:none'),
    'AC-5.5: -webkit-touch-callout: none is on BODY selector', 'Correct placement');
} else {
  warn('AC-5.4-5.5: Could not parse body CSS block', 'Manual inspection needed');
}

if (canvasBlock) {
  assert(canvasBlock.includes('touch-action: none') || canvasBlock.includes('touch-action:none'),
    'AC-5.6: touch-action: none is on CANVAS selector', 'Correct placement');
} else {
  warn('AC-5.6: Could not parse canvas CSS block', 'Manual inspection needed');
}

// ───────────────── AC-6: Subtle box-shadow ─────────────────

console.log('\n── AC-6: Subtle box-shadow visible around canvas ──');

assert(css.includes('box-shadow'), 'AC-6.1: box-shadow property exists in CSS', 'Found');

if (canvasBlock) {
  assert(canvasBlock.includes('box-shadow'),
    'AC-6.2: box-shadow is applied to canvas selector', 'Correct placement');

  // Validate it's a subtle shadow (rgba with some transparency, or reasonable spread)
  const shadowMatch = canvasBlock.match(/box-shadow:\s*([^;]+)/);
  if (shadowMatch) {
    const shadowVal = shadowMatch[1];
    assert(shadowVal.includes('rgba') || shadowVal.includes('0 0'),
      'AC-6.3: box-shadow uses rgba or soft values (subtle)', shadowVal);
    // Log the exact shadow value for manual review
    results.push({ status: 'INFO', label: 'AC-6.4: box-shadow value', detail: shadowVal.trim() });
  }
} else {
  warn('AC-6.2: Could not parse canvas CSS block for box-shadow', 'Manual inspection needed');
}

// ───────────────── AC-7: No horizontal scrollbar on narrow viewports ─────────────────

console.log('\n── AC-7: No horizontal scrollbar on narrow viewports ──');

// 7a. overflow: hidden on body
assert(css.includes('overflow: hidden') || css.includes('overflow:hidden'),
  'AC-7.1: Body has overflow: hidden', 'Prevents scrollbar');

// 7b. max-width constraint on canvas prevents it from exceeding viewport
assert(css.includes('max-width'), 'AC-7.2: Canvas has max-width constraint',
  'Prevents canvas overflow');

// 7c. box-sizing: border-box in reset
assert(css.includes('box-sizing: border-box') || css.includes('box-sizing:border-box'),
  'AC-7.3: box-sizing: border-box in reset', 'Prevents padding overflow');

// 7d. No fixed widths on body that could cause overflow
if (bodyBlock) {
  const hasFixedWidth = /width\s*:\s*\d+px/i.test(bodyBlock);
  assert(!hasFixedWidth, 'AC-7.4: Body has no fixed pixel width', 'No overflow risk from body');
}

// ───────────────── Additional Structure Checks ─────────────────

console.log('\n── Additional structural checks ──');

// HTML5 doctype
assert(html.trimStart().startsWith('<!DOCTYPE html>'), 'STRUCT-1: HTML5 doctype', 'Found');

// lang attribute
assert(/<html[^>]*lang\s*=\s*["']en["']/i.test(html), 'STRUCT-2: html lang="en"', 'Accessibility');

// charset
assert(/<meta[^>]*charset\s*=\s*["']UTF-8["']/i.test(html), 'STRUCT-3: charset UTF-8', 'Found');

// title
assert(/<title>Flappy Bird<\/title>/i.test(html), 'STRUCT-4: Page title "Flappy Bird"', 'Found');

// Script at end of body (before </body>)
const bodyContent = html.match(/<body>([\s\S]*)<\/body>/i);
if (bodyContent) {
  const bodyHTML = bodyContent[1].trim();
  assert(bodyHTML.endsWith('</script>'), 'STRUCT-5: Script is last element in body',
    'Ensures DOM is ready before script runs');
}

// Canvas is the only interactive element
const domElements = html.match(/<(div|span|p|button|input|form|a|img)\b/gi);
assert(!domElements || domElements.length === 0, 'STRUCT-6: No extra DOM elements (canvas-only UI)',
  domElements ? `Found: ${domElements.join(', ')}` : 'Only canvas element');

// Universal reset
assert(css.includes('* {') || css.includes('*{'), 'STRUCT-7: Universal reset selector exists', 'Found');
assert(css.includes('margin: 0') || css.includes('margin:0'), 'STRUCT-8: Margin reset to 0', 'Found');
assert(css.includes('padding: 0') || css.includes('padding:0'), 'STRUCT-9: Padding reset to 0', 'Found');

// Canvas display: block (removes inline gap)
if (canvasBlock) {
  assert(canvasBlock.includes('display: block') || canvasBlock.includes('display:block'),
    'STRUCT-10: Canvas display: block', 'Removes inline gap below canvas');
}

// ───────────────── Print Results ─────────────────

console.log('\n═══════════════════════════════════════════════════');
console.log('  TEST RESULTS');
console.log('═══════════════════════════════════════════════════\n');

for (const r of results) {
  const icon = r.status === 'PASS' ? '✅' :
               r.status === 'FAIL' ? '❌' :
               r.status === 'WARN' ? '⚠️ ' :
               'ℹ️ ';
  console.log(`  ${icon} [${r.status}] ${r.label}`);
  if (r.detail && r.status !== 'PASS') {
    console.log(`         └─ ${r.detail}`);
  }
}

console.log('\n───────────────────────────────────────────────────');
console.log(`  PASSED: ${passed}  |  FAILED: ${failed}  |  WARNINGS: ${warnings}`);
console.log('───────────────────────────────────────────────────');

if (failed > 0) {
  console.log('\n  ❌ QA VERDICT: FAIL — see failures above');
  console.log('');
  process.exit(1);
} else if (warnings > 0) {
  console.log('\n  ⚠️  QA VERDICT: PASS WITH WARNINGS');
  console.log('');
  process.exit(0);
} else {
  console.log('\n  ✅ QA VERDICT: ALL TESTS PASSED');
  console.log('');
  process.exit(0);
}
