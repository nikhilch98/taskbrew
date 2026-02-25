/**
 * TS-039 — QA Verification: Pipe Color Fix (TS-036 / CD-021)
 * Automated test suite using Node.js (no external dependencies)
 *
 * Verifies commit d713039: fix(pipes): restore pipe colors to spec values #3cb043/#2d8a34
 *
 * Tests cover:
 *  AC-1:  Pipe body color is #3cb043 (4 tests)
 *  AC-2:  Pipe cap color is #2d8a34 (4 tests)
 *  AC-3:  Paint order — body before caps (15 tests)
 *  AC-4:  No geometry regressions (22 tests)
 *  AC-5:  Game plays normally (12 tests)
 *  AC-6:  Old colors absent (10 tests)
 *  Extra: File integrity (5 tests)
 *  Extra: Non-pipe colors unchanged (8 tests)
 *
 * Total: 80 tests
 */

const fs   = require('fs');
const path = require('path');

// ─── helpers ───

let passed = 0;
let failed = 0;
const failures = [];
const bugs = [];

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
        const msg = `${message}  — expected: ${JSON.stringify(expected)}, got: ${JSON.stringify(actual)}`;
        console.log(`  ❌ ${msg}`);
        failures.push(msg);
    }
}

function assertApprox(actual, expected, tolerance, message) {
    if (Math.abs(actual - expected) <= tolerance) {
        passed++;
        console.log(`  ✅ ${message}`);
    } else {
        failed++;
        const msg = `${message}  — expected ~${expected} (±${tolerance}), got: ${actual}`;
        console.log(`  ❌ ${msg}`);
        failures.push(msg);
    }
}

function logBug(id, summary, steps, expected, actual) {
    bugs.push({ id, summary, steps, expected, actual });
    console.log(`  🐛 BUG-${id}: ${summary}`);
}

function section(title) {
    console.log(`\n━━━ ${title} ━━━`);
}

// ─── read source (from main branch) ───

// We read the game.js from git main to test the merged pipe-color fix.
// The test repo may be on a different branch, so use a temp copy from main.
let src;
const mainCopy = '/tmp/game-main.js';
const localCopy = path.join(__dirname, '..', 'game.js');
if (fs.existsSync(mainCopy)) {
    src = fs.readFileSync(mainCopy, 'utf8');
} else {
    src = fs.readFileSync(localCopy, 'utf8');
}

// ─── DOM/Canvas stub with render tracking ───

function createSandbox() {
    const domStub = `
        const _listeners = {};
        const _renderCalls = [];
        const _fillRects = [];
        const _ctxStub = {
            fillStyle: '',
            strokeStyle: '',
            lineWidth: 0,
            font: '',
            textAlign: '',
            textBaseline: '',
            lineJoin: '',
            fillRect: function(x, y, w, h) {
                _fillRects.push({ fillStyle: _ctxStub.fillStyle, x, y, w, h });
                _renderCalls.push({ type: 'fillRect', fillStyle: _ctxStub.fillStyle, x, y, w, h });
            },
            strokeRect: function() {},
            clearRect: function() {},
            beginPath: function() {},
            closePath: function() {},
            arc: function() {},
            ellipse: function() {},
            moveTo: function() {},
            lineTo: function() {},
            fill: function() {},
            stroke: function() {},
            fillText: function() {},
            strokeText: function() {},
            save: function() {},
            restore: function() {},
            translate: function() {},
            rotate: function() {},
        };
        const document = {
            getElementById: (id) => ({
                getContext: () => _ctxStub,
                addEventListener: (type, fn, opts) => {
                    _listeners['canvas_' + type] = { fn, opts };
                }
            }),
            addEventListener: (type, fn) => {
                _listeners['doc_' + type] = { fn };
            }
        };
        const window = {
            addEventListener: (type, fn) => {
                _listeners['window_' + type] = { fn };
            }
        };
        let _rafCallback = null;
        function requestAnimationFrame(cb) { _rafCallback = cb; }
    `;

    const evalCode = `
        ${domStub}
        ${src}
        ({
            // Constants
            CANVAS_WIDTH, CANVAS_HEIGHT, GROUND_HEIGHT,
            PIPE_WIDTH, PIPE_GAP, PIPE_SPEED, PIPE_SPACING,
            PIPE_MIN_TOP, PIPE_MAX_TOP,
            PIPE_CAP_HEIGHT, PIPE_CAP_OVERHANG,
            BIRD_X, BIRD_RADIUS, BIRD_START_Y,
            GRAVITY, FLAP_VELOCITY, MAX_FALL_SPEED,
            BOB_AMPLITUDE, BOB_FREQUENCY,
            FIRST_PIPE_DELAY,
            STATE_IDLE, STATE_PLAYING, STATE_GAME_OVER,
            // Mutable state via getters/setters
            bird, pipes,
            get score() { return score; },
            set score(v) { score = v; },
            get gameState() { return gameState; },
            set gameState(v) { gameState = v; },
            get distanceSinceLastPipe() { return distanceSinceLastPipe; },
            set distanceSinceLastPipe(v) { distanceSinceLastPipe = v; },
            get groundOffset() { return groundOffset; },
            set groundOffset(v) { groundOffset = v; },
            // Functions
            handleInput, resetGame, flap,
            updateBird, updatePipes, spawnPipe,
            checkGroundCollision, checkPipeCollisions, checkCollisions,
            updateScore, update,
            renderPipes, renderGround, renderBird, renderScore, render,
            renderBackground,
            // Stubs
            _ctxStub, _listeners, _renderCalls, _fillRects, _rafCallback,
        })
    `;

    return eval(evalCode);
}

