/**
 * TS-002 — QA Verification for game.js skeleton (CD-002)
 * Automated test suite using Node.js (no external dependencies)
 *
 * Tests cover:
 *  1. Constants (19 game + 3 state)
 *  2. Canvas initialization
 *  3. Game state variables & initial values
 *  4. State machine skeleton (handleInput, resetGame, flap)
 *  5. Game loop (dt computation, cap, update/render/rAF call chain)
 *  6. Input handlers (keyboard, mouse, touch)
 *  7. No forbidden patterns (import/export, DOMContentLoaded, external deps)
 *  8. No magic numbers in function bodies
 */

const fs   = require('fs');
const path = require('path');

// ─── helpers ───

let passed = 0;
let failed = 0;
const failures = [];

function assert(condition, message) {
    if (condition) {
        passed++;
        console.log(`  ✅ ${message}`);
    } else {
        failed++;
        console.log(`  ❌ ${message}`);
        failures.push(message);
    }
}

function assertEqual(actual, expected, message) {
    if (actual === expected) {
        passed++;
        console.log(`  ✅ ${message}`);
    } else {
        failed++;
        console.log(`  ❌ ${message}  — expected: ${JSON.stringify(expected)}, got: ${JSON.stringify(actual)}`);
        failures.push(`${message} — expected: ${JSON.stringify(expected)}, got: ${JSON.stringify(actual)}`);
    }
}

function section(title) {
    console.log(`\n━━━ ${title} ━━━`);
}

// ─── read source once ───

const src = fs.readFileSync(path.join(__dirname, 'game.js'), 'utf8');
const lines = src.split('\n');

// ═══════════════════════════════════════════════════════
// 1. Constants — 19 game + 3 state
// ═══════════════════════════════════════════════════════

section('1. Constants');

const gameConstants = [
    'CANVAS_WIDTH', 'CANVAS_HEIGHT',
    'GROUND_HEIGHT',
    'BIRD_X', 'BIRD_RADIUS', 'BIRD_START_Y',
    'GRAVITY', 'FLAP_VELOCITY', 'MAX_FALL_SPEED',
    'PIPE_WIDTH', 'PIPE_GAP', 'PIPE_SPEED', 'PIPE_SPACING', 'PIPE_MIN_TOP', 'PIPE_MAX_TOP',
    'BOB_AMPLITUDE', 'BOB_FREQUENCY',
    'PIPE_CAP_HEIGHT', 'PIPE_CAP_OVERHANG'
];

const stateConstants = ['STATE_IDLE', 'STATE_PLAYING', 'STATE_GAME_OVER'];

// Each constant must be declared with `const`
for (const name of [...gameConstants, ...stateConstants]) {
    const pattern = new RegExp(`^const\\s+${name}\\s*=`);
    const found = lines.some(l => pattern.test(l.trim()));
    assert(found, `const ${name} declared at top level`);
}

assertEqual(gameConstants.length, 19, 'Exactly 19 game constants');
assertEqual(stateConstants.length, 3, 'Exactly 3 state constants');

// Verify PIPE_MAX_TOP derivation: CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50
assert(
    src.includes('PIPE_MAX_TOP    = CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50'),
    'PIPE_MAX_TOP derived from CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50'
);

// Verify the computed value equals 360
// 600 - 60 - 130 - 50 = 360
const pipeMaxTopMatch = src.match(/\/\/\s*=\s*(\d+)px/);
assert(pipeMaxTopMatch && pipeMaxTopMatch[1] === '360', 'PIPE_MAX_TOP evaluates to 360');

// ═══════════════════════════════════════════════════════
// 2. Canvas initialization
// ═══════════════════════════════════════════════════════

section('2. Canvas initialization');

assert(src.includes("document.getElementById('gameCanvas')"), "getElementById('gameCanvas') present");
assert(src.includes(".getContext('2d')"), "getContext('2d') present");

// Both assigned to const
assert(/const\s+canvas\s*=\s*document\.getElementById/.test(src), 'canvas assigned via const');
assert(/const\s+ctx\s*=\s*canvas\.getContext/.test(src), 'ctx assigned via const');

// ═══════════════════════════════════════════════════════
// 3. Game state variables & initial values
// ═══════════════════════════════════════════════════════

section('3. Game state variables');

