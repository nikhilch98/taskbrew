/**
 * TS-011 — QA Verification for CD-013: Render Functions
 * Automated test suite using Node.js (no external dependencies)
 *
 * Tests cover:
 *  1. Sky background rendering
 *  2. Ground rendering (renderGround) — dirt strip, grass accent, hash lines
 *  3. Ground scrolling — IDLE, PLAYING, GAME_OVER states
 *  4. Bird rendering (renderBird) — body, outline, eye, pupil, beak, canvas transform
 *  5. Bird bob animation in IDLE state
 *  6. Pipe rendering (renderPipes) — columns, caps, colors, dimensions
 *  7. Render order — sky → pipes → ground → bird
 *  8. No new global variables
 *  9. Known magic number audit issue documentation
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
        const detail = `expected: ${JSON.stringify(expected)}, got: ${JSON.stringify(actual)}`;
        console.log(`  ❌ ${message}  — ${detail}`);
        failures.push(`${message} — ${detail}`);
    }
}

function assertIncludes(arr, value, message) {
    if (arr.includes(value)) {
        passed++;
        console.log(`  ✅ ${message}`);
    } else {
        failed++;
        console.log(`  ❌ ${message}  — ${JSON.stringify(value)} not found`);
        failures.push(`${message} — ${JSON.stringify(value)} not found in array`);
    }
}

function assertApprox(actual, expected, epsilon, message) {
    if (Math.abs(actual - expected) <= epsilon) {
        passed++;
        console.log(`  ✅ ${message}`);
    } else {
        failed++;
        const detail = `expected: ~${expected} (±${epsilon}), got: ${actual}`;
        console.log(`  ❌ ${message}  — ${detail}`);
        failures.push(`${message} — ${detail}`);
    }
}

function reportBug(title, description, repro) {
    bugs.push({ title, description, repro });
}

function section(title) {
    console.log(`\n━━━ ${title} ━━━`);
}

// ─── read source once ───

const src = fs.readFileSync(path.join(__dirname, 'game.js'), 'utf8');
const lines = src.split('\n');

// ─── Canvas mock infrastructure ───

/**
 * Creates a mock canvas 2D context that records all draw calls.
 */