// =====================================================================
//  SECTION 1 — AC-1: Pipe body color is #3cb043
// =====================================================================

section('AC-1: Pipe body color is #3cb043');

// Test 1.1: Source contains the correct pipe body color literal
{
    const bodyColorRegex = /ctx\.fillStyle\s*=\s*['"]#3cb043['"]/i;
    assert(bodyColorRegex.test(src), '1.1 Source contains ctx.fillStyle = "#3cb043" for pipe body');
}

// Test 1.2: renderPipes sets fillStyle to #3cb043 at runtime (single pipe)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g._renderCalls.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyCall = g._fillRects.find(c => c.fillStyle.toLowerCase() === '#3cb043');
    assert(bodyCall !== undefined, '1.2 renderPipes sets fillStyle to #3cb043 at runtime');
}

// Test 1.3: Body color appears before cap color in render sequence
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const firstBody = g._fillRects.findIndex(c => c.fillStyle.toLowerCase() === '#3cb043');
    const firstCap  = g._fillRects.findIndex(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assert(firstBody >= 0 && firstBody < firstCap, '1.3 Body color #3cb043 set before cap color #2d8a34');
}

// Test 1.4: Pipe body hex is case-insensitive match to #3CB043
{
    const match = src.match(/ctx\.fillStyle\s*=\s*['"]([^'"]+)['"]\s*;\s*\/\/\s*Green/);
    assert(match && match[1].toLowerCase() === '#3cb043', '1.4 Pipe body color extracted from source matches #3cb043');
}

// =====================================================================
//  SECTION 2 — AC-2: Pipe cap color is #2d8a34
// =====================================================================

section('AC-2: Pipe cap color is #2d8a34');

// Test 2.1: Source contains the correct pipe cap color literal
{
    const capColorRegex = /ctx\.fillStyle\s*=\s*['"]#2d8a34['"]/i;
    assert(capColorRegex.test(src), '2.1 Source contains ctx.fillStyle = "#2d8a34" for pipe caps');
}

// Test 2.2: renderPipes sets fillStyle to #2d8a34 at runtime
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capCall = g._fillRects.find(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assert(capCall !== undefined, '2.2 renderPipes sets fillStyle to #2d8a34 at runtime');
}

// Test 2.3: Cap color appears after body color in render sequence
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const lastBody = (() => {
        let idx = -1;
        g._fillRects.forEach((c, i) => { if (c.fillStyle.toLowerCase() === '#3cb043') idx = i; });
        return idx;
    })();
    const firstCap = g._fillRects.findIndex(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assert(firstCap > lastBody, '2.3 Cap color #2d8a34 appears after body color #3cb043');
}

// Test 2.4: Cap color hex is case-insensitive match to #2D8A34
{
    const match = src.match(/ctx\.fillStyle\s*=\s*['"]([^'"]+)['"]\s*;\s*\/\/\s*Darker green/);
    assert(match && match[1].toLowerCase() === '#2d8a34', '2.4 Pipe cap color extracted from source matches #2d8a34');
}

// =====================================================================
//  SECTION 3 — AC-3: Paint order — body before caps
// =====================================================================

section('AC-3: Paint order — body before caps');

// Test 3.1: Single pipe: body rects painted before cap rects
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyIndices = [];
    const capIndices  = [];
    g._fillRects.forEach((c, i) => {
        const fs = c.fillStyle.toLowerCase();
        if (fs === '#3cb043') bodyIndices.push(i);
        if (fs === '#2d8a34') capIndices.push(i);
    });
    const allBodyBeforeCap = bodyIndices.every(bi => capIndices.every(ci => bi < ci));
    assert(allBodyBeforeCap, '3.1 All body fillRects painted before all cap fillRects (single pipe)');
}