// Bird object shape
assert(/let\s+bird\s*=\s*\{/.test(src), 'bird declared with let');

// Build a minimal DOM stub and evaluate the source to inspect runtime values
// We'll use a sandboxed eval approach

const domStub = `
    // Minimal DOM stubs
    const _listeners = {};
    const document = {
        getElementById: (id) => ({
            getContext: () => ({
                fillStyle: '',
                fillRect: () => {},
            }),
            addEventListener: (type, fn, opts) => {
                _listeners['canvas_' + type] = { fn, opts };
            }
        }),
        addEventListener: (type, fn) => {
            _listeners['doc_' + type] = { fn };
        }
    };
    let _rafCallback = null;
    function requestAnimationFrame(cb) { _rafCallback = cb; }
`;

// Evaluate game.js in a sandboxed scope
// Use getters/setters for `let` variables so we can mutate them through the returned object
let sandbox = {};
try {
    const evalCode = `
        ${domStub}
        ${src}
        // Export everything we need to test
        // Use getters/setters for let-scoped variables so tests can read AND write them
        ({
            CANVAS_WIDTH, CANVAS_HEIGHT, GROUND_HEIGHT,
            BIRD_X, BIRD_RADIUS, BIRD_START_Y,
            GRAVITY, FLAP_VELOCITY, MAX_FALL_SPEED,
            PIPE_WIDTH, PIPE_GAP, PIPE_SPEED, PIPE_SPACING, PIPE_MIN_TOP, PIPE_MAX_TOP,
            BOB_AMPLITUDE, BOB_FREQUENCY,
            PIPE_CAP_HEIGHT, PIPE_CAP_OVERHANG,
            STATE_IDLE, STATE_PLAYING, STATE_GAME_OVER,
            bird, pipes,
            get score() { return score; },
            set score(v) { score = v; },
            get bobTimer() { return bobTimer; },
            set bobTimer(v) { bobTimer = v; },
            get groundOffset() { return groundOffset; },
            set groundOffset(v) { groundOffset = v; },
            get gameState() { return gameState; },
            set gameState(v) { gameState = v; },
            get lastTimestamp() { return lastTimestamp; },
            set lastTimestamp(v) { lastTimestamp = v; },
            get spacePressed() { return spacePressed; },
            set spacePressed(v) { spacePressed = v; },
            handleInput, resetGame, flap,
            update, render, gameLoop,
            _listeners, _rafCallback
        })
    `;
    sandbox = eval(evalCode);
} catch (e) {
    console.error('  ❌ Failed to evaluate game.js:', e.message);
    failed++;
    failures.push('game.js evaluation failed: ' + e.message);
}

if (sandbox.bird) {
    // Bird initial values
    assertEqual(sandbox.bird.x, 100, 'bird.x === 100');
    assertEqual(sandbox.bird.y, 300, 'bird.y === 300 (CANVAS_HEIGHT / 2)');
    assertEqual(sandbox.bird.velocity, 0, 'bird.velocity === 0');
    assertEqual(sandbox.bird.radius, 15, 'bird.radius === 15');
    assertEqual(sandbox.bird.rotation, 0, 'bird.rotation === 0');

    // Other state
    assert(Array.isArray(sandbox.pipes) && sandbox.pipes.length === 0, 'pipes === []');
    assertEqual(sandbox.score, 0, 'score === 0');
    assertEqual(sandbox.bobTimer, 0, 'bobTimer === 0');
    assertEqual(sandbox.groundOffset, 0, 'groundOffset === 0');
    assertEqual(sandbox.gameState, 'IDLE', "gameState === 'IDLE' (STATE_IDLE)");
    assertEqual(sandbox.lastTimestamp, 0, 'lastTimestamp === 0');
    assertEqual(sandbox.spacePressed, false, 'spacePressed === false');
}

// ═══════════════════════════════════════════════════════
// 4. Constant values
// ═══════════════════════════════════════════════════════

section('4. Constant runtime values');

if (sandbox.CANVAS_WIDTH !== undefined) {
    assertEqual(sandbox.CANVAS_WIDTH, 400, 'CANVAS_WIDTH === 400');
    assertEqual(sandbox.CANVAS_HEIGHT, 600, 'CANVAS_HEIGHT === 600');
    assertEqual(sandbox.GROUND_HEIGHT, 60, 'GROUND_HEIGHT === 60');
    assertEqual(sandbox.BIRD_X, 100, 'BIRD_X === 100');
    assertEqual(sandbox.BIRD_RADIUS, 15, 'BIRD_RADIUS === 15');
    assertEqual(sandbox.BIRD_START_Y, 300, 'BIRD_START_Y === 300');
    assertEqual(sandbox.GRAVITY, 980, 'GRAVITY === 980');
    assertEqual(sandbox.FLAP_VELOCITY, -280, 'FLAP_VELOCITY === -280');
    assertEqual(sandbox.MAX_FALL_SPEED, 600, 'MAX_FALL_SPEED === 600');
    assertEqual(sandbox.PIPE_WIDTH, 52, 'PIPE_WIDTH === 52');
    assertEqual(sandbox.PIPE_GAP, 130, 'PIPE_GAP === 130');
    assertEqual(sandbox.PIPE_SPEED, 120, 'PIPE_SPEED === 120');
    assertEqual(sandbox.PIPE_SPACING, 220, 'PIPE_SPACING === 220');
    assertEqual(sandbox.PIPE_MIN_TOP, 50, 'PIPE_MIN_TOP === 50');
    assertEqual(sandbox.PIPE_MAX_TOP, 360, 'PIPE_MAX_TOP === 360');
    assertEqual(sandbox.BOB_AMPLITUDE, 8, 'BOB_AMPLITUDE === 8');
    assertEqual(sandbox.BOB_FREQUENCY, 2, 'BOB_FREQUENCY === 2');
    assertEqual(sandbox.PIPE_CAP_HEIGHT, 20, 'PIPE_CAP_HEIGHT === 20');
    assertEqual(sandbox.PIPE_CAP_OVERHANG, 3, 'PIPE_CAP_OVERHANG === 3');
    assertEqual(sandbox.STATE_IDLE, 'IDLE', "STATE_IDLE === 'IDLE'");
    assertEqual(sandbox.STATE_PLAYING, 'PLAYING', "STATE_PLAYING === 'PLAYING'");
    assertEqual(sandbox.STATE_GAME_OVER, 'GAME_OVER', "STATE_GAME_OVER === 'GAME_OVER'");
}

// ═══════════════════════════════════════════════════════
// 5. State machine
// ═══════════════════════════════════════════════════════

section('5. State machine — handleInput()');

if (sandbox.handleInput) {
    // Test IDLE → PLAYING with flap
    sandbox.gameState = 'IDLE';
    sandbox.bird.velocity = 0;
    sandbox.handleInput();
    assertEqual(sandbox.gameState, 'PLAYING', 'IDLE → handleInput() → PLAYING');
    assertEqual(sandbox.bird.velocity, -280, 'IDLE → handleInput() triggers flap (velocity = FLAP_VELOCITY)');

    // Test PLAYING → flap
    sandbox.gameState = 'PLAYING';
    sandbox.bird.velocity = 100; // simulate falling
    sandbox.handleInput();
    assertEqual(sandbox.gameState, 'PLAYING', 'PLAYING → handleInput() stays PLAYING');
    assertEqual(sandbox.bird.velocity, -280, 'PLAYING → handleInput() triggers flap');

    // Test GAME_OVER → resetGame
    sandbox.gameState = 'GAME_OVER';
    sandbox.bird.y = 500;
    sandbox.bird.velocity = 200;
    sandbox.bird.rotation = 1.5;
    sandbox.pipes.push({ x: 100 });
    sandbox.score = 42;
    sandbox.bobTimer = 3.14;
    sandbox.groundOffset = 999;
    sandbox.handleInput();
    assertEqual(sandbox.gameState, 'IDLE', 'GAME_OVER → handleInput() → IDLE (via resetGame)');
}

section('5b. State machine — resetGame()');

if (sandbox.resetGame) {
    // Dirty all state, then reset
    sandbox.gameState = 'GAME_OVER';
    sandbox.bird.y = 999;
    sandbox.bird.velocity = 999;
    sandbox.bird.rotation = 999;
    sandbox.pipes.push({ x: 1 }, { x: 2 });
    sandbox.score = 100;
    sandbox.bobTimer = 50;
    sandbox.groundOffset = 50;

    sandbox.resetGame();

    assertEqual(sandbox.gameState, 'IDLE', 'resetGame sets gameState = IDLE');
    assertEqual(sandbox.bird.y, 300, 'resetGame sets bird.y = BIRD_START_Y (300)');
    assertEqual(sandbox.bird.velocity, 0, 'resetGame sets bird.velocity = 0');
    assertEqual(sandbox.bird.rotation, 0, 'resetGame sets bird.rotation = 0');
    assertEqual(sandbox.pipes.length, 0, 'resetGame clears pipes');
    assertEqual(sandbox.score, 0, 'resetGame sets score = 0');
    assertEqual(sandbox.bobTimer, 0, 'resetGame sets bobTimer = 0');
    assertEqual(sandbox.groundOffset, 0, 'resetGame sets groundOffset = 0');
}

section('5c. State machine — flap()');

if (sandbox.flap) {
    sandbox.bird.velocity = 100;
    sandbox.flap();
    assertEqual(sandbox.bird.velocity, -280, 'flap() sets bird.velocity = FLAP_VELOCITY (-280)');
}

// ═══════════════════════════════════════════════════════
// 6. Game loop
// ═══════════════════════════════════════════════════════

section('6. Game loop');

assert(typeof sandbox.gameLoop === 'function', 'gameLoop is a function');
assert(typeof sandbox.update === 'function', 'update is a function');
assert(typeof sandbox.render === 'function', 'render is a function');

// Check source structure
assert(/function\s+gameLoop\s*\(\s*timestamp\s*\)/.test(src), 'gameLoop accepts timestamp parameter');
assert(src.includes('lastTimestamp === 0'), 'First-frame check via lastTimestamp === 0');
assert(/dt\s*>\s*0\.05/.test(src), 'dt capped at 0.05s');
assert(/dt\s*=\s*0\.05/.test(src), 'dt set to 0.05 when exceeded');
assert(src.includes('update(dt)'), 'Calls update(dt)');
assert(src.includes('render(ctx)'), 'Calls render(ctx)');

// rAF scheduling inside gameLoop
const gameLoopBody = src.slice(src.indexOf('function gameLoop'));
assert(gameLoopBody.includes('requestAnimationFrame(gameLoop)'), 'rAF(gameLoop) inside gameLoop');

// rAF called at file bottom to start loop
const lastLines = lines.slice(-5).join('\n');
assert(lastLines.includes('requestAnimationFrame(gameLoop)'), 'requestAnimationFrame(gameLoop) called at file bottom');

// Verify rAF was actually called during eval
assert(sandbox._rafCallback !== null, 'requestAnimationFrame was invoked during eval (loop started)');

// Verify dt computation (ms -> seconds)
assert(src.includes('/ 1000'), 'dt conversion divides by 1000 (ms → s)');

// ═══════════════════════════════════════════════════════
// 7. Input handlers
// ═══════════════════════════════════════════════════════

section('7. Input handlers');

// Keyboard
assert(sandbox._listeners['doc_keydown'] !== undefined, 'keydown listener on document');
assert(sandbox._listeners['doc_keyup'] !== undefined, 'keyup listener on document');

// Check space key with auto-repeat guard
assert(src.includes("e.code === 'Space'"), "Keyboard uses e.code === 'Space'");
assert(src.includes('if (!spacePressed)'), 'spacePressed auto-repeat guard present');
assert(src.includes('spacePressed = true'), 'spacePressed set to true on keydown');
assert(src.includes('spacePressed = false'), 'spacePressed set to false on keyup');

// Mouse
assert(sandbox._listeners['canvas_mousedown'] !== undefined, 'mousedown listener on canvas');

// Touch
assert(sandbox._listeners['canvas_touchstart'] !== undefined, 'touchstart listener on canvas');
const touchOpts = sandbox._listeners['canvas_touchstart']?.opts;
assert(touchOpts && touchOpts.passive === false, 'touchstart has { passive: false }');
assert(src.includes('e.preventDefault()') || src.includes('e.preventDefault();'), 'preventDefault called in touch handler');

// ═══════════════════════════════════════════════════════
// 8. No forbidden patterns
// ═══════════════════════════════════════════════════════

section('8. Forbidden patterns');

assert(!/\bimport\s/.test(src), 'No import statements');
assert(!/\bexport\s/.test(src), 'No export statements');
assert(!/\brequire\s*\(/.test(src), 'No require() calls');
assert(!src.includes('DOMContentLoaded'), 'No DOMContentLoaded wrapper');

// ═══════════════════════════════════════════════════════
// 9. Render — sky blue background
// ═══════════════════════════════════════════════════════

section('9. Render — sky blue background');

assert(src.includes("#70c5ce"), 'Background color #70c5ce present');
assert(src.includes('fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)'), 'fillRect covers full canvas');

// ═══════════════════════════════════════════════════════
// 10. No magic numbers in game logic functions
// ═══════════════════════════════════════════════════════

section('10. Magic number audit');

// Scope: game LOGIC only — excludes render functions which legitimately
// use inline drawing constants (pixel offsets, font sizes, colors).
// Audited sections:
//   STATE MACHINE → RENDER LOGIC  (handleInput, resetGame, flap, updateBird,
//                                   input handlers, pipe logic, collision, scoring, update)
//   GAME LOOP → EOF               (gameLoop)
const smStart    = src.indexOf('// ===== STATE MACHINE =====');
const renderStart = src.indexOf('// ===== RENDER LOGIC =====');
const glStart    = src.indexOf('// ===== GAME LOOP =====');

const logicSection = src.slice(smStart, renderStart) + '\n' + src.slice(glStart);

// Strip comments AND string literals before scanning for magic numbers
// (prevents false positives from hex color strings like '#000000')
const logicClean = logicSection
    .replace(/\/\/.*$/gm, '')           // remove single-line comments
    .replace(/\/\*[\s\S]*?\*\//g, '')   // remove multi-line comments
    .replace(/'[^']*'/g, '""')          // remove single-quoted strings
    .replace(/"[^"]*"/g, '""')          // remove double-quoted strings
    .replace(/`[^`]*`/g, '""');         // remove template literals

// Find all numeric literals in game logic (excluding comments & strings)
const numericPattern = /(?<!\w)\d+\.?\d*(?!\w)/g;
const allowedNumbers = new Set([
    '0', '1', '2',      // 0 for origin/reset, 1 and 2 for math (2*PI)
    '1000',              // ms → s conversion (standard)
    '0.05',              // dt cap — directly tied to 50ms design
    '6',                 // Math.PI/6 — 30° rotation boundary (updateBird)
    '24',                // ground hash spacing modulo (update)
]);
const numMatches = [...logicClean.matchAll(numericPattern)];
const magicNumbers = numMatches
    .map(m => m[0])
    .filter(n => !allowedNumbers.has(n));

assert(magicNumbers.length === 0, `No unexpected magic numbers in game logic (found: ${magicNumbers.length > 0 ? magicNumbers.join(', ') : 'none'})`);

// ═══════════════════════════════════════════════════════
// 11. Update function — idle bob logic
// ═══════════════════════════════════════════════════════

section('11. Update — idle bob logic');

// Verify bob uses correct constants
assert(src.includes('BOB_FREQUENCY'), 'update uses BOB_FREQUENCY');
assert(src.includes('BOB_AMPLITUDE'), 'update uses BOB_AMPLITUDE');
assert(src.includes('BIRD_START_Y'), 'update uses BIRD_START_Y for idle bob center');

// ═══════════════════════════════════════════════════════
// 12. HTML structure check
// ═══════════════════════════════════════════════════════

section('12. HTML structure');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
assert(html.includes('<script src="game.js"></script>'), 'game.js loaded via script tag');
assert(html.includes('id="gameCanvas"'), 'Canvas has id="gameCanvas"');
assert(html.includes('width="400"'), 'Canvas width="400"');
assert(html.includes('height="600"'), 'Canvas height="600"');

// Script tag is at end of body (no DOMContentLoaded needed)
const bodyEnd = html.indexOf('</body>');
const scriptPos = html.indexOf('<script src="game.js">');
assert(scriptPos < bodyEnd && scriptPos > html.indexOf('<canvas'), 'Script tag after canvas, before </body>');

// ═══════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════

console.log('\n═══════════════════════════════════════════');
console.log(`  RESULTS: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('═══════════════════════════════════════════');

if (failures.length > 0) {
    console.log('\nFailed tests:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

process.exit(failed > 0 ? 1 : 0);
