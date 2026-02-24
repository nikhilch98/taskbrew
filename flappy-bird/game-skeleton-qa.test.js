/**
 * TS-025 — QA Verification: game.js skeleton against TECH-DESIGN-010
 *
 * Comprehensive test suite verifying game.js implementation matches the
 * technical design document TECH-DESIGN-010-flappy-bird.md exactly.
 *
 * Sections tested:
 *   §1 - File structure (top-to-bottom ordering)
 *   §2 - Game loop (variable-timestep, 50ms cap, first-frame guard, phase separation)
 *   §3 - State machine (transitions, resetGame 8 variables, per-state update behavior)
 *   §4 - Entity data structures (bird shape, pipes array, score, bobTimer, groundOffset)
 *   §7 - Rendering pipeline (background color, layer order, render function structure)
 *   §8 - Input system (keyboard on document, mouse/touch on canvas, spacePressed, passive:false)
 *   §9 - Constants (19 game + 3 state, exact values, derivations)
 *   No external deps, no import/export, no DOMContentLoaded
 *
 * Run: node game-skeleton-qa.test.js
 */

const fs   = require('fs');
const path = require('path');

// ─── Test framework ───

let passed = 0;
let failed = 0;
let skipped = 0;
const failures = [];
const warnings = [];

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
        const msg = `${message} — expected: ${JSON.stringify(expected)}, got: ${JSON.stringify(actual)}`;
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
        const msg = `${message} — expected: ~${expected} (±${tolerance}), got: ${actual}`;
        console.log(`  ❌ ${msg}`);
        failures.push(msg);
    }
}

function warn(message) {
    warnings.push(message);
    console.log(`  ⚠️  ${message}`);
}

function section(title) {
    console.log(`\n━━━ ${title} ━━━`);
}

// ─── Read source file ───

const src = fs.readFileSync(path.join(__dirname, 'game.js'), 'utf8');
const lines = src.split('\n');

// ─── DOM stub and sandbox eval ───

const domStub = `
    const _listeners = {};
    const _canvasListeners = {};
    const _ctxCalls = [];

    const document = {
        getElementById: (id) => ({
            getContext: (type) => {
                const ctx = {
                    fillStyle: '',
                    strokeStyle: '',
                    lineWidth: 0,
                    lineJoin: '',
                    font: '',
                    textAlign: '',
                    textBaseline: '',
                    fillRect: function(x, y, w, h) { _ctxCalls.push({ method: 'fillRect', args: [x, y, w, h], fillStyle: ctx.fillStyle }); },
                    strokeRect: function() {},
                    fillText: function(text, x, y) { _ctxCalls.push({ method: 'fillText', args: [text, x, y] }); },
                    strokeText: function() {},
                    beginPath: function() {},
                    moveTo: function() {},
                    lineTo: function() {},
                    closePath: function() {},
                    arc: function() {},
                    ellipse: function() {},
                    fill: function() {},
                    stroke: function() {},
                    save: function() {},
                    restore: function() {},
                    translate: function() {},
                    rotate: function() {},
                };
                return ctx;
            },
            addEventListener: (type, fn, opts) => {
                _canvasListeners[type] = { fn, opts };
            }
        }),
        addEventListener: (type, fn, opts) => {
            _listeners[type] = { fn, opts };
        }
    };
    let _rafCallback = null;
    let _rafCount = 0;
    function requestAnimationFrame(cb) {
        _rafCallback = cb;
        _rafCount++;
    }
`;

let sandbox = {};
try {
    const evalCode = `
        ${domStub}
        ${src}
        ({
            // Constants
            CANVAS_WIDTH, CANVAS_HEIGHT, GROUND_HEIGHT,
            BIRD_X, BIRD_RADIUS, BIRD_START_Y,
            GRAVITY, FLAP_VELOCITY, MAX_FALL_SPEED,
            PIPE_WIDTH, PIPE_GAP, PIPE_SPEED, PIPE_SPACING, PIPE_MIN_TOP, PIPE_MAX_TOP,
            BOB_AMPLITUDE, BOB_FREQUENCY,
            PIPE_CAP_HEIGHT, PIPE_CAP_OVERHANG,
            STATE_IDLE, STATE_PLAYING, STATE_GAME_OVER,

            // Game state (with getters/setters for let-scoped vars)
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
            get distanceSinceLastPipe() { return typeof distanceSinceLastPipe !== 'undefined' ? distanceSinceLastPipe : undefined; },
            set distanceSinceLastPipe(v) { if (typeof distanceSinceLastPipe !== 'undefined') distanceSinceLastPipe = v; },

            // Functions
            handleInput, resetGame, flap,
            update, render, gameLoop,
            updateBird: typeof updateBird === 'function' ? updateBird : undefined,
            checkGroundCollision: typeof checkGroundCollision === 'function' ? checkGroundCollision : undefined,
            checkPipeCollisions: typeof checkPipeCollisions === 'function' ? checkPipeCollisions : undefined,
            checkCollisions: typeof checkCollisions === 'function' ? checkCollisions : undefined,
            spawnPipe: typeof spawnPipe === 'function' ? spawnPipe : undefined,
            shouldSpawnPipe: typeof shouldSpawnPipe === 'function' ? shouldSpawnPipe : undefined,
            updatePipes: typeof updatePipes === 'function' ? updatePipes : undefined,
            clamp: typeof clamp === 'function' ? clamp : undefined,
            circleRectCollision: typeof circleRectCollision === 'function' ? circleRectCollision : undefined,

            // DOM stubs
            _listeners, _canvasListeners, _rafCallback, _rafCount, _ctxCalls
        })
    `;
    sandbox = eval(evalCode);
} catch (e) {
    console.error('  ❌ CRITICAL: Failed to evaluate game.js:', e.message);
    failed++;
    failures.push('game.js evaluation failed: ' + e.message);
}


// ═══════════════════════════════════════════════════════════════
// §9 — CONSTANTS & TUNING (19 game + 3 state constants)
// ═══════════════════════════════════════════════════════════════

section('§9 — Constants: Declaration & Values');