// Test 3.2: Exactly 2 body rects per pipe (top + bottom body)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyCount = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043').length;
    assertEqual(bodyCount, 2, '3.2 Exactly 2 body fillRects per pipe (top + bottom)');
}

// Test 3.3: Exactly 2 cap rects per pipe (top + bottom cap)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capCount = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34').length;
    assertEqual(capCount, 2, '3.3 Exactly 2 cap fillRects per pipe (top + bottom)');
}

// Test 3.4: Total 4 fillRect calls per pipe
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const total = g._fillRects.filter(c => {
        const fs = c.fillStyle.toLowerCase();
        return fs === '#3cb043' || fs === '#2d8a34';
    }).length;
    assertEqual(total, 4, '3.4 Total 4 pipe fillRect calls per pipe pair');
}

// Test 3.5: Multi-pipe: 2 pipes produce 4 body + 4 cap rects
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 100, gapY: 150, scored: false });
    g.pipes.push({ x: 300, gapY: 250, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyCount = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043').length;
    const capCount  = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34').length;
    assertEqual(bodyCount, 4, '3.5a 2 pipes produce 4 body fillRects');
    assertEqual(capCount, 4, '3.5b 2 pipes produce 4 cap fillRects');
}

// Test 3.6: Each pipe's body pair precedes its cap pair
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 100, gapY: 150, scored: false });
    g.pipes.push({ x: 300, gapY: 250, scored: false });
    g.renderPipes(g._ctxStub);
    // For each pipe iteration, body (2 rects) should come before caps (2 rects)
    // Pattern: body, body, cap, cap, body, body, cap, cap
    const colors = g._fillRects.map(c => c.fillStyle.toLowerCase());
    const pipeColors = colors.filter(c => c === '#3cb043' || c === '#2d8a34');
    const expectedPattern = ['#3cb043', '#3cb043', '#2d8a34', '#2d8a34', '#3cb043', '#3cb043', '#2d8a34', '#2d8a34'];
    let patternMatches = true;
    for (let i = 0; i < expectedPattern.length; i++) {
        if (pipeColors[i] !== expectedPattern[i]) { patternMatches = false; break; }
    }
    assert(patternMatches, '3.6 Multi-pipe: body-body-cap-cap pattern per pipe iteration');
}

// Test 3.7: 3 pipes produce 6 body + 6 cap rects
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 50, gapY: 100, scored: false });
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.pipes.push({ x: 350, gapY: 300, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyCount = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043').length;
    const capCount  = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34').length;
    assertEqual(bodyCount, 6, '3.7a 3 pipes produce 6 body fillRects');
    assertEqual(capCount, 6, '3.7b 3 pipes produce 6 cap fillRects');
}

// Test 3.8: 0 pipes produce 0 fillRects
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.renderPipes(g._ctxStub);
    assertEqual(g._fillRects.length, 0, '3.8 Empty pipes array produces 0 fillRect calls');
}

// Test 3.9: renderPipes uses correct iteration variable (let i)
{
    const renderPipesMatch = src.match(/function\s+renderPipes[\s\S]*?^}/m);
    if (renderPipesMatch) {
        assert(renderPipesMatch[0].includes('let i'), '3.9 renderPipes uses let i for iteration');
    } else {
        assert(false, '3.9 renderPipes function not found');
    }
}

// =====================================================================
//  SECTION 4 — AC-4: No geometry regressions
// =====================================================================

section('AC-4: No geometry regressions');

// Test 4.1: Top pipe body geometry — starts at y=0
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    assertEqual(bodyRects[0].y, 0, '4.1 Top pipe body starts at y=0');
}

// Test 4.2: Top pipe body height = gapY
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    assertEqual(bodyRects[0].h, 200, '4.2 Top pipe body height equals gapY (200)');
}

