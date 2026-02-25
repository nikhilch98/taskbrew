/**
 * TS-043 — QA Verification: Merge of fix/cd-021-pipe-colors into main
 * Merge commit: 582c302
 * Source branch: fix/cd-021-pipe-colors (commit d713039)
 * Review: RV-023 APPROVED
 *
 * Acceptance Criteria:
 *   1. main branch builds/runs without errors
 *   2. Pipe colors render correctly (#3cb043 body, #2d8a34 caps)
 *   3. No regressions in game functionality (collision, scoring, overlays)
 *   4. Merge commit is clean (no conflict markers)
 *
 * Run: node flappy-bird/ts-043-merge-qa.test.js
 */

const fs   = require('fs');
const path = require('path');

// ===== TEST HELPERS =====

let passed = 0;
let failed = 0;
const failures = [];
const bugReports = [];

function assert(condition, message) {
    if (condition) {
        passed++;
        console.log(`  \u2705 ${message}`);
    } else {
        failed++;
        console.log(`  \u274C ${message}`);
        failures.push(message);
    }
}

function assertEqual(actual, expected, message) {
    if (actual === expected) {
        passed++;
        console.log(`  \u2705 ${message}`);
    } else {
        failed++;
        const detail = `expected: ${JSON.stringify(expected)}, got: ${JSON.stringify(actual)}`;
        console.log(`  \u274C ${message} \u2014 ${detail}`);
        failures.push(`${message} \u2014 ${detail}`);
    }
}

function section(title) {
    console.log(`\n\u2501\u2501\u2501 ${title} \u2501\u2501\u2501`);
}

function reportBug(id, title, severity, description, reproSteps) {
    bugReports.push({ id, title, severity, description, reproSteps });
}

// ===== CONSTANTS =====

const SPEC_PIPE_BODY_COLOR = '#3cb043';
const SPEC_PIPE_CAP_COLOR  = '#2d8a34';
const OLD_PIPE_BODY_COLOR  = '#2ECC71';
const OLD_PIPE_CAP_COLOR   = '#27AE60';

// ===== READ SOURCE =====

const gameJsPath = path.resolve(__dirname, 'game.js');
let src;
try {
    src = fs.readFileSync(gameJsPath, 'utf8');
} catch (err) {
    console.error(`FATAL: Cannot read ${gameJsPath}: ${err.message}`);
    process.exit(1);
}

// ===== SANDBOX =====

