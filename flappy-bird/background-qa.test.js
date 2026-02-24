/**
 * TS-018 — QA Verification for CD-010: Background Rendering
 * Automated test suite using Node.js (no external dependencies)
 *
 * Acceptance criteria tested:
 *  1. Canvas shows light sky blue background (#70c5ce)
 *  2. Background covers entire canvas every frame (acts as clear)
 *  3. No clearRect call exists in the file — background fill replaces it
 *  4. Background renders in ALL states (IDLE, PLAYING, GAME_OVER)
 *  5. No visual regressions in pipes, ground, bird, or overlay rendering
 *
 * Additional verifications:
 *  6. renderBackground is a dedicated function (extracted from render)
 *  7. renderBackground is first draw call in render() (Layer 0)
 *  8. No new global variables introduced
 *  9. Existing render functions still work correctly
 * 10. JSDoc present on renderBackground
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
        console.log(`  \u2705 ${message}`);
    } else {
        failed++;
        console.log(`  \u274c ${message}`);
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
        console.log(`  \u274c ${message}  \u2014 ${detail}`);
        failures.push(`${message} \u2014 ${detail}`);
    }
}

function reportBug(title, description, repro) {
    bugs.push({ title, description, repro });
}

function section(title) {
    console.log(`\n\u2501\u2501\u2501 ${title} \u2501\u2501\u2501`);
}

// ─── read source once ───

const src = fs.readFileSync(path.join(__dirname, 'game.js'), 'utf8');
const lines = src.split('\n');

// ─── Canvas mock infrastructure ───

function createMockCtx() {
    const calls = [];
    const state = {
        fillStyle: '',
        strokeStyle: '',
        lineWidth: 0,
        font: '',
        textAlign: '',
        textBaseline: '',
        lineJoin: '',
        transforms: [],
        currentTransform: { translateX: 0, translateY: 0, rotation: 0 },
    };

    const ctx = {
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
        get font() { return state.font; },
        set font(v) {
            state.font = v;
            calls.push({ method: 'set_font', args: [v] });
        },
        get textAlign() { return state.textAlign; },
        set textAlign(v) {
            state.textAlign = v;
            calls.push({ method: 'set_textAlign', args: [v] });
        },
        get textBaseline() { return state.textBaseline; },
        set textBaseline(v) {
            state.textBaseline = v;
            calls.push({ method: 'set_textBaseline', args: [v] });
        },
        get lineJoin() { return state.lineJoin; },
        set lineJoin(v) {
            state.lineJoin = v;
            calls.push({ method: 'set_lineJoin', args: [v] });
        },
        fillRect: function(...args) { calls.push({ method: 'fillRect', args, fillStyle: state.fillStyle }); },
        strokeRect: function(...args) { calls.push({ method: 'strokeRect', args }); },
        clearRect: function(...args) { calls.push({ method: 'clearRect', args }); },
        beginPath: function() { calls.push({ method: 'beginPath', args: [] }); },
        arc: function(...args) { calls.push({ method: 'arc', args }); },
        fill: function() { calls.push({ method: 'fill', args: [], fillStyle: state.fillStyle }); },
        stroke: function() { calls.push({ method: 'stroke', args: [], strokeStyle: state.strokeStyle, lineWidth: state.lineWidth }); },
        moveTo: function(...args) { calls.push({ method: 'moveTo', args }); },
        lineTo: function(...args) { calls.push({ method: 'lineTo', args }); },
        closePath: function() { calls.push({ method: 'closePath', args: [] }); },
        save: function() {
            state.transforms.push({ ...state.currentTransform });
            calls.push({ method: 'save', args: [] });
        },
        restore: function() {
            if (state.transforms.length > 0) {
                state.currentTransform = state.transforms.pop();
            }
            calls.push({ method: 'restore', args: [] });
        },
        translate: function(x, y) {
            state.currentTransform.translateX = x;
            state.currentTransform.translateY = y;
            calls.push({ method: 'translate', args: [x, y] });
        },
        rotate: function(angle) {
            state.currentTransform.rotation = angle;
            calls.push({ method: 'rotate', args: [angle] });
        },
        ellipse: function(...args) { calls.push({ method: 'ellipse', args }); },
        strokeText: function(...args) { calls.push({ method: 'strokeText', args, strokeStyle: state.strokeStyle }); },
        fillText: function(...args) { calls.push({ method: 'fillText', args, fillStyle: state.fillStyle }); },
    };

    return ctx;
}

// ─── DOM stub + sandbox eval ───

const domStub = `
    const _listeners = {};
    const document = {
        getElementById: (id) => ({
            getContext: () => ({
                fillStyle: '',
                strokeStyle: '',
                lineWidth: 0,
                font: '',
                textAlign: '',
                textBaseline: '',
                lineJoin: '',
                fillRect: () => {},
                strokeRect: () => {},
                clearRect: () => {},
                beginPath: () => {},
                arc: () => {},
                fill: () => {},
                stroke: () => {},
                moveTo: () => {},
                lineTo: () => {},
                closePath: () => {},
                save: () => {},
                restore: () => {},
                translate: () => {},
                rotate: () => {},
                ellipse: () => {},
                strokeText: () => {},
                fillText: () => {},
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

let sandbox = {};
try {
    const evalCode = `
        ${domStub}
        ${src}
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
            get distanceSinceLastPipe() { return distanceSinceLastPipe; },
            set distanceSinceLastPipe(v) { distanceSinceLastPipe = v; },
            handleInput, resetGame, flap,
            update, render, gameLoop,
            renderBackground: typeof renderBackground !== 'undefined' ? renderBackground : undefined,
            renderGround: typeof renderGround !== 'undefined' ? renderGround : undefined,
            renderBird: typeof renderBird !== 'undefined' ? renderBird : undefined,
            renderPipes: typeof renderPipes !== 'undefined' ? renderPipes : undefined,
            renderScore: typeof renderScore !== 'undefined' ? renderScore : undefined,
            renderIdleOverlay: typeof renderIdleOverlay !== 'undefined' ? renderIdleOverlay : undefined,
            renderGameOverOverlay: typeof renderGameOverOverlay !== 'undefined' ? renderGameOverOverlay : undefined,
            _listeners, _rafCallback
        })
    `;
    sandbox = eval(evalCode);
} catch (e) {
    console.error('  \u274c Failed to evaluate game.js:', e.message);
    failed++;
    failures.push('game.js evaluation failed: ' + e.message);
}

console.log('========================================================');
console.log('  TS-018: QA Verification for CD-010 Background Rendering');
console.log('========================================================');

// ================================================================
// 1. Acceptance Criteria: Canvas shows light sky blue background (#70c5ce)
// ================================================================

section('1. Background color is #70c5ce');

// 1a. Source contains the color literal
assert(src.includes("#70c5ce"), 'Color #70c5ce present in source');

// 1b. renderBackground function sets fillStyle to #70c5ce
if (sandbox.renderBackground) {
    const mockCtx = createMockCtx();
    sandbox.renderBackground(mockCtx);

    const fillStyleCall = mockCtx._calls.find(c =>
        c.method === 'set_fillStyle' && c.args[0] === '#70c5ce'
    );
    assert(fillStyleCall !== undefined, 'renderBackground sets fillStyle to #70c5ce');

    const fillRectCall = mockCtx._calls.find(c => c.method === 'fillRect');
    assert(fillRectCall !== undefined, 'renderBackground calls fillRect');
    if (fillRectCall) {
        assertEqual(fillRectCall.fillStyle, '#70c5ce', 'fillRect uses #70c5ce fill');
    }
} else {
    assert(false, 'renderBackground function exists');
}

// 1c. Full render() also produces #70c5ce as the first fill
if (sandbox.render) {
    const mockCtx = createMockCtx();
    sandbox.gameState = 'IDLE';
    sandbox.render(mockCtx);

    const firstFillRect = mockCtx._calls.find(c => c.method === 'fillRect');
    assert(firstFillRect !== undefined, 'render() calls fillRect');
    if (firstFillRect) {
        assertEqual(firstFillRect.fillStyle, '#70c5ce', 'First fillRect in render() uses sky blue #70c5ce');
    }
}

// ================================================================
// 2. Acceptance Criteria: Background covers entire canvas every frame
// ================================================================

section('2. Background covers entire canvas (acts as clear)');

if (sandbox.renderBackground) {
    const mockCtx = createMockCtx();
    sandbox.renderBackground(mockCtx);

    const fillRectCall = mockCtx._calls.find(c => c.method === 'fillRect');
    if (fillRectCall) {
        assertEqual(fillRectCall.args[0], 0, 'Background fillRect x === 0');
        assertEqual(fillRectCall.args[1], 0, 'Background fillRect y === 0');
        assertEqual(fillRectCall.args[2], 400, 'Background fillRect width === CANVAS_WIDTH (400)');
        assertEqual(fillRectCall.args[3], 600, 'Background fillRect height === CANVAS_HEIGHT (600)');
    }

    // Verify it uses CANVAS_WIDTH and CANVAS_HEIGHT (not magic numbers) in source
    const renderBgBody = src.slice(
        src.indexOf('function renderBackground'),
        src.indexOf('}', src.indexOf('function renderBackground')) + 1
    );
    assert(
        renderBgBody.includes('CANVAS_WIDTH') && renderBgBody.includes('CANVAS_HEIGHT'),
        'renderBackground uses CANVAS_WIDTH and CANVAS_HEIGHT constants (no magic numbers)'
    );
}

// 2b. Every call to render() produces the background fill first
if (sandbox.render) {
    // Test across multiple frames
    for (let frame = 0; frame < 3; frame++) {
        const mockCtx = createMockCtx();
        sandbox.render(mockCtx);

        const firstFillRect = mockCtx._calls.find(c => c.method === 'fillRect');
        assert(
            firstFillRect && firstFillRect.fillStyle === '#70c5ce' &&
            firstFillRect.args[0] === 0 && firstFillRect.args[1] === 0 &&
            firstFillRect.args[2] === 400 && firstFillRect.args[3] === 600,
            `Frame ${frame + 1}: First fillRect is full-canvas sky blue (acts as clear)`
        );
    }
}

// ================================================================
// 3. Acceptance Criteria: No clearRect call exists in the file
// ================================================================

section('3. No clearRect in source (background fill replaces it)');

// 3a. Source code search
const clearRectCount = (src.match(/clearRect/g) || []).length;
assertEqual(clearRectCount, 0, 'No clearRect found in game.js source');

// 3b. Functional test: render() never calls clearRect on the context
if (sandbox.render) {
    const mockCtx = createMockCtx();
    sandbox.gameState = 'IDLE';
    sandbox.render(mockCtx);

    const clearRectCalls = mockCtx._calls.filter(c => c.method === 'clearRect');
    assertEqual(clearRectCalls.length, 0, 'render() in IDLE state: no clearRect calls on context');

    const mockCtx2 = createMockCtx();
    sandbox.gameState = 'PLAYING';
    sandbox.render(mockCtx2);

    const clearRectCalls2 = mockCtx2._calls.filter(c => c.method === 'clearRect');
    assertEqual(clearRectCalls2.length, 0, 'render() in PLAYING state: no clearRect calls on context');

    const mockCtx3 = createMockCtx();
    sandbox.gameState = 'GAME_OVER';
    sandbox.render(mockCtx3);

    const clearRectCalls3 = mockCtx3._calls.filter(c => c.method === 'clearRect');
    assertEqual(clearRectCalls3.length, 0, 'render() in GAME_OVER state: no clearRect calls on context');
}

// ================================================================
// 4. Acceptance Criteria: Background renders in ALL states
// ================================================================

section('4. Background renders in all states (IDLE, PLAYING, GAME_OVER)');

const states = ['IDLE', 'PLAYING', 'GAME_OVER'];

for (const state of states) {
    if (sandbox.render) {
        sandbox.gameState = state;
        // Setup minimal state for PLAYING to avoid crash
        if (state === 'PLAYING') {
            sandbox.pipes.length = 0;
        }
        const mockCtx = createMockCtx();
        sandbox.render(mockCtx);

        // Find the sky background fill
        const skyFill = mockCtx._calls.find(c =>
            c.method === 'fillRect' &&
            c.fillStyle === '#70c5ce' &&
            c.args[0] === 0 && c.args[1] === 0 &&
            c.args[2] === 400 && c.args[3] === 600
        );
        assert(skyFill !== undefined, `${state}: Sky background (#70c5ce) covers full canvas`);

        // Verify it's the FIRST fillRect
        const firstFillRect = mockCtx._calls.find(c => c.method === 'fillRect');
        assert(
            firstFillRect === skyFill,
            `${state}: Sky background is the FIRST fillRect call (Layer 0)`
        );
    }
}

// Reset state
sandbox.resetGame();

// ================================================================
// 5. Acceptance Criteria: No visual regressions
// ================================================================

section('5. No visual regressions — pipes, ground, bird, overlays');

// 5a. Pipes still render after background
if (sandbox.render) {
    sandbox.gameState = 'PLAYING';
    sandbox.pipes.length = 0;
    sandbox.pipes.push({ x: 200, gapY: 200, scored: false });

    const mockCtx = createMockCtx();
    sandbox.render(mockCtx);

    const skyIdx = mockCtx._calls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#70c5ce');
    const pipeIdx = mockCtx._calls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#2ECC71');
    assert(pipeIdx > skyIdx, 'Pipes render after sky background (not occluded)');

    // 5b. Ground renders after pipes
    const groundIdx = mockCtx._calls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#8B5E3C');
    assert(groundIdx > pipeIdx, 'Ground renders after pipes');

    // 5c. Bird renders after ground
    const birdSaveIdx = mockCtx._calls.findIndex(c => c.method === 'save');
    assert(birdSaveIdx > groundIdx, 'Bird renders after ground');

    // 5d. Score overlay renders in PLAYING state
    const scoreStrokeText = mockCtx._calls.find(c => c.method === 'strokeText');
    assert(scoreStrokeText !== undefined, 'PLAYING: Score text rendered');

    sandbox.pipes.length = 0;
}

// 5e. IDLE overlay still renders
if (sandbox.render) {
    sandbox.gameState = 'IDLE';
    const mockCtx = createMockCtx();
    sandbox.render(mockCtx);

    const idleText = mockCtx._calls.find(c =>
        c.method === 'fillText' && typeof c.args[0] === 'string' && c.args[0].includes('Flappy Bird')
    );
    assert(idleText !== undefined, 'IDLE: "Flappy Bird" title text still rendered');

    const instructionText = mockCtx._calls.find(c =>
        c.method === 'fillText' && typeof c.args[0] === 'string' && c.args[0].includes('Start')
    );
    assert(instructionText !== undefined, 'IDLE: "Press Space or Tap to Start" instruction still rendered');
}

// 5f. GAME_OVER overlay still renders
if (sandbox.render) {
    sandbox.gameState = 'GAME_OVER';
    const mockCtx = createMockCtx();
    sandbox.render(mockCtx);

    // Game over dark overlay
    const darkOverlay = mockCtx._calls.find(c =>
        c.method === 'fillRect' && c.fillStyle === 'rgba(0, 0, 0, 0.5)'
    );
    assert(darkOverlay !== undefined, 'GAME_OVER: Semi-transparent dark overlay rendered');

    const gameOverText = mockCtx._calls.find(c =>
        c.method === 'fillText' && typeof c.args[0] === 'string' && c.args[0].includes('Game Over')
    );
    assert(gameOverText !== undefined, 'GAME_OVER: "Game Over" text still rendered');

    const restartText = mockCtx._calls.find(c =>
        c.method === 'fillText' && typeof c.args[0] === 'string' && c.args[0].includes('Restart')
    );
    assert(restartText !== undefined, 'GAME_OVER: "Press Space or Tap to Restart" text still rendered');
}

sandbox.resetGame();

// 5g. Render order preserved: sky -> pipes -> ground -> bird -> overlays
section('5g. Full render layer order verification');

if (sandbox.render) {
    sandbox.gameState = 'PLAYING';
    sandbox.pipes.length = 0;
    sandbox.pipes.push({ x: 200, gapY: 200, scored: false });
    sandbox.score = 5;

    const mockCtx = createMockCtx();
    sandbox.render(mockCtx);

    const skyIdx = mockCtx._calls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#70c5ce');
    const pipeIdx = mockCtx._calls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#2ECC71');
    const groundIdx = mockCtx._calls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#8B5E3C');
    const birdIdx = mockCtx._calls.findIndex(c => c.method === 'save');
    const scoreIdx = mockCtx._calls.findIndex(c => c.method === 'strokeText');

    assert(skyIdx >= 0, 'Layer 0 — Sky background present');
    assert(pipeIdx > skyIdx, 'Layer 1 — Pipes after sky');
    assert(groundIdx > pipeIdx, 'Layer 2 — Ground after pipes');
    assert(birdIdx > groundIdx, 'Layer 3 — Bird after ground');
    assert(scoreIdx > birdIdx, 'Layer 4 — Score overlay after bird');

    sandbox.pipes.length = 0;
    sandbox.resetGame();
}

// ================================================================
// 6. renderBackground is a dedicated function
// ================================================================

section('6. renderBackground is a dedicated function');

assert(typeof sandbox.renderBackground === 'function', 'renderBackground is a function');

// 6a. It's defined with the function keyword at file level
const renderBgDeclPattern = /^function\s+renderBackground\s*\(\s*ctx\s*\)/m;
assert(renderBgDeclPattern.test(src), 'renderBackground declared as function renderBackground(ctx)');

// 6b. render() calls renderBackground(ctx) — not inline fillStyle/fillRect
const renderBody = src.slice(
    src.indexOf('function render(ctx)'),
    src.indexOf('// ===== GAME LOOP =====')
);
assert(renderBody.includes('renderBackground(ctx)'), 'render() calls renderBackground(ctx)');

// 6c. The inline ctx.fillStyle + ctx.fillRect that was previously in render() is GONE
// (There should NOT be a fillStyle = '#70c5ce' directly inside render())
const renderBodyNoSub = renderBody.replace(/renderBackground\(ctx\)/, ''); // Remove the function call
const hasInlineSky = renderBodyNoSub.includes('#70c5ce');
assert(!hasInlineSky, 'No inline #70c5ce in render() body (properly extracted to renderBackground)');

// ================================================================
// 7. renderBackground is the first draw call (Layer 0)
// ================================================================

section('7. renderBackground is first draw call in render()');

// 7a. Source analysis: renderBackground(ctx) appears before other render calls
const bgCallPos = renderBody.indexOf('renderBackground(ctx)');
const pipesCallPos = renderBody.indexOf('renderPipes(ctx)');
const groundCallPos = renderBody.indexOf('renderGround(ctx)');
const birdCallPos = renderBody.indexOf('renderBird(ctx)');

assert(bgCallPos < pipesCallPos, 'renderBackground called before renderPipes in render()');
assert(bgCallPos < groundCallPos, 'renderBackground called before renderGround in render()');
assert(bgCallPos < birdCallPos, 'renderBackground called before renderBird in render()');

// 7b. Comment or label refers to Layer 0
const lineBeforeBgCall = renderBody.slice(0, bgCallPos);
const lastCommentBefore = lineBeforeBgCall.split('\n').filter(l => l.trim().startsWith('//')).pop() || '';
assert(
    lastCommentBefore.includes('Layer 0') || lastCommentBefore.includes('background') || lastCommentBefore.includes('Sky'),
    'Comment above renderBackground references sky/background/Layer 0'
);

// ================================================================
// 8. No new global variables introduced
// ================================================================

section('8. No new global variables');

const allowedGlobals = new Set([
    'CANVAS_WIDTH', 'CANVAS_HEIGHT', 'GROUND_HEIGHT',
    'BIRD_X', 'BIRD_RADIUS', 'BIRD_START_Y',
    'GRAVITY', 'FLAP_VELOCITY', 'MAX_FALL_SPEED',
    'PIPE_WIDTH', 'PIPE_GAP', 'PIPE_SPEED', 'PIPE_SPACING', 'PIPE_MIN_TOP', 'PIPE_MAX_TOP',
    'BOB_AMPLITUDE', 'BOB_FREQUENCY',
    'PIPE_CAP_HEIGHT', 'PIPE_CAP_OVERHANG',
    'STATE_IDLE', 'STATE_PLAYING', 'STATE_GAME_OVER',
    'canvas', 'ctx',
    'bird', 'pipes', 'score', 'bobTimer', 'groundOffset',
    'gameState', 'lastTimestamp', 'spacePressed',
    'distanceSinceLastPipe',
    'FIRST_PIPE_DELAY',
]);

const allowedFunctions = new Set([
    'handleInput', 'resetGame', 'flap',
    'update', 'render', 'gameLoop',
    'updateBird', 'shouldSpawnPipe', 'spawnPipe', 'updatePipes',
    'renderGround', 'renderBird', 'renderPipes',
    'renderScore', 'renderIdleOverlay', 'renderGameOverOverlay',
    'renderBackground',  // The new function from CD-010
    'checkGroundCollision', 'checkPipeCollisions', 'checkCollisions',
    'circleRectCollision', 'clamp',
    'updateScore',
]);

const globalDeclPattern = /^(?:const|let|var)\s+(\w+)\s*=/gm;
const funcDeclPattern = /^function\s+(\w+)\s*\(/gm;

const declaredVars = new Set();
let match;
while ((match = globalDeclPattern.exec(src)) !== null) {
    declaredVars.add(match[1]);
}

const declaredFuncs = new Set();
while ((match = funcDeclPattern.exec(src)) !== null) {
    declaredFuncs.add(match[1]);
}

const unexpectedVars = [...declaredVars].filter(v => !allowedGlobals.has(v));
assertEqual(unexpectedVars.length, 0,
    `No unexpected global variables (found: ${unexpectedVars.length > 0 ? unexpectedVars.join(', ') : 'none'})`
);

const unexpectedFuncs = [...declaredFuncs].filter(f => !allowedFunctions.has(f));
assertEqual(unexpectedFuncs.length, 0,
    `No unexpected global functions (found: ${unexpectedFuncs.length > 0 ? unexpectedFuncs.join(', ') : 'none'})`
);

// Verify the new renderBackground function IS declared
assert(declaredFuncs.has('renderBackground'), 'renderBackground function declared at top level');

// ================================================================
// 9. Existing render functions still work correctly
// ================================================================

section('9. Existing render sub-functions still operational');

// 9a. renderGround still works
if (sandbox.renderGround) {
    const mockCtx = createMockCtx();
    sandbox.groundOffset = 0;
    try {
        sandbox.renderGround(mockCtx);
        const dirtFill = mockCtx._calls.find(c => c.method === 'fillRect' && c.fillStyle === '#8B5E3C');
        assert(dirtFill !== undefined, 'renderGround still renders brown dirt (#8B5E3C)');
        const grassFill = mockCtx._calls.find(c => c.method === 'fillRect' && c.fillStyle === '#5CBF2A');
        assert(grassFill !== undefined, 'renderGround still renders green grass (#5CBF2A)');
    } catch (e) {
        assert(false, `renderGround throws error: ${e.message}`);
    }
}

// 9b. renderBird still works
if (sandbox.renderBird) {
    sandbox.bird.x = 100;
    sandbox.bird.y = 300;
    sandbox.bird.rotation = 0;
    const mockCtx = createMockCtx();
    try {
        sandbox.renderBird(mockCtx);
        const bodyFill = mockCtx._calls.find(c => c.method === 'fill' && c.fillStyle === '#F7DC6F');
        assert(bodyFill !== undefined, 'renderBird still renders yellow body (#F7DC6F)');
    } catch (e) {
        assert(false, `renderBird throws error: ${e.message}`);
    }
}

// 9c. renderPipes still works
if (sandbox.renderPipes) {
    sandbox.pipes.length = 0;
    sandbox.pipes.push({ x: 200, gapY: 200, scored: false });
    const mockCtx = createMockCtx();
    try {
        sandbox.renderPipes(mockCtx);
        const pipeFill = mockCtx._calls.find(c => c.method === 'fillRect' && c.fillStyle === '#2ECC71');
        assert(pipeFill !== undefined, 'renderPipes still renders green pipes (#2ECC71)');
    } catch (e) {
        assert(false, `renderPipes throws error: ${e.message}`);
    }
    sandbox.pipes.length = 0;
}

// 9d. renderScore still works
if (sandbox.renderScore) {
    sandbox.gameState = 'PLAYING';
    sandbox.score = 42;
    const mockCtx = createMockCtx();
    try {
        sandbox.renderScore(mockCtx);
        const scoreFill = mockCtx._calls.find(c => c.method === 'fillText');
        assert(scoreFill !== undefined, 'renderScore still renders score text');
    } catch (e) {
        assert(false, `renderScore throws error: ${e.message}`);
    }
}

// 9e. renderIdleOverlay still works
if (sandbox.renderIdleOverlay) {
    const mockCtx = createMockCtx();
    try {
        sandbox.renderIdleOverlay(mockCtx);
        const titleText = mockCtx._calls.find(c =>
            c.method === 'fillText' && typeof c.args[0] === 'string' && c.args[0].includes('Flappy Bird')
        );
        assert(titleText !== undefined, 'renderIdleOverlay still renders "Flappy Bird" title');
    } catch (e) {
        assert(false, `renderIdleOverlay throws error: ${e.message}`);
    }
}

// 9f. renderGameOverOverlay still works
if (sandbox.renderGameOverOverlay) {
    const mockCtx = createMockCtx();
    try {
        sandbox.renderGameOverOverlay(mockCtx);
        const gameOverText = mockCtx._calls.find(c =>
            c.method === 'fillText' && typeof c.args[0] === 'string' && c.args[0].includes('Game Over')
        );
        assert(gameOverText !== undefined, 'renderGameOverOverlay still renders "Game Over" text');
    } catch (e) {
        assert(false, `renderGameOverOverlay throws error: ${e.message}`);
    }
}

sandbox.resetGame();

// ================================================================
// 10. JSDoc on renderBackground
// ================================================================

section('10. Documentation — JSDoc on renderBackground');

const renderBgIdx = src.indexOf('function renderBackground');
const srcBefore = src.slice(Math.max(0, renderBgIdx - 300), renderBgIdx);
const hasJSDoc = srcBefore.includes('/**') && srcBefore.includes('*/');
assert(hasJSDoc, 'renderBackground has JSDoc comment');