// Test 4.3: Top pipe body x = pipe.x
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    assertEqual(bodyRects[0].x, 200, '4.3 Top pipe body x equals pipe.x (200)');
}

// Test 4.4: Top pipe body width = PIPE_WIDTH (52)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    assertEqual(bodyRects[0].w, 52, '4.4 Top pipe body width equals PIPE_WIDTH (52)');
}

// Test 4.5: Bottom pipe body y = gapY + PIPE_GAP
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    assertEqual(bodyRects[1].y, 330, '4.5 Bottom pipe body starts at gapY + PIPE_GAP (200+130=330)');
}

// Test 4.6: Bottom pipe body height = groundY - (gapY + PIPE_GAP)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    const expectedH = (600 - 60) - 330; // groundY=540, bottomPipeTop=330 → 210
    assertEqual(bodyRects[1].h, expectedH, '4.6 Bottom pipe body height = groundY - bottomPipeTop (210)');
}

// Test 4.7: Top cap y = gapY - PIPE_CAP_HEIGHT
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assertEqual(capRects[0].y, 180, '4.7 Top cap y = gapY - PIPE_CAP_HEIGHT (200-20=180)');
}

// Test 4.8: Top cap height = PIPE_CAP_HEIGHT (20)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assertEqual(capRects[0].h, 20, '4.8 Top cap height = PIPE_CAP_HEIGHT (20)');
}

// Test 4.9: Top cap x = pipe.x - PIPE_CAP_OVERHANG
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assertEqual(capRects[0].x, 197, '4.9 Top cap x = pipe.x - PIPE_CAP_OVERHANG (200-3=197)');
}

// Test 4.10: Cap width = PIPE_WIDTH + 2*PIPE_CAP_OVERHANG (58)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assertEqual(capRects[0].w, 58, '4.10 Cap width = PIPE_WIDTH + 2*PIPE_CAP_OVERHANG (52+6=58)');
}

// Test 4.11: Bottom cap y = gapY + PIPE_GAP (same as bottom pipe body y)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assertEqual(capRects[1].y, 330, '4.11 Bottom cap y = gapY + PIPE_GAP (330)');
}

// Test 4.12: Bottom cap height = PIPE_CAP_HEIGHT (20)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assertEqual(capRects[1].h, 20, '4.12 Bottom cap height = PIPE_CAP_HEIGHT (20)');
}

// Test 4.13: Bottom cap x matches top cap x
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assertEqual(capRects[1].x, capRects[0].x, '4.13 Bottom cap x matches top cap x (both 197)');
}

// Test 4.14: Bottom cap width matches top cap width
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const capRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assertEqual(capRects[1].w, capRects[0].w, '4.14 Bottom cap width matches top cap width (both 58)');
}

// Test 4.15: PIPE_WIDTH constant is 52
{
    const g = createSandbox();
    assertEqual(g.PIPE_WIDTH, 52, '4.15 PIPE_WIDTH constant is 52');
}

// Test 4.16: PIPE_GAP constant is 130
{
    const g = createSandbox();
    assertEqual(g.PIPE_GAP, 130, '4.16 PIPE_GAP constant is 130');
}

// Test 4.17: PIPE_CAP_HEIGHT constant is 20
{
    const g = createSandbox();
    assertEqual(g.PIPE_CAP_HEIGHT, 20, '4.17 PIPE_CAP_HEIGHT constant is 20');
}

// Test 4.18: PIPE_CAP_OVERHANG constant is 3
{
    const g = createSandbox();
    assertEqual(g.PIPE_CAP_OVERHANG, 3, '4.18 PIPE_CAP_OVERHANG constant is 3');
}

// Test 4.19: PIPE_MIN_TOP is 50
{
    const g = createSandbox();
    assertEqual(g.PIPE_MIN_TOP, 50, '4.19 PIPE_MIN_TOP constant is 50');
}

// Test 4.20: PIPE_MAX_TOP is 360 (600 - 60 - 130 - 50)
{
    const g = createSandbox();
    assertEqual(g.PIPE_MAX_TOP, 360, '4.20 PIPE_MAX_TOP constant is 360');
}

// Test 4.21: Geometry with minimum gapY (50)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 50, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    assertEqual(bodyRects[0].h, 50, '4.21a Min gapY: top pipe body height = 50');
    assertEqual(bodyRects[1].y, 180, '4.21b Min gapY: bottom pipe starts at 50+130=180');
}