function createSandbox() {
    const domStub = `
        const _listeners = {};
        const _ctxState = { fillStyle: '', strokeStyle: '', lineWidth: 0 };

        const document = {
            getElementById: (id) => ({
                getContext: () => ({
                    get fillStyle() { return _ctxState.fillStyle; },
                    set fillStyle(v) { _ctxState.fillStyle = v; },
                    get strokeStyle() { return _ctxState.strokeStyle; },
                    set strokeStyle(v) { _ctxState.strokeStyle = v; },
                    get lineWidth() { return _ctxState.lineWidth; },
                    set lineWidth(v) { _ctxState.lineWidth = v; },
                    fillRect: () => {},
                    beginPath: () => {},
                    arc: () => {},
                    moveTo: () => {},
                    lineTo: () => {},
                    closePath: () => {},
                    fill: () => {},
                    stroke: () => {},
                    save: () => {},
                    restore: () => {},
                    translate: () => {},
                    rotate: () => {},
                    strokeText: () => {},
                    fillText: () => {},
                    ellipse: () => {},
                    font: '',
                    textAlign: '',
                    textBaseline: '',
                    lineJoin: '',
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

    const evalCode = `
        ${domStub}
        ${src}
        ({
            CANVAS_WIDTH, CANVAS_HEIGHT, GROUND_HEIGHT,
            BIRD_X, BIRD_RADIUS, BIRD_START_Y,
            GRAVITY, FLAP_VELOCITY, MAX_FALL_SPEED,
            PIPE_WIDTH, PIPE_GAP, PIPE_SPEED, PIPE_SPACING,
            PIPE_MIN_TOP, PIPE_MAX_TOP,
            PIPE_CAP_HEIGHT, PIPE_CAP_OVERHANG,
            FIRST_PIPE_DELAY,
            STATE_IDLE, STATE_PLAYING, STATE_GAME_OVER,
            bird, pipes,
            get score() { return score; },
            set score(v) { score = v; },
            get gameState() { return gameState; },
            set gameState(v) { gameState = v; },
            get bobTimer() { return bobTimer; },
            set bobTimer(v) { bobTimer = v; },
            get groundOffset() { return groundOffset; },
            set groundOffset(v) { groundOffset = v; },
            get lastTimestamp() { return lastTimestamp; },
            set lastTimestamp(v) { lastTimestamp = v; },
            get distanceSinceLastPipe() { return distanceSinceLastPipe; },
            set distanceSinceLastPipe(v) { distanceSinceLastPipe = v; },

            // Functions
            shouldSpawnPipe, spawnPipe, updatePipes, renderPipes,
            handleInput, resetGame, flap, updateBird, updateScore,
            update, render, checkCollisions,
            renderBackground, renderGround, renderBird,
            renderIdleOverlay, renderGameOverOverlay, renderScore,
            circleRectCollision, checkGroundCollision, checkPipeCollisions,

            // Test infra
            _listeners, _rafCallback, _ctxState
        })
    `;

    return eval(evalCode);
}

/**
 * Create a mock canvas context that records all draw operations.
 */
function createMockCtx() {
    const calls = [];
    const state = { fillStyle: '', strokeStyle: '', lineWidth: 0 };

    return {
        _calls: calls,
        _state: state,
        get fillStyle() { return state.fillStyle; },
        set fillStyle(v) {
            state.fillStyle = v;
            calls.push({ method: 'set_fillStyle', args: [v] });
        },
        get strokeStyle() { return state.strokeStyle; },
        set strokeStyle(v) {
            state.strokeStyle = v;
            calls.push({ method: 'set_strokeStyle', args: [v] });
        },
        get lineWidth() { return state.lineWidth; },
        set lineWidth(v) {
            state.lineWidth = v;
            calls.push({ method: 'set_lineWidth', args: [v] });
        },
        fillRect: function(x, y, w, h) {
            calls.push({ method: 'fillRect', args: [x, y, w, h], fillStyle: state.fillStyle });
        },
        beginPath: function() { calls.push({ method: 'beginPath' }); },
        arc: function(...args) { calls.push({ method: 'arc', args }); },
        fill: function() { calls.push({ method: 'fill', fillStyle: state.fillStyle }); },
        stroke: function() { calls.push({ method: 'stroke', strokeStyle: state.strokeStyle }); },
        moveTo: function(...args) { calls.push({ method: 'moveTo', args }); },
        lineTo: function(...args) { calls.push({ method: 'lineTo', args }); },
        closePath: function() { calls.push({ method: 'closePath' }); },
        save: function() { calls.push({ method: 'save' }); },
        restore: function() { calls.push({ method: 'restore' }); },
        translate: function(x, y) { calls.push({ method: 'translate', args: [x, y] }); },
        rotate: function(a) { calls.push({ method: 'rotate', args: [a] }); },
        strokeText: function(...args) { calls.push({ method: 'strokeText', args }); },
        fillText: function(...args) { calls.push({ method: 'fillText', args }); },
        ellipse: function(...args) { calls.push({ method: 'ellipse', args }); },
        font: '',
        textAlign: '',
        textBaseline: '',
        lineJoin: '',
    };
}

// ===== BEGIN TESTS =====

console.log('=======================================================');
console.log('  TS-043: QA Verification \u2014 Merge of');
console.log('  fix/cd-021-pipe-colors into main');
console.log('  Merge commit: 582c302');
console.log('=======================================================');

// =================================================================
// AC-1: main branch builds/runs without errors
// =================================================================

section('AC-1: main branch builds/runs without errors');

let sb;
try {
    sb = createSandbox();
    assert(true, 'game.js loads without syntax errors');
    assert(typeof sb.renderPipes === 'function', 'renderPipes function exists');
    assert(typeof sb.renderBackground === 'function', 'renderBackground function exists');
    assert(typeof sb.renderGround === 'function', 'renderGround function exists');
    assert(typeof sb.renderBird === 'function', 'renderBird function exists');
    assert(typeof sb.render === 'function', 'render function exists');
    assert(typeof sb.update === 'function', 'update function exists');
    assert(typeof sb.handleInput === 'function', 'handleInput function exists');
    assert(typeof sb.checkCollisions === 'function', 'checkCollisions function exists');
    assert(typeof sb.updateScore === 'function', 'updateScore function exists');
    assert(typeof sb.circleRectCollision === 'function', 'circleRectCollision function exists');
    assert(typeof sb.renderIdleOverlay === 'function', 'renderIdleOverlay function exists');
    assert(typeof sb.renderGameOverOverlay === 'function', 'renderGameOverOverlay function exists');
} catch (e) {
    assert(false, `game.js fails to load: ${e.message}`);
    console.error('\nFATAL: Cannot proceed without valid game.js');
    process.exit(1);
}

// Verify game loop can run without errors
(() => {
    const sb = createSandbox();
    let error = null;
    try {
        for (let frame = 0; frame < 60; frame++) {
            sb.update(1/60);
            const ctx = createMockCtx();
            sb.render(ctx);
        }
    } catch (e) {
        error = e.message;
    }
    assert(error === null, error ? `Game loop error: ${error}` : 'IDLE game loop runs 60 frames without errors');
})();

(() => {
    const sb = createSandbox();
    sb.handleInput(); // IDLE -> PLAYING
    let error = null;
    try {
        for (let frame = 0; frame < 300; frame++) {
            sb.update(1/60);
            const ctx = createMockCtx();
            sb.render(ctx);
            if (sb.gameState === 'GAME_OVER') {
                sb.gameState = 'PLAYING';
                sb.bird.y = sb.BIRD_START_Y;
                sb.bird.velocity = 0;
            }
        }
    } catch (e) {
        error = e.message;
    }
    assert(error === null, error ? `PLAYING loop error: ${error}` : 'PLAYING game loop runs 300 frames without errors');
})();

// =================================================================
// AC-2: Pipe colors render correctly (#3cb043 body, #2d8a34 caps)
// =================================================================

section('AC-2: Pipe body color is #3cb043');

(() => {
    const sb = createSandbox();
    sb.pipes.length = 0;
    sb.pipes.push({ x: 200, gapY: 200, scored: false });

    const ctx = createMockCtx();
    sb.renderPipes(ctx);

    const fillRects = ctx._calls.filter(c => c.method === 'fillRect');

    // Body fills are the first two fillRect calls per pipe
    assertEqual(fillRects[0].fillStyle, SPEC_PIPE_BODY_COLOR,
        `Top pipe body fillStyle === '${SPEC_PIPE_BODY_COLOR}'`);
    assertEqual(fillRects[1].fillStyle, SPEC_PIPE_BODY_COLOR,
        `Bottom pipe body fillStyle === '${SPEC_PIPE_BODY_COLOR}'`);
})();

section('AC-2: Pipe cap color is #2d8a34');

(() => {
    const sb = createSandbox();
    sb.pipes.length = 0;
    sb.pipes.push({ x: 200, gapY: 200, scored: false });

    const ctx = createMockCtx();
    sb.renderPipes(ctx);

    const fillRects = ctx._calls.filter(c => c.method === 'fillRect');

    // Cap fills are the 3rd and 4th fillRect calls per pipe
    assertEqual(fillRects[2].fillStyle, SPEC_PIPE_CAP_COLOR,
        `Top pipe cap fillStyle === '${SPEC_PIPE_CAP_COLOR}'`);
    assertEqual(fillRects[3].fillStyle, SPEC_PIPE_CAP_COLOR,
        `Bottom pipe cap fillStyle === '${SPEC_PIPE_CAP_COLOR}'`);
})();

// Source-level verification at exact lines
(() => {
    const lines = src.split('\n');

    // Line 241 (0-indexed: 240)
    const line241 = lines[240];
    assert(line241 && line241.includes('#3cb043'),
        'Line 241 contains pipe body color #3cb043');

    // Line 250 (0-indexed: 249)
    const line250 = lines[249];
    assert(line250 && line250.includes('#2d8a34'),
        'Line 250 contains pipe cap color #2d8a34');
})();

// Paint order: body before caps
(() => {
    const sb = createSandbox();
    sb.pipes.length = 0;
    sb.pipes.push({ x: 200, gapY: 200, scored: false });

    const ctx = createMockCtx();
    sb.renderPipes(ctx);

    const fillRects = ctx._calls.filter(c => c.method === 'fillRect');
    assertEqual(fillRects.length, 4, 'One pipe pair produces exactly 4 fillRect calls');
    assertEqual(fillRects[0].fillStyle, SPEC_PIPE_BODY_COLOR, 'Call #1: body color (top)');
    assertEqual(fillRects[1].fillStyle, SPEC_PIPE_BODY_COLOR, 'Call #2: body color (bottom)');
    assertEqual(fillRects[2].fillStyle, SPEC_PIPE_CAP_COLOR, 'Call #3: cap color (top)');
    assertEqual(fillRects[3].fillStyle, SPEC_PIPE_CAP_COLOR, 'Call #4: cap color (bottom)');
})();

// Old colors ABSENT
(() => {
    const rpStart = src.indexOf('function renderPipes');
    const rpEnd = src.indexOf('\n}', rpStart) + 2;
    const rpBody = src.substring(rpStart, rpEnd);

    assert(!rpBody.includes(OLD_PIPE_BODY_COLOR),
        `renderPipes does NOT contain old body color (${OLD_PIPE_BODY_COLOR})`);
    assert(!rpBody.includes(OLD_PIPE_CAP_COLOR),
        `renderPipes does NOT contain old cap color (${OLD_PIPE_CAP_COLOR})`);
})();

// Runtime: 500-frame simulation — no old colors leak
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.distanceSinceLastPipe = sb.PIPE_SPACING - 1;

    let oldColorFound = false;
    for (let frame = 0; frame < 500; frame++) {
        sb.update(1/60);
        if (sb.gameState !== 'PLAYING') {
            sb.gameState = 'PLAYING';
            sb.bird.y = sb.BIRD_START_Y;
            sb.bird.velocity = 0;
        }
        if (sb.pipes.length > 0) {
            const ctx = createMockCtx();
            sb.renderPipes(ctx);
            const styles = ctx._calls
                .filter(c => c.method === 'fillRect')
                .map(c => c.fillStyle);
            if (styles.includes(OLD_PIPE_BODY_COLOR) || styles.includes(OLD_PIPE_CAP_COLOR)) {
                oldColorFound = true;
                break;
            }
        }
    }
    assert(!oldColorFound, '500-frame simulation: old colors never appear');
})();

// =================================================================
// AC-3: No regressions in game functionality
// =================================================================

section('AC-3a: Collision detection regression');

// Ground collision
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 530;
    sb.bird.velocity = 100;
    sb.pipes.length = 0;
    sb.checkCollisions();
    assertEqual(sb.gameState, 'GAME_OVER', 'Ground collision triggers GAME_OVER');
    assertEqual(sb.bird.y, 525, 'Bird clamped to ground surface (y=525)');
})();

// Pipe collision — top pipe
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 50;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 90, gapY: 200, scored: false });
    sb.checkCollisions();
    assertEqual(sb.gameState, 'GAME_OVER', 'Top pipe collision triggers GAME_OVER');
})();

// Pipe collision — bottom pipe
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 340;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 90, gapY: 200, scored: false });
    sb.checkCollisions();
    assertEqual(sb.gameState, 'GAME_OVER', 'Bottom pipe collision triggers GAME_OVER');
})();

// Bird in gap — no collision
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 265;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 90, gapY: 200, scored: false });
    sb.checkCollisions();
    assertEqual(sb.gameState, 'PLAYING', 'Bird in gap \u2192 no collision, stays PLAYING');
})();

// circleRectCollision function works
(() => {
    const sb = createSandbox();
    assertEqual(sb.circleRectCollision(50, 50, 10, 0, 0, 100, 100), true,
        'circleRectCollision: circle inside rect \u2192 true');
    assertEqual(sb.circleRectCollision(-50, 50, 10, 0, 0, 100, 100), false,
        'circleRectCollision: circle far outside \u2192 false');
})();

section('AC-3b: Scoring regression');

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.pipes.length = 0;
    sb.pipes.push({ x: 20, gapY: 200, scored: false });
    sb.bird.y = 265;
    sb.bird.velocity = 0;
    sb.score = 0;
    sb.updateScore();
    assertEqual(sb.score, 1, 'Scoring works: pipe passed by bird increments score');
    assertEqual(sb.pipes[0].scored, true, 'Pipe marked as scored');
})();

// Score does not double-count
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.pipes.length = 0;
    sb.pipes.push({ x: 20, gapY: 200, scored: true });
    sb.bird.y = 265;
    sb.bird.velocity = 0;
    sb.score = 1;
    sb.updateScore();
    assertEqual(sb.score, 1, 'Already-scored pipe does NOT increment score again');
})();

// Score not incremented on death frame (CD-020 fix)
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 50; // Will collide with top pipe
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 90, gapY: 200, scored: false });
    sb.score = 0;

    sb.update(0.001);
    assertEqual(sb.gameState, 'GAME_OVER', 'Collision detected on update');
    assertEqual(sb.score, 0, 'Score NOT incremented on death frame (CD-020)');
})();

section('AC-3c: Overlay regression');

// IDLE overlay renders
(() => {
    const sb = createSandbox();
    sb.gameState = 'IDLE';
    const ctx = createMockCtx();
    sb.render(ctx);

    const textCalls = ctx._calls.filter(c => c.method === 'fillText');
    const hasTitle = textCalls.some(c => c.args && c.args[0] === 'Flappy Bird');
    const hasInstruction = textCalls.some(c => c.args && String(c.args[0]).includes('Space'));
    assert(hasTitle, 'IDLE overlay: "Flappy Bird" title rendered');
    assert(hasInstruction, 'IDLE overlay: Start instruction rendered');
})();

// GAME_OVER overlay renders
(() => {
    const sb = createSandbox();
    sb.gameState = 'GAME_OVER';
    sb.score = 5;
    const ctx = createMockCtx();
    sb.render(ctx);

    const textCalls = ctx._calls.filter(c => c.method === 'fillText');
    const hasGameOver = textCalls.some(c => c.args && c.args[0] === 'Game Over');
    const hasScore = textCalls.some(c => c.args && String(c.args[0]).includes('Score'));
    const hasRestart = textCalls.some(c => c.args && String(c.args[0]).includes('Restart'));
    assert(hasGameOver, 'GAME_OVER overlay: "Game Over" text rendered');
    assert(hasScore, 'GAME_OVER overlay: Score displayed');
    assert(hasRestart, 'GAME_OVER overlay: Restart instruction rendered');

    // Semi-transparent overlay
    const overlayFill = ctx._calls.find(c =>
        c.method === 'fillRect' && c.fillStyle && c.fillStyle.includes('rgba')
    );
    assert(overlayFill !== undefined, 'GAME_OVER overlay: semi-transparent dark overlay rendered');
})();

// PLAYING renders score
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.score = 3;
    sb.pipes.length = 0;
    const ctx = createMockCtx();
    sb.render(ctx);

    const scoreCalls = ctx._calls.filter(c =>
        c.method === 'fillText' && c.args && String(c.args[0]) === '3'
    );
    assert(scoreCalls.length > 0, 'PLAYING state: score "3" rendered on screen');
})();

section('AC-3d: State machine regression');

// IDLE -> PLAYING
(() => {
    const sb = createSandbox();
    assertEqual(sb.gameState, 'IDLE', 'Initial state is IDLE');
    sb.handleInput();
    assertEqual(sb.gameState, 'PLAYING', 'handleInput from IDLE \u2192 PLAYING');
})();

// GAME_OVER -> IDLE (via handleInput -> resetGame)
(() => {
    const sb = createSandbox();
    sb.gameState = 'GAME_OVER';
    sb.handleInput();
    assertEqual(sb.gameState, 'IDLE', 'handleInput from GAME_OVER \u2192 IDLE (resetGame)');
})();

// resetGame clears state
(() => {
    const sb = createSandbox();
    sb.pipes.push({ x: 100, gapY: 200, scored: true });
    sb.score = 5;
    sb.resetGame();
    assertEqual(sb.pipes.length, 0, 'resetGame clears pipes');
    assertEqual(sb.score, 0, 'resetGame resets score');
    assertEqual(sb.gameState, 'IDLE', 'resetGame sets state to IDLE');
    assertEqual(sb.bird.y, sb.BIRD_START_Y, 'resetGame resets bird position');
})();

section('AC-3e: Pipe geometry regression');

(() => {
    const sb = createSandbox();
    const groundY = sb.CANVAS_HEIGHT - sb.GROUND_HEIGHT; // 540

    sb.pipes.length = 0;
    sb.pipes.push({ x: 200, gapY: 200, scored: false });

    const ctx = createMockCtx();
    sb.renderPipes(ctx);

    const fillRects = ctx._calls.filter(c => c.method === 'fillRect');

    // Top pipe body
    assertEqual(fillRects[0].args[0], 200, 'Top body x === 200');
    assertEqual(fillRects[0].args[1], 0, 'Top body y === 0');
    assertEqual(fillRects[0].args[2], sb.PIPE_WIDTH, 'Top body w === PIPE_WIDTH');
    assertEqual(fillRects[0].args[3], 200, 'Top body h === gapY (200)');

    // Bottom pipe body
    const bottomPipeTop = 200 + sb.PIPE_GAP; // 330
    assertEqual(fillRects[1].args[0], 200, 'Bottom body x === 200');
    assertEqual(fillRects[1].args[1], bottomPipeTop, 'Bottom body y === 330');
    assertEqual(fillRects[1].args[3], groundY - bottomPipeTop, 'Bottom body h === 210');

    // Top cap
    assertEqual(fillRects[2].args[0], 200 - sb.PIPE_CAP_OVERHANG, 'Top cap x with overhang');
    assertEqual(fillRects[2].args[1], 200 - sb.PIPE_CAP_HEIGHT, 'Top cap y');
    assertEqual(fillRects[2].args[2], sb.PIPE_WIDTH + sb.PIPE_CAP_OVERHANG * 2, 'Top cap width with overhang');
    assertEqual(fillRects[2].args[3], sb.PIPE_CAP_HEIGHT, 'Top cap height');

    // Bottom cap
    assertEqual(fillRects[3].args[0], 200 - sb.PIPE_CAP_OVERHANG, 'Bottom cap x with overhang');
    assertEqual(fillRects[3].args[1], bottomPipeTop, 'Bottom cap y at gap bottom');
})();

section('AC-3f: Render layer order regression');

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.pipes.length = 0;
    sb.pipes.push({ x: 200, gapY: 200, scored: false });

    const ctx = createMockCtx();
    sb.render(ctx);

    const fillRects = ctx._calls.filter(c => c.method === 'fillRect');

    const skyIdx = fillRects.findIndex(c => c.fillStyle === '#70c5ce');
    const pipeIdx = fillRects.findIndex(c => c.fillStyle === SPEC_PIPE_BODY_COLOR);
    const groundIdx = fillRects.findIndex(c => c.fillStyle === '#8B5E3C');

    assert(skyIdx < pipeIdx, 'Sky rendered before pipes');
    assert(pipeIdx < groundIdx, 'Pipes rendered before ground');

    const birdSaveIdx = ctx._calls.findIndex(c => c.method === 'save');
    assert(groundIdx < birdSaveIdx, 'Ground rendered before bird');
})();

section('AC-3g: Non-pipe colors unchanged');

(() => {
    const nonPipeColors = {
        '#70c5ce': 'Sky background',
        '#8B5E3C': 'Ground dirt',
        '#5CBF2A': 'Grass accent',
        '#f5c842': 'Bird body',
        '#d4a020': 'Bird outline',
        '#e07020': 'Bird beak',
        '#7A5232': 'Ground hash lines',
    };
    for (const [color, desc] of Object.entries(nonPipeColors)) {
        assert(src.includes(color), `${desc} (${color}) still present in source`);
    }
})();

// =================================================================
// AC-4: Merge commit is clean (no conflict markers)
// =================================================================

section('AC-4: Merge commit is clean');

(() => {
    assert(!src.includes('<<<<<<<'), 'No <<<<<<< conflict markers');
    assert(!src.includes('>>>>>>>'), 'No >>>>>>> conflict markers');
    assert(!src.includes('=======\n'), 'No ======= conflict separators');

    // Verify renderPipes has exactly 2 fillStyle assignments
    const rpStart = src.indexOf('function renderPipes');
    const rpEnd = src.indexOf('\n}', rpStart) + 2;
    const rpBody = src.substring(rpStart, rpEnd);
    const fillStyleMatches = rpBody.match(/ctx\.fillStyle\s*=/g) || [];
    assertEqual(fillStyleMatches.length, 2,
        'renderPipes has exactly 2 fillStyle assignments (body + cap)');

    // File line count is reasonable
    const lineCount = src.split('\n').length;
    assert(lineCount > 500 && lineCount < 700,
        `File has ${lineCount} lines (expected 500-700)`);
})();

// Check index.html
(() => {
    const htmlPath = path.resolve(__dirname, 'index.html');
    const html = fs.readFileSync(htmlPath, 'utf8');
    assert(!html.includes('<<<<<<<'), 'index.html: no conflict markers');
    assert(html.includes('gameCanvas'), 'index.html: canvas element present');
    assert(html.includes('game.js'), 'index.html: game.js script referenced');
    assert(html.includes('style.css'), 'index.html: style.css referenced');
})();

// Check style.css
(() => {
    const cssPath = path.resolve(__dirname, 'style.css');
    const css = fs.readFileSync(cssPath, 'utf8');
    assert(!css.includes('<<<<<<<'), 'style.css: no conflict markers');
    assert(css.includes('canvas'), 'style.css: canvas styling present');
})();

// =================================================================
// SUMMARY
// =================================================================

console.log('\n=======================================================');
console.log(`  TS-043 RESULTS: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('=======================================================');

if (failures.length > 0) {
    console.log('\n\u274C Failed tests:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

if (bugReports.length > 0) {
    console.log('\n\uD83D\uDC1B Bugs found:');
    bugReports.forEach(b => {
        console.log(`\n  ${b.id}: ${b.title} [${b.severity}]`);
        console.log(`  Description: ${b.description}`);
        console.log(`  Repro: ${b.reproSteps}`);
    });
}

if (failures.length === 0) {
    console.log('\n\u2705 ALL ACCEPTANCE CRITERIA VERIFIED.');
    console.log('   Merge of fix/cd-021-pipe-colors into main PASSES QA.');
    console.log('   - AC-1: Builds/runs without errors \u2714');
    console.log('   - AC-2: Pipe colors correct (#3cb043 body, #2d8a34 caps) \u2714');
    console.log('   - AC-3: No regressions (collision, scoring, overlays) \u2714');
    console.log('   - AC-4: Merge commit clean (no conflict markers) \u2714');
}

process.exit(failed > 0 ? 1 : 0);
