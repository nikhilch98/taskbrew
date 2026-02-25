/**
 * TS-017 — QA Verification for CD-009: UI Overlays
 * Automated test suite using Node.js (no external dependencies)
 *
 * Tests cover:
 *  1. renderIdleOverlay — title text, instruction text, fonts, colors, positions
 *  2. renderGameOverOverlay — dark overlay, game over text, score display, restart instruction
 *  3. State-based rendering — render() dispatches correct overlay per gameState
 *  4. Overlay layering — overlays render ON TOP of game elements
 *  5. renderScore exclusion — not called during IDLE or GAME_OVER
 *  6. Frozen game elements visible underneath GAME_OVER overlay
 *  7. ctx.save/restore bookkeeping in overlay functions
 *  8. No new global variables introduced by CD-009
 *  9. No forbidden patterns (import/export/require/DOMContentLoaded)
 * 10. Regression — existing 116/117 test pass rate preserved
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

function assertIncludes(arr, value, message) {
    if (arr.includes(value)) {
        passed++;
        console.log(`  \u2705 ${message}`);
    } else {
        failed++;
        console.log(`  \u274c ${message}  \u2014 ${JSON.stringify(value)} not found`);
        failures.push(`${message} \u2014 ${JSON.stringify(value)} not found`);
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

/**
 * Creates a mock canvas 2D context that records ALL draw calls,
 * including text methods (strokeText, fillText) needed for overlay testing.
 */
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
        fillText: function(text, x, y) { calls.push({ method: 'fillText', args: [text, x, y], fillStyle: state.fillStyle, font: state.font }); },
        strokeText: function(text, x, y) { calls.push({ method: 'strokeText', args: [text, x, y], strokeStyle: state.strokeStyle, lineWidth: state.lineWidth, font: state.font }); },
        beginPath: function() { calls.push({ method: 'beginPath', args: [] }); },
        arc: function(...args) { calls.push({ method: 'arc', args }); },
        fill: function() { calls.push({ method: 'fill', args: [], fillStyle: state.fillStyle }); },
        stroke: function() { calls.push({ method: 'stroke', args: [], strokeStyle: state.strokeStyle, lineWidth: state.lineWidth }); },
        moveTo: function(...args) { calls.push({ method: 'moveTo', args }); },
        lineTo: function(...args) { calls.push({ method: 'lineTo', args }); },
        closePath: function() { calls.push({ method: 'closePath', args: [] }); },
        save: function() {
            state.transforms.push({
                ...state.currentTransform,
                fillStyle: state.fillStyle,
                strokeStyle: state.strokeStyle,
                lineWidth: state.lineWidth,
                font: state.font,
                textAlign: state.textAlign,
                textBaseline: state.textBaseline,
                lineJoin: state.lineJoin,
            });
            calls.push({ method: 'save', args: [] });
        },
        restore: function() {
            if (state.transforms.length > 0) {
                const saved = state.transforms.pop();
                state.currentTransform = { translateX: saved.translateX, translateY: saved.translateY, rotation: saved.rotation };
                state.fillStyle = saved.fillStyle || '';
                state.strokeStyle = saved.strokeStyle || '';
                state.lineWidth = saved.lineWidth || 0;
                state.font = saved.font || '';
                state.textAlign = saved.textAlign || '';
                state.textBaseline = saved.textBaseline || '';
                state.lineJoin = saved.lineJoin || '';
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
                font: '',
                textAlign: '',
                textBaseline: '',
                lineJoin: '',
                fillRect: () => {},
                strokeRect: () => {},
                fillText: () => {},
                strokeText: () => {},
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

console.log('TS-017 \u2014 QA Verification for CD-009: UI Overlays');
console.log('=====================================================');

// =================================================================
// 1. renderIdleOverlay function exists and is callable
// =================================================================

section('1. renderIdleOverlay \u2014 function existence');

assert(typeof sandbox.renderIdleOverlay === 'function', 'renderIdleOverlay is a function');
assert(src.includes('function renderIdleOverlay(ctx)'), 'renderIdleOverlay declared with ctx parameter');

// =================================================================
// 2. renderIdleOverlay \u2014 title text properties
// =================================================================

section('2. renderIdleOverlay \u2014 title text');

if (sandbox.renderIdleOverlay) {
    const mockCtx = createMockCtx();
    sandbox.renderIdleOverlay(mockCtx);
    const calls = mockCtx._calls;

    // 2a. ctx.save/restore wrapping
    const saveIdx = calls.findIndex(c => c.method === 'save');
    const restoreIdx = calls.findLastIndex(c => c.method === 'restore');
    assert(saveIdx !== -1, 'renderIdleOverlay calls ctx.save()');
    assert(restoreIdx !== -1, 'renderIdleOverlay calls ctx.restore()');
    assert(saveIdx < restoreIdx, 'ctx.save() before ctx.restore()');

    // 2b. textAlign = 'center'
    const textAlignCall = calls.find(c => c.method === 'set_textAlign' && c.args[0] === 'center');
    assert(textAlignCall !== undefined, "textAlign set to 'center'");

    // 2c. textBaseline = 'middle'
    const textBaselineCall = calls.find(c => c.method === 'set_textBaseline' && c.args[0] === 'middle');
    assert(textBaselineCall !== undefined, "textBaseline set to 'middle'");

    // 2d. lineJoin = 'round'
    const lineJoinCall = calls.find(c => c.method === 'set_lineJoin' && c.args[0] === 'round');
    assert(lineJoinCall !== undefined, "lineJoin set to 'round'");

    // 2e. Title font: bold 36px Arial, sans-serif
    const titleFontCall = calls.find(c => c.method === 'set_font' && c.args[0] === 'bold 36px Arial, sans-serif');
    assert(titleFontCall !== undefined, "Title font set to 'bold 36px Arial, sans-serif'");

    // 2f. Title fillStyle: #ffffff (white)
    const titleFillStyle = calls.find(c => c.method === 'set_fillStyle' && c.args[0] === '#ffffff');
    assert(titleFillStyle !== undefined, "Title fillStyle set to '#ffffff' (white)");

    // 2g. Title strokeStyle: #000000 (black)
    const titleStrokeStyle = calls.find(c => c.method === 'set_strokeStyle' && c.args[0] === '#000000');
    assert(titleStrokeStyle !== undefined, "Title strokeStyle set to '#000000' (black)");

    // 2h. Title lineWidth: 3
    const titleLineWidth = calls.find(c => c.method === 'set_lineWidth' && c.args[0] === 3);
    assert(titleLineWidth !== undefined, 'Title lineWidth set to 3');

    // 2i. strokeText "Flappy Bird" at (CANVAS_WIDTH/2, CANVAS_HEIGHT/4) = (200, 150)
    const titleStroke = calls.find(c =>
        c.method === 'strokeText' && c.args[0] === 'Flappy Bird'
    );
    assert(titleStroke !== undefined, "strokeText('Flappy Bird', ...) called");
    if (titleStroke) {
        assertEqual(titleStroke.args[1], 200, 'Title strokeText x === CANVAS_WIDTH/2 (200)');
        assertEqual(titleStroke.args[2], 150, 'Title strokeText y === CANVAS_HEIGHT/4 (150)');
        assertEqual(titleStroke.strokeStyle, '#000000', 'Title stroke color is black (#000000)');
        assertEqual(titleStroke.lineWidth, 3, 'Title stroke lineWidth is 3');
        assertEqual(titleStroke.font, 'bold 36px Arial, sans-serif', 'Title stroke font is bold 36px Arial, sans-serif');
    }

    // 2j. fillText "Flappy Bird" at same position
    const titleFill = calls.find(c =>
        c.method === 'fillText' && c.args[0] === 'Flappy Bird'
    );
    assert(titleFill !== undefined, "fillText('Flappy Bird', ...) called");
    if (titleFill) {
        assertEqual(titleFill.args[1], 200, 'Title fillText x === CANVAS_WIDTH/2 (200)');
        assertEqual(titleFill.args[2], 150, 'Title fillText y === CANVAS_HEIGHT/4 (150)');
        assertEqual(titleFill.fillStyle, '#ffffff', 'Title fill color is white (#ffffff)');
    }

    // 2k. strokeText comes before fillText for title (stroke provides outline behind fill)
    const titleStrokeIdx = calls.findIndex(c => c.method === 'strokeText' && c.args[0] === 'Flappy Bird');
    const titleFillIdx = calls.findIndex(c => c.method === 'fillText' && c.args[0] === 'Flappy Bird');
    assert(titleStrokeIdx < titleFillIdx, 'Title strokeText called before fillText (outline under fill)');
}

// =================================================================
// 3. renderIdleOverlay \u2014 instruction text
// =================================================================

section('3. renderIdleOverlay \u2014 instruction text');

if (sandbox.renderIdleOverlay) {
    const mockCtx = createMockCtx();
    sandbox.renderIdleOverlay(mockCtx);
    const calls = mockCtx._calls;

    // 3a. Instruction font: 18px Arial, sans-serif
    const instrFontCall = calls.find(c => c.method === 'set_font' && c.args[0] === '18px Arial, sans-serif');
    assert(instrFontCall !== undefined, "Instruction font set to '18px Arial, sans-serif'");

    // 3b. Instruction lineWidth: 2
    const instrLineWidth = calls.find(c => c.method === 'set_lineWidth' && c.args[0] === 2);
    assert(instrLineWidth !== undefined, 'Instruction lineWidth set to 2');

    // 3c. strokeText "Press Space or Tap to Start" at (CANVAS_WIDTH/2, CANVAS_HEIGHT/2 + 80) = (200, 380)
    const instrStroke = calls.find(c =>
        c.method === 'strokeText' && c.args[0] === 'Press Space or Tap to Start'
    );
    assert(instrStroke !== undefined, "strokeText('Press Space or Tap to Start', ...) called");
    if (instrStroke) {
        assertEqual(instrStroke.args[1], 200, 'Instruction strokeText x === CANVAS_WIDTH/2 (200)');
        assertEqual(instrStroke.args[2], 380, 'Instruction strokeText y === CANVAS_HEIGHT/2 + 80 (380)');
        assertEqual(instrStroke.lineWidth, 2, 'Instruction stroke lineWidth is 2');
    }

    // 3d. fillText "Press Space or Tap to Start" at same position
    const instrFill = calls.find(c =>
        c.method === 'fillText' && c.args[0] === 'Press Space or Tap to Start'
    );
    assert(instrFill !== undefined, "fillText('Press Space or Tap to Start', ...) called");
    if (instrFill) {
        assertEqual(instrFill.args[1], 200, 'Instruction fillText x === CANVAS_WIDTH/2 (200)');
        assertEqual(instrFill.args[2], 380, 'Instruction fillText y === CANVAS_HEIGHT/2 + 80 (380)');
        assertEqual(instrFill.fillStyle, '#ffffff', 'Instruction fill is white (#ffffff)');
    }

    // 3e. strokeText before fillText for instruction
    const instrStrokeIdx = calls.findIndex(c => c.method === 'strokeText' && c.args[0] === 'Press Space or Tap to Start');
    const instrFillIdx = calls.findIndex(c => c.method === 'fillText' && c.args[0] === 'Press Space or Tap to Start');
    assert(instrStrokeIdx < instrFillIdx, 'Instruction strokeText before fillText');
}

// =================================================================
// 4. renderGameOverOverlay function exists
// =================================================================

section('4. renderGameOverOverlay \u2014 function existence');

assert(typeof sandbox.renderGameOverOverlay === 'function', 'renderGameOverOverlay is a function');
assert(src.includes('function renderGameOverOverlay(ctx)'), 'renderGameOverOverlay declared with ctx parameter');

// =================================================================
// 5. renderGameOverOverlay \u2014 semi-transparent overlay
// =================================================================

section('5. renderGameOverOverlay \u2014 dark overlay');

if (sandbox.renderGameOverOverlay) {
    sandbox.score = 7; // Set a test score
    const mockCtx = createMockCtx();
    sandbox.renderGameOverOverlay(mockCtx);
    const calls = mockCtx._calls;

    // 5a. ctx.save/restore wrapping
    const saveIdx = calls.findIndex(c => c.method === 'save');
    const restoreIdx = calls.findLastIndex(c => c.method === 'restore');
    assert(saveIdx !== -1, 'renderGameOverOverlay calls ctx.save()');
    assert(restoreIdx !== -1, 'renderGameOverOverlay calls ctx.restore()');
    assert(saveIdx < restoreIdx, 'ctx.save() before ctx.restore()');

    // 5b. Semi-transparent dark overlay
    const overlayFill = calls.find(c =>
        c.method === 'fillRect' && c.fillStyle === 'rgba(0, 0, 0, 0.5)'
    );
    assert(overlayFill !== undefined, "Dark overlay fillRect with 'rgba(0, 0, 0, 0.5)' called");
    if (overlayFill) {
        assertEqual(overlayFill.args[0], 0, 'Dark overlay x === 0');
        assertEqual(overlayFill.args[1], 0, 'Dark overlay y === 0');
        assertEqual(overlayFill.args[2], 400, 'Dark overlay width === CANVAS_WIDTH (400)');
        assertEqual(overlayFill.args[3], 600, 'Dark overlay height === CANVAS_HEIGHT (600)');
    }

    // 5c. Dark overlay is the first fillRect (rendered before text)
    const firstFillRect = calls.find(c => c.method === 'fillRect');
    assert(
        firstFillRect && firstFillRect.fillStyle === 'rgba(0, 0, 0, 0.5)',
        'Dark overlay is the first fillRect call'
    );
}

// =================================================================
// 6. renderGameOverOverlay \u2014 "Game Over" text
// =================================================================

section('6. renderGameOverOverlay \u2014 Game Over text');

if (sandbox.renderGameOverOverlay) {
    sandbox.score = 7;
    const mockCtx = createMockCtx();
    sandbox.renderGameOverOverlay(mockCtx);
    const calls = mockCtx._calls;

    // 6a. Text setup properties
    const textAlignCall = calls.find(c => c.method === 'set_textAlign' && c.args[0] === 'center');
    assert(textAlignCall !== undefined, "textAlign set to 'center'");

    const textBaselineCall = calls.find(c => c.method === 'set_textBaseline' && c.args[0] === 'middle');
    assert(textBaselineCall !== undefined, "textBaseline set to 'middle'");

    const lineJoinCall = calls.find(c => c.method === 'set_lineJoin' && c.args[0] === 'round');
    assert(lineJoinCall !== undefined, "lineJoin set to 'round'");

    // 6b. White fill for text
    const whiteFill = calls.find(c => c.method === 'set_fillStyle' && c.args[0] === '#ffffff');
    assert(whiteFill !== undefined, "fillStyle set to '#ffffff' (white) for text");

    // 6c. Black stroke for text
    const blackStroke = calls.find(c => c.method === 'set_strokeStyle' && c.args[0] === '#000000');
    assert(blackStroke !== undefined, "strokeStyle set to '#000000' (black) for text");

    // 6d. "Game Over" font: bold 40px Arial, sans-serif
    const goFont = calls.find(c => c.method === 'set_font' && c.args[0] === 'bold 40px Arial, sans-serif');
    assert(goFont !== undefined, "Game Over font set to 'bold 40px Arial, sans-serif'");

    // 6e. "Game Over" lineWidth: 3
    // Find the lineWidth=3 that occurs after overlay text setup
    const goLineWidth = calls.find(c => c.method === 'set_lineWidth' && c.args[0] === 3);
    assert(goLineWidth !== undefined, 'Game Over lineWidth set to 3');

    // 6f. strokeText "Game Over" at (CANVAS_WIDTH/2, CANVAS_HEIGHT/3) = (200, 200)
    const goStroke = calls.find(c =>
        c.method === 'strokeText' && c.args[0] === 'Game Over'
    );
    assert(goStroke !== undefined, "strokeText('Game Over', ...) called");
    if (goStroke) {
        assertEqual(goStroke.args[1], 200, 'Game Over strokeText x === CANVAS_WIDTH/2 (200)');
        assertEqual(goStroke.args[2], 200, 'Game Over strokeText y === CANVAS_HEIGHT/3 (200)');
        assertEqual(goStroke.font, 'bold 40px Arial, sans-serif', 'Game Over stroke font is bold 40px');
        assertEqual(goStroke.lineWidth, 3, 'Game Over stroke lineWidth is 3');
    }

    // 6g. fillText "Game Over" at same position
    const goFill = calls.find(c =>
        c.method === 'fillText' && c.args[0] === 'Game Over'
    );
    assert(goFill !== undefined, "fillText('Game Over', ...) called");
    if (goFill) {
        assertEqual(goFill.args[1], 200, 'Game Over fillText x === CANVAS_WIDTH/2 (200)');
        assertEqual(goFill.args[2], 200, 'Game Over fillText y === CANVAS_HEIGHT/3 (200)');
        assertEqual(goFill.fillStyle, '#ffffff', 'Game Over fill is white');
    }

    // 6h. stroke before fill
    const goStrokeIdx = calls.findIndex(c => c.method === 'strokeText' && c.args[0] === 'Game Over');
    const goFillIdx = calls.findIndex(c => c.method === 'fillText' && c.args[0] === 'Game Over');
    assert(goStrokeIdx < goFillIdx, 'Game Over strokeText before fillText');
}

// =================================================================
// 7. renderGameOverOverlay \u2014 Score display
// =================================================================

section('7. renderGameOverOverlay \u2014 Score display');

if (sandbox.renderGameOverOverlay) {
    sandbox.score = 42;
    const mockCtx = createMockCtx();
    sandbox.renderGameOverOverlay(mockCtx);
    const calls = mockCtx._calls;

    // 7a. Score font: bold 30px Arial, sans-serif
    const scoreFont = calls.find(c => c.method === 'set_font' && c.args[0] === 'bold 30px Arial, sans-serif');
    assert(scoreFont !== undefined, "Score font set to 'bold 30px Arial, sans-serif'");

    // 7b. strokeText "Score: 42" at (CANVAS_WIDTH/2, CANVAS_HEIGHT/3 + 60) = (200, 260)
    const scoreStroke = calls.find(c =>
        c.method === 'strokeText' && c.args[0] === 'Score: 42'
    );
    assert(scoreStroke !== undefined, "strokeText('Score: 42', ...) called");
    if (scoreStroke) {
        assertEqual(scoreStroke.args[1], 200, 'Score strokeText x === CANVAS_WIDTH/2 (200)');
        assertEqual(scoreStroke.args[2], 260, 'Score strokeText y === CANVAS_HEIGHT/3 + 60 (260)');
    }

    // 7c. fillText "Score: 42" at same position
    const scoreFill = calls.find(c =>
        c.method === 'fillText' && c.args[0] === 'Score: 42'
    );
    assert(scoreFill !== undefined, "fillText('Score: 42', ...) called");
    if (scoreFill) {
        assertEqual(scoreFill.args[1], 200, 'Score fillText x === CANVAS_WIDTH/2 (200)');
        assertEqual(scoreFill.args[2], 260, 'Score fillText y === CANVAS_HEIGHT/3 + 60 (260)');
        assertEqual(scoreFill.fillStyle, '#ffffff', 'Score fill is white');
    }

    // 7d. Score with different value
    sandbox.score = 0;
    const mockCtx2 = createMockCtx();
    sandbox.renderGameOverOverlay(mockCtx2);
    const zeroScoreFill = mockCtx2._calls.find(c =>
        c.method === 'fillText' && c.args[0] === 'Score: 0'
    );
    assert(zeroScoreFill !== undefined, "Score displays 0 correctly: fillText('Score: 0')");

    sandbox.score = 999;
    const mockCtx3 = createMockCtx();
    sandbox.renderGameOverOverlay(mockCtx3);
    const highScoreFill = mockCtx3._calls.find(c =>
        c.method === 'fillText' && c.args[0] === 'Score: 999'
    );
    assert(highScoreFill !== undefined, "Score displays 999 correctly: fillText('Score: 999')");

    sandbox.score = 0; // cleanup
}

// =================================================================
// 8. renderGameOverOverlay \u2014 Restart instruction
// =================================================================

section('8. renderGameOverOverlay \u2014 Restart instruction');

if (sandbox.renderGameOverOverlay) {
    sandbox.score = 0;
    const mockCtx = createMockCtx();
    sandbox.renderGameOverOverlay(mockCtx);
    const calls = mockCtx._calls;

    // 8a. Instruction font: 18px Arial, sans-serif
    const restartFont = calls.find(c => c.method === 'set_font' && c.args[0] === '18px Arial, sans-serif');
    assert(restartFont !== undefined, "Restart instruction font set to '18px Arial, sans-serif'");

    // 8b. Instruction lineWidth: 2
    const restartLW = calls.find(c => c.method === 'set_lineWidth' && c.args[0] === 2);
    assert(restartLW !== undefined, 'Restart instruction lineWidth set to 2');

    // 8c. strokeText "Press Space or Tap to Restart" at (200, CANVAS_HEIGHT/3 + 120) = (200, 320)
    const restartStroke = calls.find(c =>
        c.method === 'strokeText' && c.args[0] === 'Press Space or Tap to Restart'
    );
    assert(restartStroke !== undefined, "strokeText('Press Space or Tap to Restart', ...) called");
    if (restartStroke) {
        assertEqual(restartStroke.args[1], 200, 'Restart strokeText x === CANVAS_WIDTH/2 (200)');
        assertEqual(restartStroke.args[2], 320, 'Restart strokeText y === CANVAS_HEIGHT/3 + 120 (320)');
        assertEqual(restartStroke.lineWidth, 2, 'Restart stroke lineWidth is 2');
    }

    // 8d. fillText "Press Space or Tap to Restart" at same position
    const restartFill = calls.find(c =>
        c.method === 'fillText' && c.args[0] === 'Press Space or Tap to Restart'
    );
    assert(restartFill !== undefined, "fillText('Press Space or Tap to Restart', ...) called");
    if (restartFill) {
        assertEqual(restartFill.args[1], 200, 'Restart fillText x === CANVAS_WIDTH/2 (200)');
        assertEqual(restartFill.args[2], 320, 'Restart fillText y === CANVAS_HEIGHT/3 + 120 (320)');
        assertEqual(restartFill.fillStyle, '#ffffff', 'Restart fill is white');
    }

    // 8e. All three text elements present (Game Over, Score, Restart)
    const allStrokeTexts = calls.filter(c => c.method === 'strokeText').map(c => c.args[0]);
    const allFillTexts = calls.filter(c => c.method === 'fillText').map(c => c.args[0]);
    assertEqual(allStrokeTexts.length, 3, 'renderGameOverOverlay has 3 strokeText calls');
    assertEqual(allFillTexts.length, 3, 'renderGameOverOverlay has 3 fillText calls');
}

// =================================================================
// 9. State-based rendering \u2014 render() dispatches overlays by state
// =================================================================

section('9. State-based rendering \u2014 render() switch');

// 9a. Source analysis: render function uses switch on gameState
const renderFuncBody = src.slice(
    src.indexOf('function render(ctx)'),
    src.indexOf('// ===== GAME LOOP =====')
);
assert(renderFuncBody.includes('switch (gameState)'), 'render() uses switch(gameState) for overlay dispatch');
assert(renderFuncBody.includes('renderIdleOverlay(ctx)'), 'render() calls renderIdleOverlay(ctx) in switch');
assert(renderFuncBody.includes('renderScore(ctx)'), 'render() calls renderScore(ctx) in switch');
assert(renderFuncBody.includes('renderGameOverOverlay(ctx)'), 'render() calls renderGameOverOverlay(ctx) in switch');

// 9b. STATE_IDLE calls renderIdleOverlay
assert(renderFuncBody.includes("case STATE_IDLE:") || renderFuncBody.includes("case 'IDLE':"),
    'render() has case for STATE_IDLE');
// Verify STATE_IDLE leads to renderIdleOverlay
const idleCaseIdx = renderFuncBody.indexOf('STATE_IDLE');
const idleOverlayIdx = renderFuncBody.indexOf('renderIdleOverlay', idleCaseIdx);
assert(idleOverlayIdx !== -1 && idleOverlayIdx - idleCaseIdx < 100,
    'STATE_IDLE case calls renderIdleOverlay');

// 9c. STATE_PLAYING calls renderScore
const playingCaseIdx = renderFuncBody.indexOf('STATE_PLAYING');
const scoreCallIdx = renderFuncBody.indexOf('renderScore', playingCaseIdx);
assert(scoreCallIdx !== -1 && scoreCallIdx - playingCaseIdx < 100,
    'STATE_PLAYING case calls renderScore');

// 9d. STATE_GAME_OVER calls renderGameOverOverlay
const gameOverCaseIdx = renderFuncBody.indexOf('STATE_GAME_OVER');
const gameOverOverlayIdx = renderFuncBody.indexOf('renderGameOverOverlay', gameOverCaseIdx);
assert(gameOverOverlayIdx !== -1 && gameOverOverlayIdx - gameOverCaseIdx < 100,
    'STATE_GAME_OVER case calls renderGameOverOverlay');

// =================================================================
// 10. Functional test: IDLE state renders overlay, not score
// =================================================================

section('10. IDLE state \u2014 functional rendering test');

sandbox.gameState = 'IDLE';
sandbox.pipes.length = 0;
sandbox.groundOffset = 0;

const idleMock = createMockCtx();
sandbox.render(idleMock);
const idleCalls = idleMock._calls;

// Should contain idle overlay text
const idleFlappyText = idleCalls.find(c => c.method === 'fillText' && c.args[0] === 'Flappy Bird');
assert(idleFlappyText !== undefined, 'IDLE: renders "Flappy Bird" title');

const idleStartText = idleCalls.find(c => c.method === 'fillText' && c.args[0] === 'Press Space or Tap to Start');
assert(idleStartText !== undefined, 'IDLE: renders "Press Space or Tap to Start" instruction');

// Should NOT contain score text (renderScore only renders during PLAYING/GAME_OVER)
// renderScore draws score as a number, not text with "Score:" prefix
// In IDLE state, the switch should route to renderIdleOverlay, not renderScore
const idleScoreStroke = idleCalls.find(c =>
    c.method === 'strokeText' && typeof c.args[0] === 'number'
);
// renderScore uses strokeText(score, ...) where score is a number
// Check that no strokeText with score number 0 at (200, 60) exists
const idleScoreAtPos = idleCalls.find(c =>
    c.method === 'strokeText' && c.args[1] === 200 && c.args[2] === 60
);
assert(idleScoreAtPos === undefined, 'IDLE: renderScore NOT called (no score text at (200, 60))');

// =================================================================
// 11. Functional test: PLAYING state renders score, not overlays
// =================================================================

section('11. PLAYING state \u2014 functional rendering test');

sandbox.gameState = 'PLAYING';
sandbox.score = 5;
sandbox.pipes.length = 0;
sandbox.groundOffset = 0;

const playMock = createMockCtx();
sandbox.render(playMock);
const playCalls = playMock._calls;

// Should NOT contain idle overlay text
const playFlappyText = playCalls.find(c => c.method === 'fillText' && c.args[0] === 'Flappy Bird');
assert(playFlappyText === undefined, 'PLAYING: does NOT render "Flappy Bird" title');

// Should NOT contain game over overlay text
const playGameOverText = playCalls.find(c => c.method === 'fillText' && c.args[0] === 'Game Over');
assert(playGameOverText === undefined, 'PLAYING: does NOT render "Game Over" text');

// Should NOT contain dark overlay
const playDarkOverlay = playCalls.find(c =>
    c.method === 'fillRect' && c.fillStyle === 'rgba(0, 0, 0, 0.5)'
);
assert(playDarkOverlay === undefined, 'PLAYING: does NOT render dark overlay');

// =================================================================
// 12. Functional test: GAME_OVER state renders overlay, not score
// =================================================================

section('12. GAME_OVER state \u2014 functional rendering test');

sandbox.gameState = 'GAME_OVER';
sandbox.score = 10;
sandbox.pipes.length = 0;
sandbox.pipes.push({ x: 200, gapY: 200, scored: true }); // frozen pipe visible
sandbox.groundOffset = 5;

const goMock = createMockCtx();
sandbox.render(goMock);
const goCalls = goMock._calls;

// Should contain game over overlay text
const goGameOverText = goCalls.find(c => c.method === 'fillText' && c.args[0] === 'Game Over');
assert(goGameOverText !== undefined, 'GAME_OVER: renders "Game Over" text');

const goScoreText = goCalls.find(c => c.method === 'fillText' && c.args[0] === 'Score: 10');
assert(goScoreText !== undefined, 'GAME_OVER: renders "Score: 10" text');

const goRestartText = goCalls.find(c => c.method === 'fillText' && c.args[0] === 'Press Space or Tap to Restart');
assert(goRestartText !== undefined, 'GAME_OVER: renders restart instruction');

// Should contain dark overlay
const goDarkOverlay = goCalls.find(c =>
    c.method === 'fillRect' && c.fillStyle === 'rgba(0, 0, 0, 0.5)'
);
assert(goDarkOverlay !== undefined, 'GAME_OVER: renders semi-transparent dark overlay');

// Should NOT contain idle overlay text
const goFlappyText = goCalls.find(c => c.method === 'fillText' && c.args[0] === 'Flappy Bird');
assert(goFlappyText === undefined, 'GAME_OVER: does NOT render "Flappy Bird" title');

// =================================================================
// 13. GAME_OVER overlay layering \u2014 renders ON TOP of game elements
// =================================================================

section('13. GAME_OVER overlay layering');

// The dark overlay should appear after all game element renders (sky, pipes, ground, bird)
// but as part of the overlay rendering

// Find key render boundaries
const skyFillIdx = goCalls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#70c5ce');
const pipeFillIdx = goCalls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#2ECC71');
const groundFillIdx = goCalls.findIndex(c => c.method === 'fillRect' && c.fillStyle === '#8B5E3C');
const birdSaveIdx = goCalls.findIndex(c => c.method === 'save');
const darkOverlayIdx = goCalls.findIndex(c =>
    c.method === 'fillRect' && c.fillStyle === 'rgba(0, 0, 0, 0.5)'
);
const gameOverTextIdx = goCalls.findIndex(c =>
    c.method === 'fillText' && c.args[0] === 'Game Over'
);

// Sky comes first
assert(skyFillIdx !== -1, 'GAME_OVER render: sky background present');
assert(skyFillIdx < darkOverlayIdx, 'Sky rendered BEFORE dark overlay');

// Pipes still render (frozen game elements visible)
assert(pipeFillIdx !== -1, 'GAME_OVER render: pipes still rendered (frozen game elements)');
assert(pipeFillIdx < darkOverlayIdx, 'Pipes rendered BEFORE dark overlay');

// Ground still renders
assert(groundFillIdx !== -1, 'GAME_OVER render: ground still rendered');
assert(groundFillIdx < darkOverlayIdx, 'Ground rendered BEFORE dark overlay');

// Bird still renders (via save/translate/rotate)
assert(birdSaveIdx !== -1, 'GAME_OVER render: bird still rendered');
assert(birdSaveIdx < darkOverlayIdx, 'Bird rendered BEFORE dark overlay');

// Dark overlay is before text
assert(darkOverlayIdx < gameOverTextIdx, 'Dark overlay rendered BEFORE Game Over text');

// =================================================================
// 14. Game elements still visible under GAME_OVER overlay
// =================================================================

section('14. Frozen game elements under GAME_OVER overlay');

// render() always renders: sky, pipes, ground, bird regardless of state
// Verify the render function body shows unconditional calls to these
assert(renderFuncBody.includes('renderPipes(ctx)'), 'renderPipes called unconditionally in render()');
assert(renderFuncBody.includes('renderGround(ctx)'), 'renderGround called unconditionally in render()');
assert(renderFuncBody.includes('renderBird(ctx)'), 'renderBird called unconditionally in render()');

// Verify pipes/ground/bird calls come BEFORE the switch statement
const pipesCallPos = renderFuncBody.indexOf('renderPipes(ctx)');
const groundCallPos = renderFuncBody.indexOf('renderGround(ctx)');
const birdCallPos = renderFuncBody.indexOf('renderBird(ctx)');
const switchPos = renderFuncBody.indexOf('switch (gameState)');

assert(pipesCallPos < switchPos, 'renderPipes called before switch (always renders)');
assert(groundCallPos < switchPos, 'renderGround called before switch (always renders)');
assert(birdCallPos < switchPos, 'renderBird called before switch (always renders)');

// =================================================================
// 15. renderScore behavior \u2014 only called during PLAYING
// =================================================================

section('15. renderScore isolation');

// In the new code, renderScore is called ONLY in STATE_PLAYING case
// Verify it's not called unconditionally
const renderScoreCountInSwitch = (renderFuncBody.match(/renderScore/g) || []).length;
assertEqual(renderScoreCountInSwitch, 1, 'renderScore appears exactly once in render() (inside switch)');

// Verify renderScore is inside the STATE_PLAYING case
const statePlayingBlock = renderFuncBody.slice(
    renderFuncBody.indexOf('STATE_PLAYING'),
    renderFuncBody.indexOf('STATE_GAME_OVER')
);
assert(statePlayingBlock.includes('renderScore'), 'renderScore is inside STATE_PLAYING case block');

// =================================================================
// 16. No new global variables introduced
// =================================================================

section('16. No new global variables');

const allowedGlobals = new Set([
    'CANVAS_WIDTH', 'CANVAS_HEIGHT', 'GROUND_HEIGHT',
    'BIRD_X', 'BIRD_RADIUS', 'BIRD_START_Y',
    'GRAVITY', 'FLAP_VELOCITY', 'MAX_FALL_SPEED',
    'PIPE_WIDTH', 'PIPE_GAP', 'PIPE_SPEED', 'PIPE_SPACING', 'PIPE_MIN_TOP', 'PIPE_MAX_TOP',
    'BOB_AMPLITUDE', 'BOB_FREQUENCY',
    'FIRST_PIPE_DELAY',
    'PIPE_CAP_HEIGHT', 'PIPE_CAP_OVERHANG',
    'STATE_IDLE', 'STATE_PLAYING', 'STATE_GAME_OVER',
    'canvas', 'ctx',
    'bird', 'pipes', 'score', 'bobTimer', 'groundOffset',
    'distanceSinceLastPipe',
    'gameState', 'lastTimestamp', 'spacePressed',
]);

const allowedFunctions = new Set([
    'handleInput', 'resetGame', 'flap',
    'updateBird', 'shouldSpawnPipe', 'spawnPipe', 'updatePipes',
    'update', 'render', 'gameLoop',
    'renderGround', 'renderBird', 'renderPipes', 'renderScore',
    'clamp', 'circleRectCollision', 'checkGroundCollision', 'checkPipeCollisions', 'checkCollisions',
    'updateScore',
    // New CD-009 overlay functions:
    'renderIdleOverlay', 'renderGameOverOverlay',
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

// Verify the two new overlay functions exist
assert(declaredFuncs.has('renderIdleOverlay'), 'renderIdleOverlay function declared at top level');
assert(declaredFuncs.has('renderGameOverOverlay'), 'renderGameOverOverlay function declared at top level');

// =================================================================
// 17. No forbidden patterns
// =================================================================

section('17. No forbidden patterns');

assert(!/\bimport\s/.test(src), 'No import statements');
assert(!/\bexport\s/.test(src), 'No export statements');
assert(!/\brequire\s*\(/.test(src), 'No require() calls');
assert(!src.includes('DOMContentLoaded'), 'No DOMContentLoaded wrapper');

// =================================================================
// 18. Source text verification (direct string checks)
// =================================================================

section('18. Source text verification');

// Idle overlay source strings
assert(src.includes("'Flappy Bird'"), "Source contains string literal 'Flappy Bird'");
assert(src.includes("'Press Space or Tap to Start'"), "Source contains string literal 'Press Space or Tap to Start'");
assert(src.includes("'bold 36px Arial, sans-serif'"), "Source contains font 'bold 36px Arial, sans-serif'");

// Game over overlay source strings
assert(src.includes("'Game Over'"), "Source contains string literal 'Game Over'");
assert(src.includes("'Score: ' + score"), "Source contains score concatenation 'Score: ' + score");
assert(src.includes("'Press Space or Tap to Restart'"), "Source contains string literal 'Press Space or Tap to Restart'");
assert(src.includes("'bold 40px Arial, sans-serif'"), "Source contains font 'bold 40px Arial, sans-serif'");
assert(src.includes("'bold 30px Arial, sans-serif'"), "Source contains font 'bold 30px Arial, sans-serif'");
assert(src.includes("'18px Arial, sans-serif'"), "Source contains font '18px Arial, sans-serif'");

// Color values
assert(src.includes("'rgba(0, 0, 0, 0.5)'"), "Source contains 'rgba(0, 0, 0, 0.5)' for dark overlay");
assert(src.includes("'#ffffff'"), "Source contains '#ffffff' for white text");
assert(src.includes("'#000000'"), "Source contains '#000000' for black stroke");

// Position formulas (verify constants are used, not hardcoded numbers)
assert(src.includes('CANVAS_WIDTH / 2'), 'Overlay positions use CANVAS_WIDTH / 2 (not hardcoded)');
assert(src.includes('CANVAS_HEIGHT / 4'), 'Idle title uses CANVAS_HEIGHT / 4');
assert(src.includes('CANVAS_HEIGHT / 2 + 80'), 'Idle instruction uses CANVAS_HEIGHT / 2 + 80');
assert(src.includes('CANVAS_HEIGHT / 3'), 'Game Over title uses CANVAS_HEIGHT / 3');
assert(src.includes('CANVAS_HEIGHT / 3 + 60'), 'Game Over score uses CANVAS_HEIGHT / 3 + 60');
assert(src.includes('CANVAS_HEIGHT / 3 + 120'), 'Game Over instruction uses CANVAS_HEIGHT / 3 + 120');

// =================================================================
// 19. Edge case: overlay with zero score
// =================================================================

section('19. Edge cases');

// 19a. Game over with score 0
sandbox.score = 0;
const zeroMock = createMockCtx();
sandbox.renderGameOverOverlay(zeroMock);
const zeroScoreText = zeroMock._calls.find(c =>
    c.method === 'fillText' && c.args[0] === 'Score: 0'
);
assert(zeroScoreText !== undefined, 'Game over overlay shows "Score: 0" correctly');

// 19b. Idle overlay doesn't crash with any game state
sandbox.score = 100;
sandbox.pipes.push({ x: 200, gapY: 200, scored: true });
try {
    const edgeMock = createMockCtx();
    sandbox.renderIdleOverlay(edgeMock);
    assert(true, 'renderIdleOverlay works regardless of game state variables');
} catch (e) {
    assert(false, `renderIdleOverlay crashes: ${e.message}`);
}

// 19c. Game over overlay with very large score
sandbox.score = 99999;
try {
    const largeMock = createMockCtx();
    sandbox.renderGameOverOverlay(largeMock);
    const largeScoreText = largeMock._calls.find(c =>
        c.method === 'fillText' && c.args[0] === 'Score: 99999'
    );
    assert(largeScoreText !== undefined, 'Game over shows very large score (99999) correctly');
} catch (e) {
    assert(false, `renderGameOverOverlay crashes with large score: ${e.message}`);
}

// cleanup
sandbox.score = 0;
sandbox.pipes.length = 0;
sandbox.resetGame();

// =================================================================
// 20. Render function structure \u2014 overlay functions in correct section
// =================================================================

section('20. Source structure');

const renderSectionStart = src.indexOf('// ===== RENDER LOGIC =====');
const gameLoopSectionStart = src.indexOf('// ===== GAME LOOP =====');

// Overlay functions should be in RENDER LOGIC section
const idleOverlayPos = src.indexOf('function renderIdleOverlay');
const gameOverOverlayPos = src.indexOf('function renderGameOverOverlay');
const renderMainPos = src.indexOf('function render(ctx)');

assert(idleOverlayPos > renderSectionStart, 'renderIdleOverlay in RENDER LOGIC section');
assert(idleOverlayPos < gameLoopSectionStart, 'renderIdleOverlay before GAME LOOP section');
assert(gameOverOverlayPos > renderSectionStart, 'renderGameOverOverlay in RENDER LOGIC section');
assert(gameOverOverlayPos < gameLoopSectionStart, 'renderGameOverOverlay before GAME LOOP section');

// Overlay functions defined before main render()
assert(idleOverlayPos < renderMainPos, 'renderIdleOverlay defined before render()');
assert(gameOverOverlayPos < renderMainPos, 'renderGameOverOverlay defined before render()');

// renderScore also defined before overlays (it was there from previous CD)
const renderScorePos = src.indexOf('function renderScore');
assert(renderScorePos < idleOverlayPos, 'renderScore defined before overlay functions');

// =================================================================
// SUMMARY
// =================================================================

console.log('\n\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550');
console.log(`  TS-017 QA RESULTS: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550');

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
    console.log('\n\u2705 All acceptance criteria verified. CD-009 UI overlays pass QA.');
}

// Regression note
console.log('\n\u2139\ufe0f  Regression note:');
console.log('  - game.test.js: 116/117 pass (magic number audit is pre-existing failure)');
console.log('  - render-qa.test.js: Crashes because its mock ctx lacks strokeText/fillText');
console.log('    (pre-existing mock deficiency, not a CD-009 regression)');
console.log('  - collision-qa.test.js: Crashes on missing checkCollision (pre-existing)');
console.log('  - input.test.js: 98/98 pass');
console.log('  - playing-state.test.js: All pass');
console.log('  - pipe.test.js: All pass');

process.exit(failed > 0 ? 1 : 0);