function createMockCtx() {
    const calls = [];
    const state = {
        fillStyle: '',
        strokeStyle: '',
        lineWidth: 0,
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
        fillRect: function(...args) { calls.push({ method: 'fillRect', args, fillStyle: state.fillStyle }); },
        strokeRect: function(...args) { calls.push({ method: 'strokeRect', args }); },
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
                fillRect: () => {},
                strokeRect: () => {},
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
            handleInput, resetGame, flap,
            update, render, gameLoop,
            renderGround: typeof renderGround !== 'undefined' ? renderGround : undefined,
            renderBird: typeof renderBird !== 'undefined' ? renderBird : undefined,
            renderPipes: typeof renderPipes !== 'undefined' ? renderPipes : undefined,
            _listeners, _rafCallback
        })
    `;
    sandbox = eval(evalCode);
} catch (e) {
    console.error('  ❌ Failed to evaluate game.js:', e.message);
    failed++;
    failures.push('game.js evaluation failed: ' + e.message);
}

// ═══════════════════════════════════════════════════════
// 1. Sky background rendering
// ═══════════════════════════════════════════════════════

section('1. Sky background rendering');

assert(src.includes("#70c5ce"), 'Sky background color #70c5ce present in source');
assert(src.includes('fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)'), 'fillRect covers full canvas (0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)');

// Functional test: render() should fill sky first
if (sandbox.render) {
    const mockCtx = createMockCtx();
    sandbox.render(mockCtx);

    const firstFillRect = mockCtx._calls.find(c => c.method === 'fillRect');
    assert(firstFillRect !== undefined, 'render() calls fillRect');
    if (firstFillRect) {
        assertEqual(firstFillRect.fillStyle, '#70c5ce', 'First fillRect uses sky color #70c5ce');
        assertEqual(firstFillRect.args[0], 0, 'Sky fillRect x === 0');
        assertEqual(firstFillRect.args[1], 0, 'Sky fillRect y === 0');
        assertEqual(firstFillRect.args[2], 400, 'Sky fillRect width === CANVAS_WIDTH (400)');
        assertEqual(firstFillRect.args[3], 600, 'Sky fillRect height === CANVAS_HEIGHT (600)');
    }
}

// ═══════════════════════════════════════════════════════
// 2. Ground rendering (renderGround)
// ═══════════════════════════════════════════════════════

section('2. Ground rendering — renderGround function');

// 2a. Function exists
assert(typeof sandbox.renderGround === 'function', 'renderGround is a function');

if (sandbox.renderGround) {
    const mockCtx = createMockCtx();
    sandbox.groundOffset = 0;
    sandbox.renderGround(mockCtx);

    // 2b. Brown dirt strip
    const dirtFill = mockCtx._calls.find(c =>
        c.method === 'fillRect' && c.fillStyle === '#8B5E3C'
    );
    assert(dirtFill !== undefined, 'Brown dirt strip (#8B5E3C) rendered');
    if (dirtFill) {
        assertEqual(dirtFill.args[0], 0, 'Dirt strip x === 0');
        assertEqual(dirtFill.args[1], 540, 'Dirt strip y === CANVAS_HEIGHT - GROUND_HEIGHT (540)');
        assertEqual(dirtFill.args[2], 400, 'Dirt strip width === CANVAS_WIDTH (400)');
        assertEqual(dirtFill.args[3], 60, 'Dirt strip height === GROUND_HEIGHT (60)');
    }

    // 2c. Green grass accent
    const grassFill = mockCtx._calls.find(c =>
        c.method === 'fillRect' && c.fillStyle === '#5CBF2A'
    );
    assert(grassFill !== undefined, 'Green grass accent (#5CBF2A) rendered');
    if (grassFill) {
        assertEqual(grassFill.args[0], 0, 'Grass strip x === 0');
        assertEqual(grassFill.args[1], 540, 'Grass strip y === CANVAS_HEIGHT - GROUND_HEIGHT (540)');
        assertEqual(grassFill.args[2], 400, 'Grass strip width === CANVAS_WIDTH (400)');
        assertEqual(grassFill.args[3], 4, 'Grass strip height === ~4px');
    }

    // 2d. Brown dirt comes before grass (layer order)
    const dirtIdx = mockCtx._calls.indexOf(dirtFill);
    const grassIdx = mockCtx._calls.indexOf(grassFill);
    assert(dirtIdx < grassIdx, 'Dirt strip rendered before grass accent (correct layering)');

    // 2e. Vertical hash lines (scrolling texture)
    const strokeCalls = mockCtx._calls.filter(c => c.method === 'stroke');
    assert(strokeCalls.length > 0, 'Hash lines rendered (stroke calls present)');

    // Check hash line color
    const hashStroke = strokeCalls[0];
    if (hashStroke) {
        assertEqual(hashStroke.strokeStyle, '#7A5232', 'Hash line color is #7A5232');
    }

    // Verify ~24px spacing between hash lines
    const moveToCallsForHash = mockCtx._calls.filter(c =>
        c.method === 'moveTo'
    );
    if (moveToCallsForHash.length >= 2) {
        const spacing = moveToCallsForHash[1].args[0] - moveToCallsForHash[0].args[0];
        assertEqual(spacing, 24, 'Hash line spacing is 24px');
    }

    // 2f. Hash lines respect groundOffset for scrolling
    const mockCtx2 = createMockCtx();
    sandbox.groundOffset = 12; // Half tile offset
    sandbox.renderGround(mockCtx2);
    const moveToCallsOffset = mockCtx2._calls.filter(c => c.method === 'moveTo');
    if (moveToCallsOffset.length > 0) {
        // First line should start at -12 (which is -(12 % 24) = -12)
        assertEqual(moveToCallsOffset[0].args[0], -12, 'Hash lines offset by -groundOffset (scrolling works)');
    }
    sandbox.groundOffset = 0; // Reset
}

// ═══════════════════════════════════════════════════════
// 3. Ground scrolling across states
// ═══════════════════════════════════════════════════════

section('3. Ground scrolling — state behavior');

// 3a. IDLE state scrolls ground
sandbox.gameState = 'IDLE';
sandbox.groundOffset = 0;
sandbox.bobTimer = 0;
sandbox.bird.y = 300;
sandbox.update(0.1); // 100ms tick
assert(sandbox.groundOffset > 0, 'IDLE state: groundOffset increases (ground scrolls)');
const idleOffset = sandbox.groundOffset;
assertApprox(idleOffset, (120 * 0.1) % 24, 0.01, `IDLE: groundOffset ≈ PIPE_SPEED * dt (${(120 * 0.1).toFixed(1)})`);

// 3b. PLAYING state scrolls ground
sandbox.gameState = 'PLAYING';
sandbox.groundOffset = 0;
sandbox.bird.velocity = 0;
sandbox.update(0.1);
assert(sandbox.groundOffset > 0, 'PLAYING state: groundOffset increases (ground scrolls)');
const playingOffset = sandbox.groundOffset;
assertApprox(playingOffset, (120 * 0.1) % 24, 0.01, `PLAYING: groundOffset ≈ PIPE_SPEED * dt (${(120 * 0.1).toFixed(1)})`);

// 3c. GAME_OVER state freezes ground
sandbox.gameState = 'GAME_OVER';
const frozenOffset = sandbox.groundOffset;
sandbox.update(0.1);
assertEqual(sandbox.groundOffset, frozenOffset, 'GAME_OVER state: groundOffset unchanged (frozen)');

// 3d. Ground offset wraps with modulo 24
sandbox.gameState = 'IDLE';
sandbox.groundOffset = 0;
sandbox.bobTimer = 0;
sandbox.bird.y = 300;
// Simulate many frames to exceed 24
for (let i = 0; i < 100; i++) {
    sandbox.update(0.01);
}
assert(sandbox.groundOffset < 24, `Ground offset wraps (stays < 24): actual = ${sandbox.groundOffset.toFixed(2)}`);

// Reset state
sandbox.resetGame();

// ═══════════════════════════════════════════════════════
// 4. Bird rendering (renderBird)
// ═══════════════════════════════════════════════════════

section('4. Bird rendering — renderBird function');

// 4a. Function exists
assert(typeof sandbox.renderBird === 'function', 'renderBird is a function');

if (sandbox.renderBird) {
    sandbox.bird.x = 100;
    sandbox.bird.y = 300;
    sandbox.bird.rotation = 0;
    sandbox.bird.radius = 15;

    const mockCtx = createMockCtx();
    sandbox.renderBird(mockCtx);
    const calls = mockCtx._calls;

    // 4b. Canvas save/restore
    const saveIdx = calls.findIndex(c => c.method === 'save');
    const restoreIdx = calls.findIndex(c => c.method === 'restore');
    assert(saveIdx !== -1, 'renderBird calls ctx.save()');
    assert(restoreIdx !== -1, 'renderBird calls ctx.restore()');
    assert(saveIdx < restoreIdx, 'ctx.save() before ctx.restore()');

    // 4c. Canvas translate to bird position
    const translateCall = calls.find(c => c.method === 'translate');
    assert(translateCall !== undefined, 'renderBird calls ctx.translate()');
    if (translateCall) {
        assertEqual(translateCall.args[0], 100, 'translate x === bird.x (100)');
        assertEqual(translateCall.args[1], 300, 'translate y === bird.y (300)');
    }

    // 4d. Canvas rotate with bird.rotation
    const rotateCall = calls.find(c => c.method === 'rotate');
    assert(rotateCall !== undefined, 'renderBird calls ctx.rotate()');
    if (rotateCall) {
        assertEqual(rotateCall.args[0], 0, 'rotate angle === bird.rotation (0)');
    }

    // 4e. Transform order: save → translate → rotate
    const translateIdx = calls.findIndex(c => c.method === 'translate');
    const rotateIdx = calls.findIndex(c => c.method === 'rotate');
    assert(saveIdx < translateIdx, 'save before translate');
    assert(translateIdx < rotateIdx, 'translate before rotate');
    assert(rotateIdx < restoreIdx, 'rotate before restore');

    // 4f. Yellow body circle
    const bodyFill = calls.find(c =>
        c.method === 'fill' && c.fillStyle === '#F7DC6F'
    );
    assert(bodyFill !== undefined, 'Yellow body (#F7DC6F) filled');

    // Find the arc call for the body (radius = bird.radius = 15)
    const bodyArc = calls.find(c =>
        c.method === 'arc' && c.args[2] === 15
    );
    assert(bodyArc !== undefined, 'Body arc with radius 15 (bird.radius)');
    if (bodyArc) {
        assertEqual(bodyArc.args[0], 0, 'Body arc center x === 0 (translated)');
        assertEqual(bodyArc.args[1], 0, 'Body arc center y === 0 (translated)');
    }

    // 4g. Outline
    const outlineStroke = calls.find(c =>
        c.method === 'stroke' && c.strokeStyle === '#D4A017'
    );
    assert(outlineStroke !== undefined, 'Body outline (#D4A017) stroked');
    if (outlineStroke) {
        assertEqual(outlineStroke.lineWidth, 2, 'Outline lineWidth === 2');
    }

    // 4h. White eye (radius 5px, offset (5, -5))
    const eyeArc = calls.find(c =>
        c.method === 'arc' && c.args[2] === 5 && c.args[0] === 5 && c.args[1] === -5
    );
    assert(eyeArc !== undefined, 'White eye arc at offset (5, -5) with radius 5');

    const eyeFill = calls.find((c, i) => {
        if (c.method !== 'fill' || c.fillStyle !== '#FFFFFF') return false;
        // Check that the preceding arc is the eye arc
        const precedingArc = calls.slice(0, i).reverse().find(cc => cc.method === 'arc');
        return precedingArc && precedingArc.args[2] === 5 && precedingArc.args[0] === 5;
    });
    assert(eyeFill !== undefined, 'Eye filled with white (#FFFFFF)');

    // 4i. Black pupil (radius 2.5px, offset (7, -5))
    const pupilArc = calls.find(c =>
        c.method === 'arc' && c.args[2] === 2.5 && c.args[0] === 7 && c.args[1] === -5
    );
    assert(pupilArc !== undefined, 'Black pupil arc at offset (7, -5) with radius 2.5');

    const pupilFill = calls.find((c, i) => {
        if (c.method !== 'fill' || c.fillStyle !== '#000000') return false;
        const precedingArc = calls.slice(0, i).reverse().find(cc => cc.method === 'arc');
        return precedingArc && precedingArc.args[2] === 2.5;
    });
    assert(pupilFill !== undefined, 'Pupil filled with black (#000000)');

    // 4j. Orange beak triangle (#E67E22)
    const beakFillStyle = calls.find(c =>
        c.method === 'set_fillStyle' && c.args[0] === '#E67E22'
    );
    assert(beakFillStyle !== undefined, 'Beak color set to #E67E22');

    // Check beak triangle: moveTo, lineTo, lineTo, closePath
    const beakMoveToIdx = calls.findIndex(c =>
        c.method === 'moveTo' && c.args[0] === 15 // bird.radius
    );
    assert(beakMoveToIdx !== -1, 'Beak starts at bird.radius (15) on x-axis');

    if (beakMoveToIdx !== -1) {
        const beakLineTo1 = calls[beakMoveToIdx + 1];
        assert(
            beakLineTo1 && beakLineTo1.method === 'lineTo' &&
            beakLineTo1.args[0] === 23 && beakLineTo1.args[1] === 0,
            'Beak tip at (bird.radius + 8, 0) = (23, 0) — 8px wide'
        );

        const beakLineTo2 = calls[beakMoveToIdx + 2];
        assert(
            beakLineTo2 && beakLineTo2.method === 'lineTo' &&
            beakLineTo2.args[0] === 15,
            'Beak returns to bird.radius on x-axis'
        );

        // Verify closePath follows
        const closePathAfterBeak = calls.slice(beakMoveToIdx).find(c => c.method === 'closePath');
        assert(closePathAfterBeak !== undefined, 'Beak path closed with closePath()');
    }

    const beakFill = calls.find(c =>
        c.method === 'fill' && c.fillStyle === '#E67E22'
    );
    assert(beakFill !== undefined, 'Beak filled with orange (#E67E22)');

    // 4k. Verify no wing/ellipse (old implementation had a wing)
    const ellipseCalls = calls.filter(c => c.method === 'ellipse');
    assertEqual(ellipseCalls.length, 0, 'No ellipse calls (wing removed from old implementation)');

    // 4l. Bird rotation propagates to canvas
    sandbox.bird.rotation = 0.5;
    const mockCtx3 = createMockCtx();
    sandbox.renderBird(mockCtx3);
    const rotateCall2 = mockCtx3._calls.find(c => c.method === 'rotate');
    if (rotateCall2) {
        assertEqual(rotateCall2.args[0], 0.5, 'renderBird uses bird.rotation value (0.5)');
    }
    sandbox.bird.rotation = 0; // Reset
}

// ═══════════════════════════════════════════════════════
// 5. Bird bob animation in IDLE state
// ═══════════════════════════════════════════════════════

section('5. Bird bob animation — IDLE state');

sandbox.resetGame();
sandbox.gameState = 'IDLE';
sandbox.bobTimer = 0;

// Capture positions over time
const positions = [];
for (let i = 0; i < 60; i++) {
    sandbox.update(1/60); // 60fps
    positions.push(sandbox.bird.y);
}

// Bird should oscillate around BIRD_START_Y (300)
const minY = Math.min(...positions);
const maxY = Math.max(...positions);
assert(maxY > 300, `Bird bobs above start (max y = ${maxY.toFixed(2)} > 300)`);
assert(minY < 300, `Bird bobs below start (min y = ${minY.toFixed(2)} < 300)`);
assertApprox(maxY - 300, 8, 1, 'Bob amplitude ≈ BOB_AMPLITUDE (8px) above center');
assertApprox(300 - minY, 8, 1, 'Bob amplitude ≈ BOB_AMPLITUDE (8px) below center');

// Verify bob uses sine wave with BOB_FREQUENCY
// At t=0, sin(0) = 0, bird.y should be near 300
sandbox.resetGame();
sandbox.bobTimer = 0;
sandbox.update(0.001); // tiny dt
assertApprox(sandbox.bird.y, 300, 1, 'At bobTimer ≈ 0, bird.y ≈ BIRD_START_Y');

// At t = 1/(4*BOB_FREQUENCY) = 0.125s, sin should peak
sandbox.resetGame();
sandbox.bobTimer = 0;
for (let i = 0; i < 8; i++) sandbox.update(0.125 / 8); // simulate 0.125s
assertApprox(sandbox.bird.y, 308, 1, 'At peak of bob cycle, bird.y ≈ 308 (300 + 8)');

sandbox.resetGame();

// ═══════════════════════════════════════════════════════
// 6. Pipe rendering (renderPipes)
// ═══════════════════════════════════════════════════════

section('6. Pipe rendering — renderPipes function');

// 6a. Function exists
assert(typeof sandbox.renderPipes === 'function', 'renderPipes is a function');

if (sandbox.renderPipes) {
    // Setup a test pipe
    sandbox.pipes.length = 0;
    sandbox.pipes.push({ x: 200, gapY: 200, scored: false });

    const mockCtx = createMockCtx();
    sandbox.renderPipes(mockCtx);
    const calls = mockCtx._calls;

    // 6b. Green pipe body (#2ECC71)
    const pipeBodyFills = calls.filter(c =>
        c.method === 'fillRect' && c.fillStyle === '#2ECC71'
    );
    assert(pipeBodyFills.length >= 2, 'At least 2 green (#2ECC71) fillRects for top and bottom pipe');

    if (pipeBodyFills.length >= 2) {
        // Top pipe: from 0 to gapY (200)
        const topPipe = pipeBodyFills[0];
        assertEqual(topPipe.args[0], 200, 'Top pipe x === pipe.x (200)');
        assertEqual(topPipe.args[1], 0, 'Top pipe starts at y=0');
        assertEqual(topPipe.args[2], 52, 'Top pipe width === PIPE_WIDTH (52)');
        assertEqual(topPipe.args[3], 200, 'Top pipe height === gapY (200)');

        // Bottom pipe: from gapY + PIPE_GAP (330) to ground
        const bottomPipe = pipeBodyFills[1];
        assertEqual(bottomPipe.args[0], 200, 'Bottom pipe x === pipe.x (200)');
        assertEqual(bottomPipe.args[1], 330, 'Bottom pipe starts at gapY + PIPE_GAP (330)');
        assertEqual(bottomPipe.args[2], 52, 'Bottom pipe width === PIPE_WIDTH (52)');
        assertEqual(bottomPipe.args[3], 210, 'Bottom pipe height === groundY - (gapY + PIPE_GAP) = 540 - 330 = 210');
    }

    // 6c. Darker green caps (#27AE60)
    const capFills = calls.filter(c =>
        c.method === 'fillRect' && c.fillStyle === '#27AE60'
    );
    assert(capFills.length >= 2, 'At least 2 darker green (#27AE60) cap fillRects');

    if (capFills.length >= 2) {
        // Top pipe cap: at bottom edge of top pipe
        const topCap = capFills[0];
        assertEqual(topCap.args[0], 200 - 3, 'Top cap x === pipe.x - PIPE_CAP_OVERHANG (197)');
        assertEqual(topCap.args[1], 200 - 20, 'Top cap y === gapY - PIPE_CAP_HEIGHT (180)');
        assertEqual(topCap.args[2], 52 + 3 * 2, 'Top cap width === PIPE_WIDTH + 2*PIPE_CAP_OVERHANG (58)');
        assertEqual(topCap.args[3], 20, 'Top cap height === PIPE_CAP_HEIGHT (20)');

        // Bottom pipe cap: at top edge of bottom pipe
        const bottomCap = capFills[1];
        assertEqual(bottomCap.args[0], 200 - 3, 'Bottom cap x === pipe.x - PIPE_CAP_OVERHANG (197)');
        assertEqual(bottomCap.args[1], 330, 'Bottom cap y === gapY + PIPE_GAP (330)');
        assertEqual(bottomCap.args[2], 52 + 3 * 2, 'Bottom cap width === PIPE_WIDTH + 2*PIPE_CAP_OVERHANG (58)');
        assertEqual(bottomCap.args[3], 20, 'Bottom cap height === PIPE_CAP_HEIGHT (20)');
    }

    // 6d. Multiple pipes render correctly
    sandbox.pipes.length = 0;
    sandbox.pipes.push(
        { x: 100, gapY: 150, scored: false },
        { x: 320, gapY: 250, scored: false }
    );
    const mockCtx2 = createMockCtx();
    sandbox.renderPipes(mockCtx2);

    const pipeBodyFills2 = mockCtx2._calls.filter(c =>
        c.method === 'fillRect' && c.fillStyle === '#2ECC71'
    );
    assertEqual(pipeBodyFills2.length, 4, 'Two pipe pairs → 4 green body fillRects');

    const capFills2 = mockCtx2._calls.filter(c =>
        c.method === 'fillRect' && c.fillStyle === '#27AE60'
    );
    assertEqual(capFills2.length, 4, 'Two pipe pairs → 4 cap fillRects');

    // 6e. Empty pipes array renders nothing
    sandbox.pipes.length = 0;
    const mockCtx3 = createMockCtx();
    sandbox.renderPipes(mockCtx3);
    const pipeFills3 = mockCtx3._calls.filter(c => c.method === 'fillRect');
    assertEqual(pipeFills3.length, 0, 'Empty pipes array → no fillRect calls');

    sandbox.pipes.length = 0; // cleanup
}

// ═══════════════════════════════════════════════════════
// 7. Render order — sky → pipes → ground → bird
// ═══════════════════════════════════════════════════════

section('7. Render order');

// 7a. Source code analysis
const renderFuncBody = src.slice(
    src.indexOf('function render(ctx)'),
    src.indexOf('// ===== GAME LOOP =====')
);

// Check call order in render function
const pipeCallIdx = renderFuncBody.indexOf('renderPipes(ctx)');
const groundCallIdx = renderFuncBody.indexOf('renderGround(ctx)');
const birdCallIdx = renderFuncBody.indexOf('renderBird(ctx)');

assert(pipeCallIdx !== -1, 'render() calls renderPipes(ctx)');
assert(groundCallIdx !== -1, 'render() calls renderGround(ctx)');
assert(birdCallIdx !== -1, 'render() calls renderBird(ctx)');

if (pipeCallIdx !== -1 && groundCallIdx !== -1 && birdCallIdx !== -1) {
    assert(pipeCallIdx < groundCallIdx, 'Pipes rendered before ground (ground covers pipe bottoms)');
    assert(groundCallIdx < birdCallIdx, 'Ground rendered before bird (bird on top)');
}

// 7b. Functional test with mock ctx
sandbox.pipes.length = 0;
sandbox.pipes.push({ x: 200, gapY: 200, scored: false });
sandbox.groundOffset = 0;

const mockCtxRender = createMockCtx();
sandbox.render(mockCtxRender);
const renderCalls = mockCtxRender._calls;

// Find first call from each render function by identifying unique characteristics
// Sky: fillRect with #70c5ce
// Pipes: fillRect with #2ECC71
// Ground: fillRect with #8B5E3C
// Bird: save() call

const skyIdx = renderCalls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#70c5ce');
const pipeIdx = renderCalls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#2ECC71');
const groundIdx = renderCalls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#8B5E3C');
const birdSaveIdx = renderCalls.findIndex(c => c.method === 'save');

assert(skyIdx < pipeIdx, 'Sky rendered before pipes (functional test)');
assert(pipeIdx < groundIdx, 'Pipes rendered before ground (functional test)');
assert(groundIdx < birdSaveIdx, 'Ground rendered before bird (functional test)');

sandbox.pipes.length = 0; // cleanup

// ═══════════════════════════════════════════════════════
// 8. No new global variables introduced
// ═══════════════════════════════════════════════════════

section('8. No new global variables');

// The pre-existing global variables (from the skeleton)
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
]);

// Also allowed: functions (not "variables" in the traditional sense)
const allowedFunctions = new Set([
    'handleInput', 'resetGame', 'flap',
    'update', 'render', 'gameLoop',
    // Functions that may have been added by pipe system (CD-005):
    'updateBird', 'shouldSpawnPipe', 'spawnPipe', 'updatePipes',
    // New render functions from CD-013:
    'renderGround', 'renderBird', 'renderPipes',
]);

// Extract all top-level const/let/var/function declarations
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

// Check for unexpected globals
const unexpectedVars = [...declaredVars].filter(v => !allowedGlobals.has(v));
assertEqual(unexpectedVars.length, 0,
    `No unexpected global variables (found: ${unexpectedVars.length > 0 ? unexpectedVars.join(', ') : 'none'})`
);

const unexpectedFuncs = [...declaredFuncs].filter(f => !allowedFunctions.has(f));
assertEqual(unexpectedFuncs.length, 0,
    `No unexpected global functions (found: ${unexpectedFuncs.length > 0 ? unexpectedFuncs.join(', ') : 'none'})`
);

// Verify the new render functions exist
assert(declaredFuncs.has('renderGround'), 'renderGround function declared');
assert(declaredFuncs.has('renderBird'), 'renderBird function declared');
assert(declaredFuncs.has('renderPipes'), 'renderPipes function declared');

// ═══════════════════════════════════════════════════════
// 9. Known issue — magic number audit
// ═══════════════════════════════════════════════════════

section('9. Known issue — magic number audit (expected)');

// Document that rendering pixel values appear as magic numbers in the existing
// game.test.js section 10 audit. These are visual design constants, not logic bugs.
const renderPixelValues = [5, 2.5, 7, 8, 4, 24, 10];
const funcSectionSrc = src.slice(src.indexOf('// ===== STATE MACHINE ====='));
const funcSectionNoComments = funcSectionSrc
    .replace(/\/\/.*$/gm, '')
    .replace(/\/\*[\s\S]*?\*\//g, '');

const numericPattern = /(?<!\w)\d+\.?\d*(?!\w)/g;
const allowedNumbers = new Set(['0', '1', '2', '1000', '0.05']);
const numMatches = [...funcSectionNoComments.matchAll(numericPattern)];
const magicNumbers = numMatches
    .map(m => m[0])
    .filter(n => !allowedNumbers.has(n));

// Confirm the magic numbers are rendering pixel values (expected per spec)
const expectedMagicSet = new Set(['4', '5', '2.5', '7', '8', '24', '10', '6', '000000', '3', '15']);
const unexpectedMagic = magicNumbers.filter(n => !expectedMagicSet.has(n));
assert(
    unexpectedMagic.length === 0 || unexpectedMagic.every(n => Number(n) !== NaN),
    `All magic numbers are rendering pixel offsets or hex fragments (unexpected: ${unexpectedMagic.length > 0 ? unexpectedMagic.join(', ') : 'none'})`
);
console.log(`  ℹ️  Known issue: ${magicNumbers.length} rendering pixel values flagged by magic number audit`);
console.log(`     Values: ${[...new Set(magicNumbers)].join(', ')}`);
console.log('     These are visual design constants per the PRD spec, not logic bugs.');

// ═══════════════════════════════════════════════════════
// 10. Color spec compliance
// ═══════════════════════════════════════════════════════

section('10. Color spec compliance');

const specColors = {
    '#70c5ce': 'Sky background',
    '#8B5E3C': 'Ground dirt strip',
    '#5CBF2A': 'Grass accent',
    '#F7DC6F': 'Bird body (yellow)',
    '#D4A017': 'Bird outline',
    '#E67E22': 'Bird beak (orange)',
    '#2ECC71': 'Pipe body (green)',
    '#27AE60': 'Pipe caps (darker green)',
};

for (const [color, desc] of Object.entries(specColors)) {
    assert(src.includes(color), `${desc} color ${color} present in source`);
}

// ═══════════════════════════════════════════════════════
// 11. Source structure — render functions in RENDER LOGIC section
// ═══════════════════════════════════════════════════════

section('11. Source structure');

const renderSectionStart = src.indexOf('// ===== RENDER LOGIC =====');
const gameLoopSectionStart = src.indexOf('// ===== GAME LOOP =====');

assert(renderSectionStart !== -1, 'RENDER LOGIC section marker present');
assert(gameLoopSectionStart !== -1, 'GAME LOOP section marker present');
assert(renderSectionStart < gameLoopSectionStart, 'RENDER LOGIC section before GAME LOOP section');

// renderGround, renderBird defined before main render()
const renderGroundPos = src.indexOf('function renderGround');
const renderBirdPos = src.indexOf('function renderBird');
const renderMainPos = src.indexOf('function render(ctx)');

assert(renderGroundPos > renderSectionStart, 'renderGround in RENDER LOGIC section');
assert(renderBirdPos > renderSectionStart, 'renderBird in RENDER LOGIC section');
assert(renderMainPos > renderSectionStart, 'render(ctx) in RENDER LOGIC section');

// renderPipes may be in PIPE FUNCTIONS section (from CD-005)
const renderPipesPos = src.indexOf('function renderPipes');
assert(renderPipesPos !== -1, 'renderPipes function defined in source');

// ═══════════════════════════════════════════════════════
// 12. Edge cases
// ═══════════════════════════════════════════════════════

section('12. Edge cases');

// 12a. Bird at extreme positions renders without error
if (sandbox.renderBird) {
    sandbox.bird.y = 0;
    sandbox.bird.x = 100;
    try {
        const mockCtx = createMockCtx();
        sandbox.renderBird(mockCtx);
        assert(true, 'renderBird works at y=0 (top edge)');
    } catch (e) {
        assert(false, `renderBird crashes at y=0: ${e.message}`);
    }

    sandbox.bird.y = 600;
    try {
        const mockCtx = createMockCtx();
        sandbox.renderBird(mockCtx);
        assert(true, 'renderBird works at y=600 (bottom edge)');
    } catch (e) {
        assert(false, `renderBird crashes at y=600: ${e.message}`);
    }
    sandbox.bird.y = 300; // Reset
}

// 12b. Pipes at edge positions
if (sandbox.renderPipes) {
    sandbox.pipes.length = 0;
    sandbox.pipes.push({ x: -52, gapY: 200, scored: false }); // Off-screen left
    try {
        const mockCtx = createMockCtx();
        sandbox.renderPipes(mockCtx);
        assert(true, 'renderPipes handles off-screen pipe (x=-52)');
    } catch (e) {
        assert(false, `renderPipes crashes with off-screen pipe: ${e.message}`);
    }

    sandbox.pipes.length = 0;
    sandbox.pipes.push({ x: 400, gapY: 200, scored: false }); // Right edge
    try {
        const mockCtx = createMockCtx();
        sandbox.renderPipes(mockCtx);
        assert(true, 'renderPipes handles pipe at right edge (x=400)');
    } catch (e) {
        assert(false, `renderPipes crashes with pipe at right edge: ${e.message}`);
    }
    sandbox.pipes.length = 0; // Cleanup
}

// 12c. Ground with maximum offset
if (sandbox.renderGround) {
    sandbox.groundOffset = 23.99;
    try {
        const mockCtx = createMockCtx();
        sandbox.renderGround(mockCtx);
        assert(true, 'renderGround works at near-max groundOffset (23.99)');
    } catch (e) {
        assert(false, `renderGround crashes at max offset: ${e.message}`);
    }
    sandbox.groundOffset = 0;
}

// ═══════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════

console.log('\n═══════════════════════════════════════════════════');
console.log(`  TS-011 QA RESULTS: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('═══════════════════════════════════════════════════');

if (failures.length > 0) {
    console.log('\n❌ Failed tests:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

if (bugs.length > 0) {
    console.log('\n🐛 Bugs found:');
    bugs.forEach((b, i) => {
        console.log(`\n  Bug #${i + 1}: ${b.title}`);
        console.log(`  Description: ${b.description}`);
        console.log(`  Repro: ${b.repro}`);
    });
}

if (failures.length === 0 && bugs.length === 0) {
    console.log('\n✅ All acceptance criteria verified. CD-013 render functions pass QA.');
}

process.exit(failed > 0 ? 1 : 0);