// Test 4.22: Geometry with maximum gapY (360)
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 360, scored: false });
    g.renderPipes(g._ctxStub);
    const bodyRects = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    assertEqual(bodyRects[0].h, 360, '4.22a Max gapY: top pipe body height = 360');
    assertEqual(bodyRects[1].y, 490, '4.22b Max gapY: bottom pipe starts at 360+130=490');
}

// =====================================================================
//  SECTION 5 — AC-5: Game plays normally
// =====================================================================

section('AC-5: Game plays normally');

// Test 5.1: Game starts in IDLE state
{
    const g = createSandbox();
    assertEqual(g.gameState, 'IDLE', '5.1 Game starts in IDLE state');
}

// Test 5.2: handleInput transitions from IDLE to PLAYING
{
    const g = createSandbox();
    g.handleInput();
    assertEqual(g.gameState, 'PLAYING', '5.2 handleInput() transitions IDLE → PLAYING');
}

// Test 5.3: Bird gets initial flap on transition to PLAYING
{
    const g = createSandbox();
    g.handleInput();
    assertEqual(g.bird.velocity, -280, '5.3 Bird gets FLAP_VELOCITY (-280) on transition to PLAYING');
}

// Test 5.4: Pipes spawn during PLAYING after distance accumulation
{
    const g = createSandbox();
    g.handleInput(); // IDLE → PLAYING
    // distanceSinceLastPipe seeded to PIPE_SPACING - FIRST_PIPE_DELAY = 160
    // Need 60 more px of travel: 60/120 = 0.5s
    g.update(0.05); // 6px
    g.update(0.05); // 6px
    g.update(0.05); // 6px
    g.update(0.05); // 6px
    g.update(0.05); // 6px
    g.update(0.05); // 6px
    g.update(0.05); // 6px
    g.update(0.05); // 6px
    g.update(0.05); // 6px
    g.update(0.05); // 6px = 60px total
    assert(g.pipes.length >= 1, '5.4 At least 1 pipe spawned after PLAYING state updates');
}

// Test 5.5: Pipe moves left over time
{
    const g = createSandbox();
    g.gameState = 'PLAYING';
    g.distanceSinceLastPipe = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 400, gapY: 200, scored: false });
    const startX = g.pipes[0].x;
    g.updatePipes(1/60);
    assert(g.pipes[0].x < startX, '5.5 Pipe moves left after updatePipes (dt=1/60)');
}

// Test 5.6: Score increments when bird passes pipe
{
    const g = createSandbox();
    g.gameState = 'PLAYING';
    g.score = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 50, gapY: 200, scored: false }); // pipe center at x=76 (50+52/2)
    // bird.x = 100 (BIRD_X) which is > 76 → should score
    g.updateScore();
    assertEqual(g.score, 1, '5.6 Score increments when bird passes pipe center');
}

// Test 5.7: Score doesn't double-count same pipe
{
    const g = createSandbox();
    g.gameState = 'PLAYING';
    g.score = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 50, gapY: 200, scored: false });
    g.updateScore();
    g.updateScore();
    assertEqual(g.score, 1, '5.7 Score only counted once per pipe (scored flag)');
}

// Test 5.8: Ground collision triggers GAME_OVER
{
    const g = createSandbox();
    g.gameState = 'PLAYING';
    g.bird.y = 540; // at ground level (CANVAS_HEIGHT - GROUND_HEIGHT = 540)
    g.checkCollisions();
    assertEqual(g.gameState, 'GAME_OVER', '5.8 Ground collision triggers GAME_OVER');
}

// Test 5.9: Pipe collision triggers GAME_OVER
{
    const g = createSandbox();
    g.gameState = 'PLAYING';
    g.pipes.length = 0;
    g.pipes.push({ x: 90, gapY: 50, scored: false }); // top pipe covers y 0-50, bird at y=300
    g.bird.y = 30; // bird inside top pipe
    g.checkCollisions();
    assertEqual(g.gameState, 'GAME_OVER', '5.9 Pipe collision triggers GAME_OVER');
}

// Test 5.10: resetGame restores IDLE state
{
    const g = createSandbox();
    g.gameState = 'GAME_OVER';
    g.pipes.push({ x: 200, gapY: 200, scored: true });
    g.score = 5;
    g.resetGame();
    assertEqual(g.gameState, 'IDLE', '5.10 resetGame restores IDLE state');
}