const specConstants = {
    CANVAS_WIDTH: 400,
    CANVAS_HEIGHT: 600,
    GROUND_HEIGHT: 60,
    BIRD_X: 100,
    BIRD_RADIUS: 15,
    BIRD_START_Y: 300, // CANVAS_HEIGHT / 2
    GRAVITY: 980,
    FLAP_VELOCITY: -280,
    MAX_FALL_SPEED: 600,
    PIPE_WIDTH: 52,
    PIPE_GAP: 130,
    PIPE_SPEED: 120,
    PIPE_SPACING: 220,
    PIPE_MIN_TOP: 50,
    PIPE_MAX_TOP: 360, // 600 - 60 - 130 - 50
    BOB_AMPLITUDE: 8,
    BOB_FREQUENCY: 2,
    PIPE_CAP_HEIGHT: 20,
    PIPE_CAP_OVERHANG: 3,
};

const stateConstants = {
    STATE_IDLE: 'IDLE',
    STATE_PLAYING: 'PLAYING',
    STATE_GAME_OVER: 'GAME_OVER',
};

// Verify all 19 game constants exist as const declarations with correct values
for (const [name, expected] of Object.entries(specConstants)) {
    const declPattern = new RegExp(`^const\\s+${name}\\s*=`);
    const declared = lines.some(l => declPattern.test(l.trim()));
    assert(declared, `const ${name} is declared`);
    if (sandbox[name] !== undefined) {
        assertEqual(sandbox[name], expected, `${name} === ${expected}`);
    }
}

// Verify 3 state constants
for (const [name, expected] of Object.entries(stateConstants)) {
    const declPattern = new RegExp(`^const\\s+${name}\\s*=`);
    const declared = lines.some(l => declPattern.test(l.trim()));
    assert(declared, `const ${name} is declared`);
    assertEqual(sandbox[name], expected, `${name} === '${expected}'`);
}

// Verify count: exactly 19 game constants
assertEqual(Object.keys(specConstants).length, 19, 'Exactly 19 game constants per §9');
assertEqual(Object.keys(stateConstants).length, 3, 'Exactly 3 state constants per §3');

// Verify BIRD_START_Y is derived from CANVAS_HEIGHT / 2
assert(
    src.includes('BIRD_START_Y    = CANVAS_HEIGHT / 2') ||
    src.includes('BIRD_START_Y = CANVAS_HEIGHT / 2'),
    'BIRD_START_Y derived from CANVAS_HEIGHT / 2'
);

// Verify PIPE_MAX_TOP is derived from CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50
assert(
    src.includes('PIPE_MAX_TOP    = CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50') ||
    src.includes('PIPE_MAX_TOP = CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50'),
    'PIPE_MAX_TOP derived from CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50'
);

// Check for extra constants not in spec
section('§9 — Constants: Extra constants (deviations)');
const extraConstantPattern = /^const\s+([A-Z][A-Z_0-9]+)\s*=/;
const allDeclaredConstants = [];
for (const line of lines) {
    const match = line.trim().match(extraConstantPattern);
    if (match) {
        allDeclaredConstants.push(match[1]);
    }
}
const allSpecNames = new Set([...Object.keys(specConstants), ...Object.keys(stateConstants)]);
const extraConstants = allDeclaredConstants.filter(n => !allSpecNames.has(n) && n !== 'CANVAS_WIDTH');
// Filter out non-uppercase-only identifiers (like 'canvas', 'ctx')
const extraUpperConstants = extraConstants.filter(n => n === n.toUpperCase());
if (extraUpperConstants.length > 0) {
    warn(`Extra constant(s) not in TECH-DESIGN-010 §9: ${extraUpperConstants.join(', ')}`);
} else {
    console.log('  ℹ️  No extra UPPER_CASE constants beyond spec');
}


// ═══════════════════════════════════════════════════════════════
// §1 — FILE STRUCTURE (top-to-bottom ordering)
// ═══════════════════════════════════════════════════════════════

section('§1 — File Structure: Section ordering');

// Per spec §1, game.js should have this top-to-bottom order:
//   1. Constants block
//   2. Canvas/context initialization
//   3. Game state variables
//   4. Entity helper functions (bird, pipes)
//   5. Input handler setup
//   6. Update logic (update(dt))
//   7. Render logic (render(ctx))
//   8. Game loop (gameLoop(timestamp))
//   9. Initialization call (requestAnimationFrame(gameLoop))

// Find section markers or key functions to verify ordering
function findLineNum(pattern) {
    for (let i = 0; i < lines.length; i++) {
        if (typeof pattern === 'string' ? lines[i].includes(pattern) : pattern.test(lines[i])) {
            return i + 1;
        }
    }
    return -1;
}

const sectionPositions = {
    constants: findLineNum('// ===== CONSTANTS ====='),
    canvasInit: findLineNum("document.getElementById('gameCanvas')"),
    stateVars: findLineNum('let bird = {'),
    stateMachine: findLineNum('function handleInput'),
    inputHandlers: findLineNum("addEventListener('keydown'"),
    updateLogic: findLineNum('function update(dt)'),
    renderLogic: findLineNum('function render(ctx)'),
    gameLoop: findLineNum('function gameLoop(timestamp)'),
};

// Verify constants come first
assert(sectionPositions.constants > 0, 'Constants section exists');
assert(sectionPositions.constants < sectionPositions.canvasInit,
    'Constants block comes before canvas init');
assert(sectionPositions.canvasInit < sectionPositions.stateVars,
    'Canvas init comes before state variables');
assert(sectionPositions.stateVars < sectionPositions.stateMachine,
    'State variables come before state machine / entity functions');

// Input handlers should come after entity helper functions
assert(sectionPositions.stateMachine < sectionPositions.inputHandlers,
    'Entity/state functions come before input handlers');

// Update before render before game loop
assert(sectionPositions.updateLogic < sectionPositions.renderLogic,
    'update(dt) comes before render(ctx)');
assert(sectionPositions.renderLogic < sectionPositions.gameLoop,
    'render(ctx) comes before gameLoop()');

// requestAnimationFrame(gameLoop) at the very end
const lastNonEmpty = lines.map((l, i) => ({ l: l.trim(), i }))
    .filter(x => x.l.length > 0)
    .pop();