// Check JSDoc mentions Layer 0 or background or canvas clear
const jsDocBlock = srcBefore.slice(srcBefore.lastIndexOf('/**'));
assert(
    jsDocBlock.includes('Layer 0') || jsDocBlock.includes('background') || jsDocBlock.includes('canvas clear'),
    'JSDoc mentions Layer 0, background, or canvas clear'
);

// Check JSDoc has @param tag
assert(jsDocBlock.includes('@param'), 'JSDoc includes @param tag for ctx');

// ================================================================
// 11. renderBackground is pure — no side effects
// ================================================================

section('11. renderBackground purity');

if (sandbox.renderBackground) {
    // Call renderBackground multiple times and verify identical output each time
    const mockCtx1 = createMockCtx();
    const mockCtx2 = createMockCtx();

    sandbox.renderBackground(mockCtx1);
    sandbox.renderBackground(mockCtx2);

    const calls1 = mockCtx1._calls.map(c => JSON.stringify(c));
    const calls2 = mockCtx2._calls.map(c => JSON.stringify(c));

    assertEqual(calls1.length, calls2.length, 'renderBackground produces same number of draw calls each time');
    const identical = calls1.every((c, i) => c === calls2[i]);
    assert(identical, 'renderBackground produces identical draw calls (deterministic)');

    // Exactly 2 operations: set fillStyle + fillRect
    assertEqual(mockCtx1._calls.length, 2, 'renderBackground makes exactly 2 context operations (set fillStyle, fillRect)');
}