// Test 5.11: resetGame clears pipes
{
    const g = createSandbox();
    g.pipes.push({ x: 200, gapY: 200, scored: true });
    g.resetGame();
    assertEqual(g.pipes.length, 0, '5.11 resetGame clears pipes array');
}

// Test 5.12: resetGame resets score
{
    const g = createSandbox();
    g.score = 10;
    g.resetGame();
    assertEqual(g.score, 0, '5.12 resetGame resets score to 0');
}

// =====================================================================
//  SECTION 6 — AC-6: Old colors absent
// =====================================================================

section('AC-6: Old colors absent');

// Test 6.1: Old pipe body color #2ECC71 absent from renderPipes
{
    const renderPipesMatch = src.match(/function\s+renderPipes[\s\S]*?^}/m);
    const fnBody = renderPipesMatch ? renderPipesMatch[0] : '';
    assert(!fnBody.includes('#2ECC71') && !fnBody.includes('#2ecc71'), '6.1 Old body color #2ECC71 absent from renderPipes');
}

// Test 6.2: Old cap color #27AE60 absent from renderPipes
{
    const renderPipesMatch = src.match(/function\s+renderPipes[\s\S]*?^}/m);
    const fnBody = renderPipesMatch ? renderPipesMatch[0] : '';
    assert(!fnBody.includes('#27AE60') && !fnBody.includes('#27ae60'), '6.2 Old cap color #27AE60 absent from renderPipes');
}

// Test 6.3: Old body color #2ECC71 absent from entire file
{
    assert(!src.toLowerCase().includes('#2ecc71'), '6.3 Old body color #2ECC71 absent from entire file');
}

// Test 6.4: Old cap color #27AE60 absent from entire file
{
    assert(!src.toLowerCase().includes('#27ae60'), '6.4 Old cap color #27AE60 absent from entire file');
}

// Test 6.5: No occurrence of old green hex #2ECC in file
{
    assert(!src.includes('#2ECC') && !src.includes('#2ecc'), '6.5 No #2ECC prefix found anywhere in file');
}

// Test 6.6: No occurrence of old green hex #27AE in file
{
    assert(!src.includes('#27AE') && !src.includes('#27ae'), '6.6 No #27AE prefix found anywhere in file');
}

// Test 6.7: renderPipes runtime doesn't produce #2ECC71
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const oldBodyUsed = g._fillRects.some(c => c.fillStyle.toLowerCase() === '#2ecc71');
    assert(!oldBodyUsed, '6.7 renderPipes never sets fillStyle to old #2ECC71 at runtime');
}

// Test 6.8: renderPipes runtime doesn't produce #27AE60
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const oldCapUsed = g._fillRects.some(c => c.fillStyle.toLowerCase() === '#27ae60');
    assert(!oldCapUsed, '6.8 renderPipes never sets fillStyle to old #27AE60 at runtime');
}

// Test 6.9: Only 2 distinct fillStyle colors used in renderPipes
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const colors = new Set(g._fillRects.map(c => c.fillStyle.toLowerCase()));
    assertEqual(colors.size, 2, '6.9 Exactly 2 distinct fillStyle colors used in renderPipes');
}

// Test 6.10: The 2 colors are exactly #3cb043 and #2d8a34
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.renderPipes(g._ctxStub);
    const colors = [...new Set(g._fillRects.map(c => c.fillStyle.toLowerCase()))].sort();
    const expected = ['#2d8a34', '#3cb043'];
    assert(
        colors.length === 2 && colors[0] === expected[0] && colors[1] === expected[1],
        '6.10 Exactly colors #3cb043 and #2d8a34 used — no others'
    );
}

// =====================================================================
//  SECTION 7 — Extra: File integrity
// =====================================================================

section('Extra: File integrity');

// Test 7.1: game.js loads without SyntaxError
{
    let loadedOk = false;
    try {
        createSandbox();
        loadedOk = true;
    } catch (e) {
        // noop
    }
    assert(loadedOk, '7.1 game.js loads and evaluates without SyntaxError');
}

// Test 7.2: No merge conflict markers in source
{
    assert(!src.includes('<<<<<<<'), '7.2a No <<<<<<< merge conflict markers');
    assert(!src.includes('======='), '7.2b No ======= merge conflict markers');
    assert(!src.includes('>>>>>>>'), '7.2c No >>>>>>> merge conflict markers');
}