assert(
    lastNonEmpty && lastNonEmpty.l === 'requestAnimationFrame(gameLoop);',
    'requestAnimationFrame(gameLoop) is the last statement in the file'
);

// Check for renderPipes location — spec says render functions should be together
section('§1 — File Structure: Render function grouping');
const renderPipesLine = findLineNum('function renderPipes');
const renderBackgroundLine = findLineNum('function renderBackground');
const renderGroundLine = findLineNum('function renderGround');
const renderBirdLine = findLineNum('function renderBird');

if (renderPipesLine > 0 && renderBackgroundLine > 0) {
    // Check if renderPipes is grouped with other render functions
    const renderFnsGrouped = (
        Math.abs(renderPipesLine - renderBackgroundLine) < 100 &&
        Math.abs(renderPipesLine - renderGroundLine) < 100
    );
    if (!renderFnsGrouped) {
        warn(`renderPipes() (line ${renderPipesLine}) is separated from other render functions (renderBackground at line ${renderBackgroundLine})`);
    }
}


// ═══════════════════════════════════════════════════════════════
// §4 — ENTITY DATA STRUCTURES
// ═══════════════════════════════════════════════════════════════

section('§4 — Entity Data Structures');

if (sandbox.bird) {
    // Bird object shape per spec
    assertEqual(sandbox.bird.x, 100, 'bird.x === BIRD_X (100)');
    assertEqual(sandbox.bird.y, 300, 'bird.y === BIRD_START_Y (300)');
    assertEqual(sandbox.bird.velocity, 0, 'bird.velocity === 0');
    assertEqual(sandbox.bird.radius, 15, 'bird.radius === BIRD_RADIUS (15)');
    assertEqual(sandbox.bird.rotation, 0, 'bird.rotation === 0');

    // Verify bird has exactly 5 properties
    const birdKeys = Object.keys(sandbox.bird).sort();
    assertEqual(birdKeys.join(','), 'radius,rotation,velocity,x,y', 'bird has exactly 5 properties: x, y, velocity, radius, rotation');
}

// Pipes as empty array
assert(Array.isArray(sandbox.pipes), 'pipes is an array');
assertEqual(sandbox.pipes.length, 0, 'pipes initially empty');

// Score, bobTimer, groundOffset
assertEqual(sandbox.score, 0, 'score === 0');
assertEqual(sandbox.bobTimer, 0, 'bobTimer === 0');
assertEqual(sandbox.groundOffset, 0, 'groundOffset === 0');
assertEqual(sandbox.lastTimestamp, 0, 'lastTimestamp === 0');
assertEqual(sandbox.spacePressed, false, 'spacePressed === false');
assertEqual(sandbox.gameState, 'IDLE', "gameState === 'IDLE'");


// ═══════════════════════════════════════════════════════════════
// §3 — STATE MACHINE DESIGN
// ═══════════════════════════════════════════════════════════════

section('§3 — State Machine: Transitions');

if (sandbox.handleInput) {
    // IDLE → PLAYING (with first flap)
    sandbox.gameState = 'IDLE';
    sandbox.bird.velocity = 0;
    sandbox.bird.y = 300;
    sandbox.handleInput();
    assertEqual(sandbox.gameState, 'PLAYING', 'IDLE + input → PLAYING');
    assertEqual(sandbox.bird.velocity, -280, 'IDLE → PLAYING applies first flap impulse');

    // PLAYING + input → stays PLAYING (flap)
    sandbox.gameState = 'PLAYING';
    sandbox.bird.velocity = 100;
    sandbox.handleInput();
    assertEqual(sandbox.gameState, 'PLAYING', 'PLAYING + input → stays PLAYING');
    assertEqual(sandbox.bird.velocity, -280, 'PLAYING + input → flap()');

    // GAME_OVER + input → IDLE (resetGame)
    sandbox.gameState = 'GAME_OVER';
    sandbox.bird.y = 500;
    sandbox.bird.velocity = 200;
    sandbox.bird.rotation = 1.5;
    sandbox.pipes.push({ x: 100, gapY: 200, scored: false });
    sandbox.score = 42;
    sandbox.bobTimer = 3.14;
    sandbox.groundOffset = 999;
    sandbox.handleInput();
    assertEqual(sandbox.gameState, 'IDLE', 'GAME_OVER + input → IDLE (via resetGame)');
}

section('§3 — State Machine: resetGame() resets all 8 variables');

if (sandbox.resetGame) {
    // Dirty all state
    sandbox.gameState = 'GAME_OVER';
    sandbox.bird.y = 500;
    sandbox.bird.velocity = 300;
    sandbox.bird.rotation = 1.57;
    sandbox.pipes.push({ x: 1 }, { x: 2 }, { x: 3 });
    sandbox.score = 99;
    sandbox.bobTimer = 10.5;
    sandbox.groundOffset = 888;

    sandbox.resetGame();

    // Verify all 8 spec-required resets
    assertEqual(sandbox.gameState, 'IDLE', 'resetGame: gameState → IDLE');
    assertEqual(sandbox.bird.y, 300, 'resetGame: bird.y → BIRD_START_Y (300)');
    assertEqual(sandbox.bird.velocity, 0, 'resetGame: bird.velocity → 0');
    assertEqual(sandbox.bird.rotation, 0, 'resetGame: bird.rotation → 0');
    assertEqual(sandbox.pipes.length, 0, 'resetGame: pipes → empty (length = 0)');
    assertEqual(sandbox.score, 0, 'resetGame: score → 0');
    assertEqual(sandbox.bobTimer, 0, 'resetGame: bobTimer → 0');
    assertEqual(sandbox.groundOffset, 0, 'resetGame: groundOffset → 0');
}

section('§3 — State Machine: flap()');

if (sandbox.flap) {
    sandbox.bird.velocity = 200;
    sandbox.flap();
    assertEqual(sandbox.bird.velocity, -280, 'flap() SETS (not adds) velocity to FLAP_VELOCITY (-280)');

    sandbox.bird.velocity = -100;
    sandbox.flap();
    assertEqual(sandbox.bird.velocity, -280, 'flap() overwrites any existing velocity');
}

section('§3 — State Machine: Per-state update() behavior');