// ================================================================
// 12. Edge case: renderBackground called standalone
// ================================================================

section('12. Edge case — renderBackground standalone');

if (sandbox.renderBackground) {
    // Calling renderBackground with a fresh mock should not throw
    try {
        const mockCtx = createMockCtx();
        sandbox.renderBackground(mockCtx);
        assert(true, 'renderBackground callable standalone without errors');
    } catch (e) {
        assert(false, `renderBackground standalone call throws: ${e.message}`);
    }
}

// ================================================================
// SUMMARY
// ================================================================

console.log('\n========================================================');
console.log(`  TS-018 QA RESULTS: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('========================================================');

if (failures.length > 0) {
    console.log('\n\u274c Failed tests:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

if (bugs.length > 0) {
    console.log('\n\ud83d\udc1b Bugs found:');
    bugs.forEach((b, i) => {
        console.log(`\n  Bug #${i + 1}: ${b.title}`);
        console.log(`  Description: ${b.description}`);
        console.log(`  Repro: ${b.repro}`);
    });
}

if (failures.length === 0 && bugs.length === 0) {
    console.log('\n\u2705 All acceptance criteria verified. CD-010 background rendering passes QA.');
}

console.log('\n--- Acceptance Criteria Summary ---');
console.log('  [' + (failures.some(f => f.includes('#70c5ce') && f.includes('color')) ? '\u274c' : '\u2705') + '] Canvas shows light sky blue background (#70c5ce)');
console.log('  [' + (failures.some(f => f.includes('entire canvas') || f.includes('covers full canvas')) ? '\u274c' : '\u2705') + '] Background covers entire canvas every frame (acts as clear)');
console.log('  [' + (failures.some(f => f.includes('clearRect')) ? '\u274c' : '\u2705') + '] No clearRect call exists \u2014 background fill replaces it');
console.log('  [' + (failures.some(f => f.includes('IDLE') || f.includes('PLAYING') || f.includes('GAME_OVER')) ? '\u274c' : '\u2705') + '] Background renders in ALL states (IDLE, PLAYING, GAME_OVER)');
console.log('  [' + (failures.some(f => f.includes('regression') || f.includes('still render')) ? '\u274c' : '\u2705') + '] No visual regressions in pipes, ground, bird, or overlay rendering');

process.exit(failed > 0 ? 1 : 0);