// Test 7.3: renderPipes function exists
{
    assert(typeof createSandbox().renderPipes === 'function', '7.3 renderPipes function exists');
}

// =====================================================================
//  SECTION 8 — Extra: Non-pipe colors unchanged
// =====================================================================

section('Extra: Non-pipe colors unchanged');

// Test 8.1: Sky background color is #70c5ce
{
    assert(src.includes('#70c5ce'), '8.1 Sky background color #70c5ce unchanged');
}

// Test 8.2: Ground dirt color is #8B5E3C
{
    assert(src.includes('#8B5E3C'), '8.2 Ground dirt color #8B5E3C unchanged');
}

// Test 8.3: Grass accent color is #5CBF2A
{
    assert(src.includes('#5CBF2A'), '8.3 Grass accent color #5CBF2A unchanged');
}

// Test 8.4: Bird body color is #f5c842
{
    assert(src.toLowerCase().includes('#f5c842'), '8.4 Bird body color #f5c842 unchanged');
}

// Test 8.5: Bird beak color is #e07020
{
    assert(src.toLowerCase().includes('#e07020'), '8.5 Bird beak color #e07020 unchanged');
}

// Test 8.6: Score text color is #FFFFFF
{
    assert(src.includes('#FFFFFF') || src.includes('#ffffff'), '8.6 Score text color #FFFFFF unchanged');
}

// Test 8.7: Ground hash line color is #7A5232
{
    assert(src.includes('#7A5232'), '8.7 Ground hash line color #7A5232 unchanged');
}

// Test 8.8: Bird outline color is #d4a020
{
    assert(src.toLowerCase().includes('#d4a020'), '8.8 Bird outline color #d4a020 unchanged');
}

// =====================================================================
//  SECTION 9 — Extra: Render integration (pipe colors in full render)
// =====================================================================

section('Extra: Render integration');

// Test 9.1: Full render() in PLAYING state produces pipe colors in correct order
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.gameState = 'PLAYING';
    g.pipes.length = 0;
    g.pipes.push({ x: 200, gapY: 200, scored: false });
    g.render(g._ctxStub);
    const pipeBodyCalls = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    const pipeCapCalls  = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assert(pipeBodyCalls.length === 2 && pipeCapCalls.length === 2,
        '9.1 Full render() in PLAYING produces correct pipe body + cap fillRect counts');
}

// Test 9.2: Full render() in IDLE state with empty pipes produces no pipe colors
{
    const g = createSandbox();
    g._fillRects.length = 0;
    g.gameState = 'IDLE';
    g.pipes.length = 0;
    g.render(g._ctxStub);
    const pipeBodyCalls = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#3cb043');
    const pipeCapCalls  = g._fillRects.filter(c => c.fillStyle.toLowerCase() === '#2d8a34');
    assert(pipeBodyCalls.length === 0 && pipeCapCalls.length === 0,
        '9.2 Full render() in IDLE with empty pipes produces no pipe-colored rects');
}

// =====================================================================
//  SUMMARY
// =====================================================================

console.log('\n' + '='.repeat(60));
console.log(`RESULTS: ${passed} passed, ${failed} failed (${passed + failed} total)`);
console.log('='.repeat(60));

if (failures.length > 0) {
    console.log('\nFAILURES:');
    failures.forEach(f => console.log(`  • ${f}`));
}

if (bugs.length > 0) {
    console.log('\nBUGS FOUND:');
    bugs.forEach(b => {
        console.log(`  🐛 BUG-${b.id}: ${b.summary}`);
        console.log(`     Steps: ${b.steps}`);
        console.log(`     Expected: ${b.expected}`);
        console.log(`     Actual: ${b.actual}`);
    });
} else {
    console.log('\nBUGS FOUND: NONE');
}

// Pre-existing issue note (not caused by this fix):
console.log('\nPRE-EXISTING ISSUE (not caused by this fix):');
console.log('  BUG-002: renderPipes(ctx) called unconditionally in render() (no state guard).');
console.log('  Harmless since pipes array is empty in IDLE. Pre-dates this branch.');

console.log('\n' + (failed === 0 ? '🚀 RECOMMENDATION: SHIP IT ✅' : '🔴 RECOMMENDATION: FIX FAILURES BEFORE SHIPPING'));

process.exit(failed > 0 ? 1 : 0);