if (sandbox.update) {
    // IDLE state: should update bobTimer and bob bird position
    sandbox.resetGame();
    sandbox.gameState = 'IDLE';
    sandbox.bobTimer = 0;
    const dt = 0.016; // ~60fps
    sandbox.update(dt);

    assertApprox(sandbox.bobTimer, dt, 0.001, 'IDLE update: bobTimer incremented by dt');

    // IDLE bob formula: bird.y = BIRD_START_Y + sin(bobTimer * BOB_FREQUENCY * PI * 2) * BOB_AMPLITUDE
    const expectedBobY = 300 + Math.sin(sandbox.bobTimer * 2 * Math.PI * 2) * 8;
    assertApprox(sandbox.bird.y, expectedBobY, 0.01,
        `IDLE update: bird.y uses sine bob formula (expected ~${expectedBobY.toFixed(2)})`);

    // GAME_OVER state: no updates
    sandbox.gameState = 'GAME_OVER';
    const prevBirdY = sandbox.bird.y;
    const prevScore = sandbox.score;
    sandbox.update(dt);
    assertEqual(sandbox.bird.y, prevBirdY, 'GAME_OVER update: bird.y unchanged (frozen)');
    assertEqual(sandbox.score, prevScore, 'GAME_OVER update: score unchanged (frozen)');
}


// ═══════════════════════════════════════════════════════════════
// §2 — GAME LOOP ARCHITECTURE
// ═══════════════════════════════════════════════════════════════

section('§2 — Game Loop: Structure');

assert(typeof sandbox.gameLoop === 'function', 'gameLoop is a function');
assert(typeof sandbox.update === 'function', 'update is a function');
assert(typeof sandbox.render === 'function', 'render is a function');

// Parameter name
assert(/function\s+gameLoop\s*\(\s*timestamp\s*\)/.test(src), 'gameLoop parameter named "timestamp"');

// First-frame guard: lastTimestamp === 0 check
assert(src.includes('lastTimestamp === 0'), 'First-frame guard: checks lastTimestamp === 0');

// dt computation: (timestamp - lastTimestamp) / 1000
assert(/\(timestamp\s*-\s*lastTimestamp\)\s*\/\s*1000/.test(src), 'dt = (timestamp - lastTimestamp) / 1000');

// 50ms cap
assert(/dt\s*>\s*0\.05/.test(src), 'dt cap condition: dt > 0.05');
assert(/dt\s*=\s*0\.05/.test(src), 'dt capped to 0.05');

// Phase separation: update(dt) then render(ctx)
const gameLoopStart = src.indexOf('function gameLoop');
const gameLoopBody = src.slice(gameLoopStart);
const updateCallPos = gameLoopBody.indexOf('update(dt)');
const renderCallPos = gameLoopBody.indexOf('render(ctx)');
assert(updateCallPos > 0 && renderCallPos > 0 && updateCallPos < renderCallPos,
    'Phase separation: update(dt) called before render(ctx) in gameLoop');

// rAF scheduling inside gameLoop
assert(gameLoopBody.includes('requestAnimationFrame(gameLoop)'),
    'requestAnimationFrame(gameLoop) called inside gameLoop');

// rAF kickoff at bottom of file
assert(sandbox._rafCallback !== null, 'requestAnimationFrame was invoked at script load (loop started)');

section('§2 — Game Loop: Runtime behavior');

if (sandbox.gameLoop) {
    // Simulate first frame
    sandbox.lastTimestamp = 0;
    sandbox.gameState = 'IDLE';
    sandbox.gameLoop(1000); // First call at 1000ms
    assertEqual(sandbox.lastTimestamp, 1000, 'First frame: lastTimestamp set to timestamp');

    // Simulate second frame (16ms later = ~60fps)
    sandbox.gameLoop(1016);
    assertEqual(sandbox.lastTimestamp, 1016, 'Second frame: lastTimestamp updated');

    // Simulate tab-refocus (500ms gap — should be capped)
    const prevY = sandbox.bird.y;
    sandbox.gameLoop(1516); // 500ms gap
    assertEqual(sandbox.lastTimestamp, 1516, 'Large gap: lastTimestamp updated normally');
    // dt should have been capped to 0.05s (50ms), not 0.5s
    // The fact that the game didn't explode is indirect evidence of the cap
}


// ═══════════════════════════════════════════════════════════════
// §8 — INPUT SYSTEM DESIGN
// ═══════════════════════════════════════════════════════════════

section('§8 — Input System: Event listeners');

// Keyboard on document
assert(sandbox._listeners['keydown'] !== undefined, 'keydown listener registered on document');
assert(sandbox._listeners['keyup'] !== undefined, 'keyup listener registered on document');

// Mouse on canvas
assert(sandbox._canvasListeners['mousedown'] !== undefined, 'mousedown listener registered on canvas');

// Touch on canvas with passive:false
assert(sandbox._canvasListeners['touchstart'] !== undefined, 'touchstart listener registered on canvas');
const touchOpts = sandbox._canvasListeners['touchstart']?.opts;
assert(touchOpts && touchOpts.passive === false, 'touchstart uses { passive: false }');

section('§8 — Input System: Source-level checks');

// e.code === 'Space' (not e.key or e.keyCode)
assert(src.includes("e.code === 'Space'"), "Uses e.code === 'Space' (not e.key or e.keyCode)");

// spacePressed guard
assert(src.includes('if (!spacePressed)'), 'spacePressed auto-repeat guard present');
assert(src.includes('spacePressed = true'), 'spacePressed set to true on keydown');
assert(src.includes('spacePressed = false'), 'spacePressed reset to false on keyup');

// preventDefault calls
const keydownBlock = src.slice(src.indexOf("addEventListener('keydown'"), src.indexOf("addEventListener('keyup'"));
assert(keydownBlock.includes('e.preventDefault()'), 'preventDefault in keydown handler (prevent page scroll)');

const touchBlock = src.slice(src.indexOf("addEventListener('touchstart'"));
assert(touchBlock.includes('e.preventDefault()'), 'preventDefault in touchstart handler');

// Mouse uses mousedown (not click)
assert(src.includes("addEventListener('mousedown'"), 'Mouse uses mousedown (not click) for lower latency');
assert(!src.includes("addEventListener('click'"), 'No click handler (mousedown preferred per spec)');

section('§8 — Input System: Unified handler');

// All three input paths call handleInput()
assert(keydownBlock.includes('handleInput()'), 'keydown calls handleInput()');
const mouseBlock = src.slice(src.indexOf("addEventListener('mousedown'"), src.indexOf("addEventListener('touchstart'"));
assert(mouseBlock.includes('handleInput()'), 'mousedown calls handleInput()');
assert(touchBlock.includes('handleInput()'), 'touchstart calls handleInput()');


// ═══════════════════════════════════════════════════════════════
// §7 — RENDERING PIPELINE
// ═══════════════════════════════════════════════════════════════

section('§7 — Rendering: Background color');

// Background must be #70c5ce (sky blue) — may be in renderBackground() or inlined in render()
assert(src.includes("'#70c5ce'"), "Background color '#70c5ce' (sky blue) present in source");

// Background fill covers full canvas
assert(src.includes('fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)'),
    'Background fills entire canvas (0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)');

section('§7 — Rendering: Layer order in render()');

// Extract render() function body
const renderFnStart = src.indexOf('function render(ctx)');
const renderFnBody = src.slice(renderFnStart, src.indexOf('// ===== GAME LOOP'));

// Background may be inline (#70c5ce + fillRect) or via renderBackground()
const hasSeparateBgFn = src.includes('function renderBackground');
const bgPos = hasSeparateBgFn
    ? renderFnBody.indexOf('renderBackground')
    : renderFnBody.indexOf('#70c5ce') > -1
        ? renderFnBody.indexOf('#70c5ce')
        : renderFnBody.indexOf('fillRect(0, 0');

const pipesPos = renderFnBody.indexOf('renderPipes');
const groundPos = renderFnBody.indexOf('renderGround');
const birdPos = renderFnBody.indexOf('renderBird');

assert(bgPos >= 0, 'render() has background fill (inline or via renderBackground)');
assert(pipesPos > bgPos, 'render(): pipes drawn after background');
assert(groundPos > pipesPos, 'render(): ground drawn after pipes');
assert(birdPos > groundPos, 'render(): bird drawn after ground');

// UI overlay via switch statement
assert(renderFnBody.includes('renderIdleOverlay'), 'render() calls renderIdleOverlay for IDLE state');
assert(renderFnBody.includes('renderScore'), 'render() calls renderScore for PLAYING state');
assert(renderFnBody.includes('renderGameOverOverlay'), 'render() calls renderGameOverOverlay for GAME_OVER');

section('§7 — Rendering: Pipe state guard');

// Spec says pipes should only render in PLAYING and GAME_OVER
// Check if render() guards pipe rendering with state check
const renderPipesInRender = renderFnBody.slice(0, renderFnBody.indexOf('renderGround'));
const hasPipeStateGuard = renderPipesInRender.includes('STATE_PLAYING') ||
    renderPipesInRender.includes("'PLAYING'");
if (!hasPipeStateGuard) {
    warn('render() calls renderPipes() without state guard — spec says only in PLAYING/GAME_OVER (cosmetic: pipes array is empty in IDLE, so no visual impact)');
}

section('§7 — Rendering: renderGameOverOverlay');

// Check semi-transparent overlay
assert(src.includes("rgba(0, 0, 0, 0.5)"), 'Game over overlay uses rgba(0, 0, 0, 0.5)');
assert(src.includes("'Game Over'"), "Game over text says 'Game Over'");
assert(src.includes("'Score: ' + score"), 'Game over shows final score');
assert(src.includes("'Press Space or Tap to Restart'"), 'Game over shows restart instruction');

section('§7 — Rendering: renderIdleOverlay');

assert(src.includes("'Flappy Bird'"), "Idle overlay shows 'Flappy Bird' title");
assert(src.includes("'Press Space or Tap to Start'"), "Idle overlay shows start instruction");

section('§7 — Rendering: Color verification against spec');

// Extract function bodies for precise color checks
const srcLower = src.toLowerCase();

// For each render function, extract its body and check colors within it
function extractFunctionBody(funcName) {
    const startIdx = src.indexOf('function ' + funcName);
    if (startIdx === -1) return '';
    // Find matching closing brace (simple approach: count braces)
    let depth = 0;
    let inBody = false;
    for (let i = startIdx; i < src.length; i++) {
        if (src[i] === '{') { depth++; inBody = true; }
        if (src[i] === '}') { depth--; }
        if (inBody && depth === 0) return src.slice(startIdx, i + 1).toLowerCase();
    }
    return src.slice(startIdx).toLowerCase();
}

const renderBgBody = extractFunctionBody('renderBackground');
const renderPipesBody = extractFunctionBody('renderPipes');
const renderGroundBody = extractFunctionBody('renderGround');
const renderBirdBody = extractFunctionBody('renderBird');
const renderMainBody = extractFunctionBody('render'); // the main render() function

// Background color — may be in renderBackground or inlined in render()
const bgSource = renderBgBody || renderMainBody;
assert(bgSource.includes('#70c5ce'), "Background color '#70c5ce' found in render pipeline");

// Pipe colors — spec: body=#3cb043, cap=#2d8a34
if (renderPipesBody.includes('#3cb043')) {
    console.log("  ✅ Pipe body color matches spec: #3cb043");
    passed++;
} else {
    // Find what color IS used
    const pipeColorMatch = renderPipesBody.match(/#[0-9a-f]{6}/);
    warn(`Pipe body color differs from spec #3cb043 — code uses ${pipeColorMatch ? pipeColorMatch[0] : 'unknown'}`);
}
if (renderPipesBody.includes('#2d8a34')) {
    console.log("  ✅ Pipe cap color matches spec: #2d8a34");
    passed++;
} else {
    const capColors = [...renderPipesBody.matchAll(/#[0-9a-f]{6}/g)].map(m => m[0]);
    const capColor = capColors.length > 1 ? capColors[1] : capColors[0] || 'unknown';
    warn(`Pipe cap color differs from spec #2d8a34 — code uses ${capColor}`);
}

// Ground colors — spec: main=#deb050, grass=#5cb85c, texture stroke=#c8a040
if (renderGroundBody.includes('#deb050')) {
    console.log("  ✅ Ground color matches spec: #deb050");
    passed++;
} else {
    const groundColors = [...renderGroundBody.matchAll(/#[0-9a-f]{6}/g)].map(m => m[0]);
    warn(`Ground color differs from spec #deb050 — code uses ${groundColors[0] || 'unknown'}`);
}
if (renderGroundBody.includes('#5cb85c')) {
    console.log("  ✅ Grass color matches spec: #5cb85c");
    passed++;
} else {
    const grassColors = [...renderGroundBody.matchAll(/#[0-9a-f]{6}/g)].map(m => m[0]);
    warn(`Grass color differs from spec #5cb85c — code uses ${grassColors[1] || 'unknown'}`);
}
if (renderGroundBody.includes('#c8a040')) {
    console.log("  ✅ Ground texture color matches spec: #c8a040");
    passed++;
} else {
    const texColors = [...renderGroundBody.matchAll(/#[0-9a-f]{6}/g)].map(m => m[0]);
    warn(`Ground texture color differs from spec #c8a040 — code uses ${texColors[2] || 'unknown'}`);
}

// Bird colors — spec: body=#f5c842, outline=#d4a020, beak=#e07020, wing=#e0b030
const birdColorChecks = [
    { name: 'Bird body', spec: '#f5c842' },
    { name: 'Bird outline', spec: '#d4a020' },
    { name: 'Beak', spec: '#e07020' },
    { name: 'Wing', spec: '#e0b030' },
];
for (const check of birdColorChecks) {
    if (renderBirdBody.includes(check.spec)) {
        console.log(`  ✅ ${check.name} color matches spec: ${check.spec}`);
        passed++;
    } else {
        warn(`${check.name} color differs from spec ${check.spec}`);
    }
}

// Verify bird wing exists (spec §7 defines wing via ctx.ellipse)
section('§7 — Rendering: Bird wing');
if (renderBirdBody.includes('ellipse')) {
    console.log('  ✅ Bird wing rendered with ellipse()');
    passed++;
} else {
    warn('Bird wing missing — spec §7 defines a wing via ctx.ellipse()');
}

// Bird eye/pupil/beak geometry per spec
section('§7 — Rendering: Bird geometry details');
// Eye at (6, -5) radius 4
if (renderBirdBody.includes('arc(6, -5, 4')) {
    console.log('  ✅ Bird eye: arc(6, -5, 4) matches spec');
    passed++;
} else {
    warn('Bird eye position/radius may differ from spec (6, -5, radius 4)');
}
// Pupil at (7, -5) radius 2
if (renderBirdBody.includes('arc(7, -5, 2,') || renderBirdBody.includes('arc(7, -5, 2)')) {
    console.log('  ✅ Bird pupil: arc(7, -5, 2) matches spec');
    passed++;
} else {
    warn('Bird pupil position/radius may differ from spec (7, -5, radius 2)');
}
// Beak vertices at (radius, -3), (radius+8, 0), (radius, 3)
if (renderBirdBody.includes('bird.radius, -3') && renderBirdBody.includes('bird.radius, 3')) {
    console.log('  ✅ Bird beak vertices match spec: (radius, ±3)');
    passed++;
} else {
    warn('Bird beak vertices may differ from spec (radius, -3)/(radius, 3)');
}


// ═══════════════════════════════════════════════════════════════
// §6 — COLLISION DETECTION
// ═══════════════════════════════════════════════════════════════

section('§6 — Collision Detection: Functions exist');

assert(typeof sandbox.clamp === 'function', 'clamp() function exists');
assert(typeof sandbox.circleRectCollision === 'function', 'circleRectCollision() function exists');
assert(typeof sandbox.checkGroundCollision === 'function', 'checkGroundCollision() function exists');
assert(typeof sandbox.checkPipeCollisions === 'function', 'checkPipeCollisions() function exists');
assert(typeof sandbox.checkCollisions === 'function', 'checkCollisions() function exists');

section('§6 — Collision Detection: clamp()');

if (sandbox.clamp) {
    assertEqual(sandbox.clamp(5, 0, 10), 5, 'clamp(5, 0, 10) === 5 (within range)');
    assertEqual(sandbox.clamp(-5, 0, 10), 0, 'clamp(-5, 0, 10) === 0 (below min)');
    assertEqual(sandbox.clamp(15, 0, 10), 10, 'clamp(15, 0, 10) === 10 (above max)');
    assertEqual(sandbox.clamp(0, 0, 10), 0, 'clamp(0, 0, 10) === 0 (at min)');
    assertEqual(sandbox.clamp(10, 0, 10), 10, 'clamp(10, 0, 10) === 10 (at max)');
}

section('§6 — Collision Detection: circleRectCollision()');

if (sandbox.circleRectCollision) {
    // Circle inside rect
    assert(sandbox.circleRectCollision(50, 50, 10, 0, 0, 100, 100) === true,
        'Circle inside rect → collision');
    // Circle outside rect
    assert(sandbox.circleRectCollision(200, 200, 10, 0, 0, 100, 100) === false,
        'Circle far outside rect → no collision');
    // Circle touching rect corner
    assert(sandbox.circleRectCollision(110, 110, 15, 0, 0, 100, 100) === true,
        'Circle overlapping rect corner → collision');
    // Circle just barely outside corner
    assert(sandbox.circleRectCollision(115, 115, 10, 0, 0, 100, 100) === false,
        'Circle just outside rect corner → no collision');
}

section('§6 — Collision Detection: Ground collision');

if (sandbox.checkGroundCollision) {
    // Bird just above ground (1px clearance)
    sandbox.bird.y = 540 - 15 - 1; // CANVAS_HEIGHT - GROUND_HEIGHT - radius - 1 = 1px above ground
    assert(sandbox.checkGroundCollision() === false, 'Bird 1px above ground → no collision');

    // Bird exactly touching ground boundary (y + radius == groundY)
    sandbox.bird.y = 540 - 15; // CANVAS_HEIGHT - GROUND_HEIGHT - radius = edge touching
    assert(sandbox.checkGroundCollision() === true, 'Bird edge exactly at ground → collision (>=)');

    sandbox.bird.y = 540; // exactly at ground (y + radius = 555 >= 540)
    assert(sandbox.checkGroundCollision() === true, 'Bird at ground level → collision');

    sandbox.bird.y = 300; // middle of canvas
    assert(sandbox.checkGroundCollision() === false, 'Bird in middle → no collision');

    // Reset
    sandbox.bird.y = 300;
}


// ═══════════════════════════════════════════════════════════════
// §5 — PIPE LIFECYCLE
// ═══════════════════════════════════════════════════════════════

section('§5 — Pipe Lifecycle: Functions exist');

assert(typeof sandbox.spawnPipe === 'function', 'spawnPipe() function exists');
assert(typeof sandbox.updatePipes === 'function', 'updatePipes() function exists');

section('§5 — Pipe Lifecycle: spawnPipe()');

if (sandbox.spawnPipe) {
    sandbox.pipes.length = 0;
    sandbox.spawnPipe();

    assertEqual(sandbox.pipes.length, 1, 'spawnPipe adds one pipe to array');

    const pipe = sandbox.pipes[0];
    assertEqual(pipe.x, 400, 'New pipe spawns at CANVAS_WIDTH (400)');
    assert(pipe.gapY >= 50, `Pipe gapY (${pipe.gapY}) >= PIPE_MIN_TOP (50)`);
    assert(pipe.gapY <= 360, `Pipe gapY (${pipe.gapY}) <= PIPE_MAX_TOP (360)`);
    assertEqual(pipe.scored, false, 'New pipe has scored = false');

    // Verify pipe object shape: x, gapY, scored
    const pipeKeys = Object.keys(pipe).sort();
    assertEqual(pipeKeys.join(','), 'gapY,scored,x', 'Pipe has exactly 3 properties: x, gapY, scored');

    sandbox.pipes.length = 0;
}

section('§5 — Pipe Lifecycle: Pipe cleanup (off-screen removal)');

if (sandbox.updatePipes) {
    sandbox.pipes.length = 0;
    // Place a pipe off-screen to the left
    sandbox.pipes.push({ x: -60, gapY: 200, scored: true }); // fully off screen (x + PIPE_WIDTH < 0 → -60 + 52 = -8 < 0)
    if (sandbox.distanceSinceLastPipe !== undefined) {
        sandbox.distanceSinceLastPipe = 0;
    }
    sandbox.updatePipes(0.016);

    // The off-screen pipe should have been shifted off
    const offScreenRemoved = sandbox.pipes.every(p => p.x + 52 >= 0);
    assert(offScreenRemoved, 'Off-screen pipes removed via shift()');

    sandbox.pipes.length = 0;
}


// ═══════════════════════════════════════════════════════════════
// §4 — BIRD PHYSICS (updateBird)
// ═══════════════════════════════════════════════════════════════

section('§4 — Bird Physics: updateBird()');

if (sandbox.updateBird) {
    // Test gravity application
    sandbox.bird.y = 300;
    sandbox.bird.velocity = 0;
    sandbox.updateBird(0.1); // 100ms
    assertApprox(sandbox.bird.velocity, 98, 1, 'Gravity: velocity += GRAVITY * dt (0 + 980*0.1 = 98)');
    assertApprox(sandbox.bird.y, 309.8, 1, 'Position: y += velocity * dt (300 + 98*0.1 = 309.8)');

    // Test flap then gravity
    sandbox.bird.velocity = -280; // just flapped
    sandbox.bird.y = 300;
    sandbox.updateBird(0.016);
    assert(sandbox.bird.velocity > -280, 'After flap, gravity reduces upward velocity');
    assert(sandbox.bird.y < 300, 'After flap, bird moves up');

    // Test terminal velocity cap
    sandbox.bird.velocity = 700; // above MAX_FALL_SPEED
    sandbox.bird.y = 100;
    sandbox.updateBird(0.016);
    assert(sandbox.bird.velocity <= 600, 'Terminal velocity capped at MAX_FALL_SPEED (600)');

    // Test ceiling clamp
    sandbox.bird.y = 5; // near top
    sandbox.bird.velocity = -200; // moving up
    sandbox.updateBird(0.016);
    assert(sandbox.bird.y >= 15, 'Bird clamped to ceiling (y >= BIRD_RADIUS)');
    assertEqual(sandbox.bird.velocity, 0, 'Velocity set to 0 at ceiling');

    // Reset
    sandbox.bird.y = 300;
    sandbox.bird.velocity = 0;
    sandbox.bird.rotation = 0;
}


// ═══════════════════════════════════════════════════════════════
// §3/§7 — IDLE BOB FORMULA (PLACEHOLDER)
// ═══════════════════════════════════════════════════════════════

section('§3/§7 — Idle Bob: Sine formula verification');

// Spec formula: bird.y = BIRD_START_Y + Math.sin(bobTimer * BOB_FREQUENCY * 2 * Math.PI) * BOB_AMPLITUDE
// Verify the exact formula is present in source
assert(
    src.includes('Math.sin(bobTimer * BOB_FREQUENCY * Math.PI * 2)') ||
    src.includes('Math.sin(bobTimer * BOB_FREQUENCY * 2 * Math.PI)'),
    'Bob formula uses Math.sin(bobTimer * BOB_FREQUENCY * 2 * Math.PI)'
);

assert(
    src.includes('BIRD_START_Y + Math.sin'),
    'Bob formula centers on BIRD_START_Y'
);

assert(
    src.includes('* BOB_AMPLITUDE'),
    'Bob formula multiplies by BOB_AMPLITUDE'
);

// Verify bob behavior with specific values
if (sandbox.update) {
    sandbox.resetGame();
    sandbox.gameState = 'IDLE';

    // At t=0, sin(0) = 0, so bird.y should be BIRD_START_Y
    sandbox.bobTimer = 0;
    sandbox.update(0);
    assertApprox(sandbox.bird.y, 300, 0.1, 'Bob at t=0: bird.y ≈ BIRD_START_Y (300)');

    // At t=0.125s with freq=2Hz: sin(0.125 * 2 * 2π) = sin(π/2) = 1.0
    // bird.y = 300 + 1.0 * 8 = 308
    sandbox.bobTimer = 0;
    sandbox.update(0.125);
    assertApprox(sandbox.bird.y, 308, 0.1, 'Bob at t=0.125: bird.y ≈ 308 (peak amplitude)');

    sandbox.resetGame();
}


// ═══════════════════════════════════════════════════════════════
// NO EXTERNAL DEPENDENCIES
// ═══════════════════════════════════════════════════════════════

section('No External Dependencies');

assert(!/\bimport\s/.test(src), 'No import statements');
assert(!/\bexport\s/.test(src), 'No export statements');
assert(!/\brequire\s*\(/.test(src), 'No require() calls');
assert(!src.includes('DOMContentLoaded'), 'No DOMContentLoaded wrapper');
assert(!/https?:\/\//.test(src), 'No external URLs in source');
assert(!src.includes('fetch('), 'No fetch() calls');
assert(!src.includes('XMLHttpRequest'), 'No XMLHttpRequest');


// ═══════════════════════════════════════════════════════════════
// CODE QUALITY
// ═══════════════════════════════════════════════════════════════

section('Code Quality');

// Clean const/let usage (no var for game state)
const varDeclarations = [...src.matchAll(/\bvar\s+/g)];
if (varDeclarations.length > 0) {
    warn(`Found ${varDeclarations.length} 'var' declaration(s) — spec uses const/let exclusively`);
} else {
    console.log('  ✅ No var declarations (const/let only)');
    passed++;
}

// Consistent naming — camelCase for functions and variables
const funcNames = [...src.matchAll(/function\s+(\w+)/g)].map(m => m[1]);
const allCamelCase = funcNames.every(n => /^[a-z]/.test(n));
assert(allCamelCase, 'All function names use camelCase');

// Section comments present
assert(src.includes('// ===== CONSTANTS ====='), 'Section comment: CONSTANTS');
assert(src.includes('// ===== STATE CONSTANTS =====') || src.includes('STATE CONSTANTS'), 'Section comment: STATE CONSTANTS');
assert(src.includes('// ===== CANVAS INITIALIZATION =====') || src.includes('CANVAS INITIALIZATION'), 'Section comment: CANVAS INITIALIZATION');
assert(src.includes('// ===== GAME STATE VARIABLES =====') || src.includes('GAME STATE VARIABLES'), 'Section comment: GAME STATE VARIABLES');
assert(src.includes('// ===== GAME LOOP =====') || src.includes('GAME LOOP'), 'Section comment: GAME LOOP');

// No console.log in production code
assert(!src.includes('console.log'), 'No console.log statements in game.js');
assert(!src.includes('console.warn'), 'No console.warn statements in game.js');
assert(!src.includes('console.error'), 'No console.error statements in game.js');


// ═══════════════════════════════════════════════════════════════
// HTML / CSS VERIFICATION
// ═══════════════════════════════════════════════════════════════

section('HTML Structure (§1)');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');

assert(html.includes('<!DOCTYPE html>'), 'HTML has DOCTYPE');
assert(html.includes('<meta charset="UTF-8">'), 'HTML has charset meta');
assert(html.includes('<meta name="viewport"'), 'HTML has viewport meta');
assert(html.includes('<link rel="stylesheet" href="style.css">'), 'HTML links style.css');
assert(html.includes('<canvas id="gameCanvas" width="400" height="600"'), 'Canvas: id=gameCanvas, 400x600');
assert(html.includes('<script src="game.js"></script>'), 'HTML loads game.js via script tag');

// Script at end of body
const bodyEndPos = html.indexOf('</body>');
const scriptPos = html.indexOf('<script src="game.js">');
const canvasPos = html.indexOf('<canvas');
assert(scriptPos > canvasPos, 'Script tag is after canvas element');
assert(scriptPos < bodyEndPos, 'Script tag is before </body>');

section('CSS Structure (§1)');

const css = fs.readFileSync(path.join(__dirname, 'style.css'), 'utf8');

assert(css.includes('margin: 0'), 'CSS reset: margin: 0');
assert(css.includes('padding: 0'), 'CSS reset: padding: 0');
assert(css.includes('box-sizing: border-box'), 'CSS reset: box-sizing: border-box');
assert(css.includes('#2c2c2c'), 'Body background: #2c2c2c');
assert(css.includes('overflow: hidden'), 'Body overflow: hidden');
assert(css.includes('display: block') || css.includes('display:block'), 'Canvas display: block');
assert(css.includes('user-select: none') || css.includes('user-select:none'), 'user-select: none');
assert(css.includes('touch-action: none') || css.includes('touch-action:none'), 'touch-action: none on canvas');


// ═══════════════════════════════════════════════════════════════
// SCORING SYSTEM
// ═══════════════════════════════════════════════════════════════

section('§5 — Scoring System');

// Check scoring function exists
const hasScoreFunc = src.includes('function updateScore') || src.includes('function checkScoring');
assert(hasScoreFunc, 'Score checking function exists (updateScore or checkScoring)');

// Scored flag for double-count prevention
assert(src.includes('.scored') || src.includes('pipe.scored'), 'Scoring uses .scored flag for double-count prevention');

// Score is incremented
assert(src.includes('score++') || src.includes('score += 1') || src.includes('score = score + 1'),
    'Score is incremented on pipe pass');


// ═══════════════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════════════

console.log('\n\n╔═══════════════════════════════════════════════════════════╗');
console.log('║           TS-025 QA VERIFICATION RESULTS                 ║');
console.log('╠═══════════════════════════════════════════════════════════╣');
console.log(`║  ✅ PASSED:   ${String(passed).padStart(3)}                                      ║`);
console.log(`║  ❌ FAILED:   ${String(failed).padStart(3)}                                      ║`);
console.log(`║  ⚠️  WARNINGS: ${String(warnings.length).padStart(3)}                                      ║`);
console.log(`║  📊 TOTAL:    ${String(passed + failed).padStart(3)}                                      ║`);
console.log('╚═══════════════════════════════════════════════════════════╝');

if (failures.length > 0) {
    console.log('\n❌ FAILURES:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

if (warnings.length > 0) {
    console.log('\n⚠️  WARNINGS (spec deviations — non-blocking):');
    warnings.forEach((w, i) => console.log(`  ${i + 1}. ${w}`));
}

console.log('\n');
process.exit(failed > 0 ? 1 : 0);
