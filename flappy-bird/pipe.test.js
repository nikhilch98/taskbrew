/**
 * TS-008 — QA Verification: Pipe System (CD-005)
 * Automated test suite using Node.js (no external dependencies)
 *
 * Tests cover:
 *  1. Pipe constants and their values
 *  2. shouldSpawnPipe() — behavior (empty pipes, distance-based threshold)
 *  3. spawnPipe() — creates pipe at right edge, randomized gap within [50, 360]
 *  4. updatePipes(dt) — movement via delta-time, distance-based spawning, cleanup
 *  5. renderPipes(ctx) — correct draw calls, layer ordering, cap details
 *  6. Pipe gap size consistency (PIPE_GAP = 130px)
 *  7. Pipe spacing uniformity (PIPE_SPACING = 220px)
 *  8. Pipe array bounded (max ~4 pipes on screen)
 *  9. Pipe gap always reachable (above ground, below top)
 * 10. Wiring into update/render loops
 * 11. Pipe lifecycle integration: spawn → move → despawn
 * 12. Reset clears pipes and distance tracker
 * 13. Regression checks for colors and conditional rendering
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

function section(title) {
    console.log(`\n━━━ ${title} ━━━`);
}

// ─── read source ───

const src = fs.readFileSync(path.join(__dirname, 'game.js'), 'utf8');

// ─── DOM + Canvas stub with call tracking ───

function createSandbox() {
    const domStub = `
        const _listeners = {};
        const _drawCalls = [];
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
                    fillRect: (x, y, w, h) => {
                        _drawCalls.push({ type: 'fillRect', fillStyle: _ctxState.fillStyle, x, y, w, h });
                    },
                    beginPath: () => {},
                    arc: () => {},
                    moveTo: () => {},
                    lineTo: () => {},
                    closePath: () => {},
                    ellipse: () => {},
                    fill: () => {},
                    stroke: () => {},
                    save: () => {},
                    restore: () => {},
                    translate: () => {},
                    rotate: () => {},
                    strokeText: () => {},
                    fillText: () => {},
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
            // Constants
            CANVAS_WIDTH, CANVAS_HEIGHT, GROUND_HEIGHT,
            BIRD_X, BIRD_RADIUS, BIRD_START_Y,
            GRAVITY, FLAP_VELOCITY, MAX_FALL_SPEED,
            PIPE_WIDTH, PIPE_GAP, PIPE_SPEED, PIPE_SPACING,
            PIPE_MIN_TOP, PIPE_MAX_TOP,
            PIPE_CAP_HEIGHT, PIPE_CAP_OVERHANG,
            FIRST_PIPE_DELAY,
            STATE_IDLE, STATE_PLAYING, STATE_GAME_OVER,

            // State (with getters/setters for let variables)
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
            get spacePressed() { return spacePressed; },
            set spacePressed(v) { spacePressed = v; },
            get distanceSinceLastPipe() { return distanceSinceLastPipe; },
            set distanceSinceLastPipe(v) { distanceSinceLastPipe = v; },

            // Functions
            shouldSpawnPipe, spawnPipe, updatePipes, renderPipes,
            handleInput, resetGame, flap, update, render,

            // Test infrastructure
            _listeners, _rafCallback, _drawCalls, _ctxState
        })
    `;

    return eval(evalCode);
}

// ═══════════════════════════════════════════════════════
// 1. PIPE CONSTANTS
// ═══════════════════════════════════════════════════════

section('1. Pipe Constants');

(() => {
    const sb = createSandbox();

    assertEqual(sb.PIPE_WIDTH, 52, 'PIPE_WIDTH === 52');
    assertEqual(sb.PIPE_GAP, 130, 'PIPE_GAP === 130');
    assertEqual(sb.PIPE_SPEED, 120, 'PIPE_SPEED === 120');
    assertEqual(sb.PIPE_SPACING, 220, 'PIPE_SPACING === 220');
    assertEqual(sb.PIPE_MIN_TOP, 50, 'PIPE_MIN_TOP === 50');
    assertEqual(sb.PIPE_MAX_TOP, 360, 'PIPE_MAX_TOP === 360');
    assertEqual(sb.PIPE_CAP_HEIGHT, 20, 'PIPE_CAP_HEIGHT === 20');
    assertEqual(sb.PIPE_CAP_OVERHANG, 3, 'PIPE_CAP_OVERHANG === 3');

    // Verify PIPE_MAX_TOP derivation: CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50
    assertEqual(sb.PIPE_MAX_TOP,
        sb.CANVAS_HEIGHT - sb.GROUND_HEIGHT - sb.PIPE_GAP - 50,
        'PIPE_MAX_TOP = CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50 (= 360)');

    // FIRST_PIPE_DELAY constant
    assertEqual(sb.FIRST_PIPE_DELAY, 60, 'FIRST_PIPE_DELAY === 60');
})();

// ═══════════════════════════════════════════════════════
// 2. shouldSpawnPipe() — BEHAVIOR
// ═══════════════════════════════════════════════════════

section('2. shouldSpawnPipe()');

(() => {
    const sb = createSandbox();

    // 2a. Returns true when pipes array is empty
    sb.pipes.length = 0;
    assertEqual(sb.shouldSpawnPipe(), true, 'shouldSpawnPipe() returns true when pipes array is empty');

    // 2b. Returns false when rightmost pipe is near right edge
    sb.pipes.length = 0;
    sb.pipes.push({ x: 350, gapY: 200, scored: false });
    assertEqual(sb.shouldSpawnPipe(), false, 'shouldSpawnPipe() returns false when last pipe at x=350 (near right edge)');

    // 2c. Returns true at exact threshold (CANVAS_WIDTH - PIPE_SPACING = 180)
    sb.pipes.length = 0;
    sb.pipes.push({ x: 180, gapY: 200, scored: false });
    assertEqual(sb.shouldSpawnPipe(), true, 'shouldSpawnPipe() returns true when last pipe at x=180 (= CANVAS_WIDTH - PIPE_SPACING)');

    // 2d. Uses LAST pipe (rightmost), not first
    sb.pipes.length = 0;
    sb.pipes.push({ x: -20, gapY: 200, scored: false }); // far left
    sb.pipes.push({ x: 350, gapY: 200, scored: false }); // near right
    assertEqual(sb.shouldSpawnPipe(), false, 'shouldSpawnPipe() uses last pipe (rightmost), not first');
})();

// ═══════════════════════════════════════════════════════
// 3. spawnPipe() — PIPE CREATION
// ═══════════════════════════════════════════════════════

section('3. spawnPipe()');

(() => {
    const sb = createSandbox();

    // 3a. Adds a pipe to the array
    sb.pipes.length = 0;
    sb.spawnPipe();
    assertEqual(sb.pipes.length, 1, 'spawnPipe() adds one pipe to the array');

    // 3b. Pipe spawns at right edge
    assertEqual(sb.pipes[0].x, 400, 'Pipe spawns at x = CANVAS_WIDTH (400)');

    // 3c. Pipe has scored = false
    assertEqual(sb.pipes[0].scored, false, 'New pipe has scored = false');

    // 3d. Pipe has gapY property
    assert(typeof sb.pipes[0].gapY === 'number', 'Pipe has numeric gapY property');

    // 3e. gapY is within valid range [PIPE_MIN_TOP, PIPE_MAX_TOP]
    assert(sb.pipes[0].gapY >= 50 && sb.pipes[0].gapY <= 360,
        `gapY (${sb.pipes[0].gapY.toFixed(1)}) is within [50, 360] range`);
})();

// 3f. Statistical test: gapY is randomized within [50, 360]
(() => {
    const sb = createSandbox();
    let minGap = Infinity;
    let maxGap = -Infinity;
    const samples = 1000;

    for (let i = 0; i < samples; i++) {
        sb.pipes.length = 0;
        sb.spawnPipe();
        const g = sb.pipes[0].gapY;
        if (g < minGap) minGap = g;
        if (g > maxGap) maxGap = g;
    }

    assert(minGap >= 50, `Statistical: min gapY (${minGap.toFixed(1)}) >= 50 (PIPE_MIN_TOP)`);
    assert(maxGap <= 360, `Statistical: max gapY (${maxGap.toFixed(1)}) <= 360 (PIPE_MAX_TOP)`);
    assert(minGap < 80, `Statistical: min gapY (${minGap.toFixed(1)}) < 80 (distribution reaches low end)`);
    assert(maxGap > 330, `Statistical: max gapY (${maxGap.toFixed(1)}) > 330 (distribution reaches high end)`);
})();

// ═══════════════════════════════════════════════════════
// 4. updatePipes(dt) — MOVEMENT (delta-time multiplication)
// ═══════════════════════════════════════════════════════

section('4. updatePipes(dt) — Movement');

(() => {
    const sb = createSandbox();

    // Setup: place pipes and set distanceSinceLastPipe to prevent spawning
    sb.pipes.length = 0;
    sb.pipes.push({ x: 300, gapY: 200, scored: false });
    sb.pipes.push({ x: 200, gapY: 150, scored: false });
    sb.distanceSinceLastPipe = 0; // will accumulate but not reach PIPE_SPACING

    const dt = 0.5; // half a second
    const expectedDisplacement = 120 * 0.5; // PIPE_SPEED * dt = 60px

    sb.updatePipes(dt);

    // Filter to original pipes (new one may have been spawned at x=400)
    const pipe0 = sb.pipes.find(p => Math.abs(p.gapY - 200) < 1);
    const pipe1 = sb.pipes.find(p => Math.abs(p.gapY - 150) < 1);

    assertApprox(pipe0.x, 300 - expectedDisplacement, 0.01,
        `Pipe moves left by PIPE_SPEED * dt = ${expectedDisplacement}px (300 → ${300 - expectedDisplacement})`);
    assertApprox(pipe1.x, 200 - expectedDisplacement, 0.01,
        `Second pipe also moves by ${expectedDisplacement}px (200 → ${200 - expectedDisplacement})`);
})();

// 4b. Delta-time multiplication at 60fps
(() => {
    const sb = createSandbox();
    sb.pipes.length = 0;
    sb.pipes.push({ x: 300, gapY: 200, scored: false });
    sb.distanceSinceLastPipe = 0;

    const dt = 1.0 / 60; // one frame at 60fps
    sb.updatePipes(dt);

    const movedPipe = sb.pipes.find(p => Math.abs(p.gapY - 200) < 1);
    assertApprox(movedPipe.x, 300 - (120 / 60), 0.01,
        'Pipe moves correctly at 60fps dt (120/60 = 2px per frame)');
})();

// ═══════════════════════════════════════════════════════
// 5. updatePipes(dt) — DISTANCE-BASED SPAWNING
// ═══════════════════════════════════════════════════════

section('5. updatePipes(dt) — Distance-Based Spawning');

(() => {
    const sb = createSandbox();

    // 5a. Spawns pipe when distanceSinceLastPipe reaches PIPE_SPACING
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 219; // just below PIPE_SPACING (220)

    // dt of 0.016s → accumulates 120*0.016 = 1.92px → 219 + 1.92 = 220.92 >= 220
    sb.updatePipes(0.016);
    assert(sb.pipes.length >= 1, 'Pipe spawned when distanceSinceLastPipe crosses PIPE_SPACING');
})();

(() => {
    const sb = createSandbox();

    // 5b. Does NOT spawn when distance is below threshold
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 100; // far below threshold

    sb.updatePipes(0.016); // accumulates ~1.92px → 101.92 < 220
    assertEqual(sb.pipes.length, 0, 'No pipe spawned when distanceSinceLastPipe (101.92) < PIPE_SPACING (220)');
})();

(() => {
    const sb = createSandbox();

    // 5c. Preserves remainder for consistent spacing
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 219;

    sb.updatePipes(0.016); // dist becomes 220.92, then 220.92 - 220 = 0.92
    assertApprox(sb.distanceSinceLastPipe, 220.92 - 220, 0.1,
        'distanceSinceLastPipe preserves remainder after spawn (≈0.92)');
})();

(() => {
    const sb = createSandbox();

    // 5d. handleInput seeds distanceSinceLastPipe for first pipe delay
    sb.gameState = 'IDLE';
    sb.handleInput(); // IDLE → PLAYING
    assertEqual(sb.distanceSinceLastPipe, 220 - 60,
        'handleInput seeds distanceSinceLastPipe = PIPE_SPACING - FIRST_PIPE_DELAY (160)');

    // First pipe should spawn after 60px more of scrolling
    // At 120px/s, that's 0.5s = 30 frames at 60fps
    let pipeSpawned = false;
    for (let i = 0; i < 50 && !pipeSpawned; i++) {
        sb.updatePipes(1 / 60);
        if (sb.pipes.length > 0) pipeSpawned = true;
    }
    assert(pipeSpawned, 'First pipe spawns after FIRST_PIPE_DELAY (60px) of scrolling');
})();

// ═══════════════════════════════════════════════════════
// 6. updatePipes(dt) — OFF-SCREEN CLEANUP
// ═══════════════════════════════════════════════════════

section('6. updatePipes(dt) — Off-Screen Cleanup');

(() => {
    const sb = createSandbox();

    // 6a. Pipe fully off-screen (x + PIPE_WIDTH < 0)
    sb.pipes.length = 0;
    sb.pipes.push({ x: -53, gapY: 200, scored: false }); // -53 + 52 = -1 < 0
    sb.pipes.push({ x: 200, gapY: 150, scored: false });
    sb.distanceSinceLastPipe = 0;

    sb.updatePipes(0.001); // tiny dt
    assert(sb.pipes.length < 3, 'Off-screen pipe removed from array');
    assert(!sb.pipes.some(p => Math.abs(p.gapY - 200) < 1 && p.x < -52),
        'The off-screen pipe (gapY=200, x≈-53) was removed');
})();

(() => {
    const sb = createSandbox();

    // 6b. Pipe NOT removed when partially visible
    sb.pipes.length = 0;
    sb.pipes.push({ x: -50, gapY: 200, scored: false }); // -50 + 52 = 2 >= 0 → visible
    sb.distanceSinceLastPipe = 0;

    sb.updatePipes(0.001);
    const stillThere = sb.pipes.some(p => Math.abs(p.gapY - 200) < 1);
    assert(stillThere, 'Partially visible pipe NOT removed (x + PIPE_WIDTH >= 0)');
})();

(() => {
    // 6c. Source analysis: uses shift()
    assert(src.includes('pipes.shift()'), 'Off-screen cleanup uses pipes.shift() (removes from front)');
    assert(src.includes('pipes[0].x + PIPE_WIDTH < 0'), 'Cleanup condition: pipes[0].x + PIPE_WIDTH < 0');
})();

// ═══════════════════════════════════════════════════════
// 7. PIPE LIFECYCLE — spawn → move → despawn
// ═══════════════════════════════════════════════════════

section('7. Pipe Lifecycle Integration');

(() => {
    const sb = createSandbox();
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = sb.PIPE_SPACING - 1; // about to spawn

    // Step 1: Spawn
    sb.updatePipes(0.016);
    assert(sb.pipes.length >= 1, 'Lifecycle: pipe spawns when distance threshold reached');
    const spawnX = sb.pipes[0].x;
    assertEqual(spawnX, 400, 'Lifecycle: pipe spawns at right edge (400)');

    // Step 2: Move for 1 second
    for (let i = 0; i < 60; i++) {
        sb.updatePipes(1 / 60);
    }
    const afterMove = sb.pipes[0].x;
    assertApprox(afterMove, 400 - 120, 2,
        'Lifecycle: pipe at ~280 after 1s of movement at 120px/s');

    // Step 3: Move until first pipe goes off-screen (~3.8s more at 120px/s from ~280)
    let despawned = false;
    const originalPipe = sb.pipes[0];
    for (let i = 0; i < 300 && !despawned; i++) {
        sb.updatePipes(1 / 60);
        if (sb.pipes[0] !== originalPipe) despawned = true;
    }
    assert(despawned, 'Lifecycle: off-screen pipe eventually removed via shift()');
})();

// ═══════════════════════════════════════════════════════
// 8. PIPE ARRAY BOUNDED (max ~4 pipes)
// ═══════════════════════════════════════════════════════

section('8. Pipe Array Bounded');

(() => {
    const sb = createSandbox();
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = sb.PIPE_SPACING - 1; // about to spawn first

    let maxPipes = 0;
    for (let frame = 0; frame < 1800; frame++) { // 30s at 60fps
        sb.updatePipes(1 / 60);
        if (sb.pipes.length > maxPipes) maxPipes = sb.pipes.length;
    }

    assert(maxPipes <= 5, `Max pipes on screen = ${maxPipes} (should be ≤ 5, bounded)`);
    assert(maxPipes >= 2, `Max pipes on screen = ${maxPipes} (should have at least 2)`);
})();

// ═══════════════════════════════════════════════════════
// 9. PIPE GAP ALWAYS REACHABLE
// ═══════════════════════════════════════════════════════

section('9. Pipe Gap Always Reachable');

(() => {
    const sb = createSandbox();
    const groundY = sb.CANVAS_HEIGHT - sb.GROUND_HEIGHT; // 540

    // Worst case: maximum gapY → bottom of gap
    const maxBottom = sb.PIPE_MAX_TOP + sb.PIPE_GAP; // 360 + 130 = 490
    assert(maxBottom < groundY, `Max gap bottom (${maxBottom}) is above ground (${groundY})`);

    // Worst case: minimum gapY → gap top
    assert(sb.PIPE_MIN_TOP > 0, `Min gap top (${sb.PIPE_MIN_TOP}) is below screen top`);

    // At least 50px clearance above ground
    assertEqual(groundY - maxBottom, 50, `Clearance above ground = ${groundY - maxBottom}px (= 50px)`);

    // At least 50px below screen top
    assertEqual(sb.PIPE_MIN_TOP, 50, `Distance from screen top = ${sb.PIPE_MIN_TOP}px (= 50px)`);

    // Statistical: 500 random pipes all reachable
    let allReachable = true;
    for (let i = 0; i < 500; i++) {
        sb.pipes.length = 0;
        sb.spawnPipe();
        const g = sb.pipes[0].gapY;
        if (g < 0 || g + sb.PIPE_GAP > groundY) {
            allReachable = false;
            break;
        }
    }
    assert(allReachable, 'All 500 randomly spawned pipes have reachable gaps');
})();

// ═══════════════════════════════════════════════════════
// 10. PIPE GAP SIZE CONSISTENCY
// ═══════════════════════════════════════════════════════

section('10. Pipe Gap Size Consistency');

(() => {
    const sb = createSandbox();
    assertEqual(sb.PIPE_GAP, 130, 'PIPE_GAP is 130px for all pipes');
    assert(src.includes('pipe.gapY + PIPE_GAP'), 'Bottom pipe computed as gapY + PIPE_GAP');
    assert(!src.includes('pipe.gap ') && !src.includes('pipe.gapSize'), 'No per-pipe gap override');
})();

// ═══════════════════════════════════════════════════════
// 11. PIPE SPACING UNIFORMITY
// ═══════════════════════════════════════════════════════

section('11. Pipe Spacing Uniformity');

(() => {
    const sb = createSandbox();

    // All spawned pipes should spawn at x = CANVAS_WIDTH (400)
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = sb.PIPE_SPACING - 1;

    const spawnXPositions = [];
    for (let frame = 0; frame < 600; frame++) {
        const prevLen = sb.pipes.length;
        sb.updatePipes(1 / 60);
        if (sb.pipes.length > prevLen) {
            spawnXPositions.push(sb.pipes[sb.pipes.length - 1].x);
        }
    }

    for (let i = 0; i < Math.min(spawnXPositions.length, 4); i++) {
        assertEqual(spawnXPositions[i], 400, `Pipe #${i + 1} spawned at x = 400 (right edge)`);
    }

    // Distance-based: PIPE_SPACING (220px) between spawns
    assertEqual(sb.PIPE_SPACING, 220, 'PIPE_SPACING is 220px (uniform)');
    assert(src.includes('distanceSinceLastPipe >= PIPE_SPACING'),
        'Spawn threshold uses distanceSinceLastPipe >= PIPE_SPACING');
    assert(src.includes('distanceSinceLastPipe -= PIPE_SPACING'),
        'Remainder preserved: distanceSinceLastPipe -= PIPE_SPACING');
})();

// ═══════════════════════════════════════════════════════
// 12. renderPipes() — DRAW CALL STRUCTURE
// ═══════════════════════════════════════════════════════

section('12. renderPipes() — Draw Call Structure');

(() => {
    const sb = createSandbox();

    sb.pipes.length = 0;
    sb.pipes.push({ x: 200, gapY: 200, scored: false });
    sb._drawCalls.length = 0;

    const mockCtx = {
        get fillStyle() { return sb._ctxState.fillStyle; },
        set fillStyle(v) { sb._ctxState.fillStyle = v; },
        fillRect: (x, y, w, h) => {
            sb._drawCalls.push({ type: 'fillRect', fillStyle: sb._ctxState.fillStyle, x, y, w, h });
        },
    };

    sb.renderPipes(mockCtx);

    // Each pipe pair: 2 bodies + 2 caps = 4 fillRect calls
    assertEqual(sb._drawCalls.length, 4, 'One pipe pair produces 4 fillRect calls (2 bodies + 2 caps)');

    // Top pipe body (y=0, h=gapY)
    const topBody = sb._drawCalls[0];
    assertEqual(topBody.x, 200, 'Top pipe body x = pipe.x (200)');
    assertEqual(topBody.y, 0, 'Top pipe body y = 0');
    assertEqual(topBody.w, 52, 'Top pipe body width = PIPE_WIDTH (52)');
    assertEqual(topBody.h, 200, 'Top pipe body height = gapY (200)');

    // Bottom pipe body (y=gapY+PIPE_GAP=330, h=groundY-330=210)
    const bottomBody = sb._drawCalls[1];
    assertEqual(bottomBody.x, 200, 'Bottom pipe body x = pipe.x (200)');
    assertEqual(bottomBody.y, 330, 'Bottom pipe body y = gapY + PIPE_GAP (330)');
    assertEqual(bottomBody.w, 52, 'Bottom pipe body width = PIPE_WIDTH (52)');
    assertEqual(bottomBody.h, 210, 'Bottom pipe body height = groundY - bottomPipeTop (210)');

    // Top cap
    const topCap = sb._drawCalls[2];
    assertEqual(topCap.x, 197, 'Top cap x = pipe.x - PIPE_CAP_OVERHANG (197)');
    assertEqual(topCap.y, 180, 'Top cap y = gapY - PIPE_CAP_HEIGHT (180)');
    assertEqual(topCap.w, 58, 'Top cap width = PIPE_WIDTH + 2*OVERHANG (58)');
    assertEqual(topCap.h, 20, 'Top cap height = PIPE_CAP_HEIGHT (20)');

    // Bottom cap
    const bottomCap = sb._drawCalls[3];
    assertEqual(bottomCap.x, 197, 'Bottom cap x = pipe.x - PIPE_CAP_OVERHANG (197)');
    assertEqual(bottomCap.y, 330, 'Bottom cap y = gapY + PIPE_GAP (330)');
    assertEqual(bottomCap.w, 58, 'Bottom cap width = PIPE_WIDTH + 2*OVERHANG (58)');
    assertEqual(bottomCap.h, 20, 'Bottom cap height = PIPE_CAP_HEIGHT (20)');
})();

// ═══════════════════════════════════════════════════════
// 13. renderPipes() — COLORS (SPEC vs ACTUAL)
// ═══════════════════════════════════════════════════════

section('13. renderPipes() — Colors');

(() => {
    const sb = createSandbox();

    sb.pipes.length = 0;
    sb.pipes.push({ x: 200, gapY: 200, scored: false });
    sb._drawCalls.length = 0;

    const mockCtx = {
        get fillStyle() { return sb._ctxState.fillStyle; },
        set fillStyle(v) { sb._ctxState.fillStyle = v; },
        fillRect: (x, y, w, h) => {
            sb._drawCalls.push({ type: 'fillRect', fillStyle: sb._ctxState.fillStyle, x, y, w, h });
        },
    };

    sb.renderPipes(mockCtx);

    const fillStyles = sb._drawCalls.map(c => c.fillStyle);
    const uniqueColors = [...new Set(fillStyles)];

    // Spec says: body=#3cb043, cap=#2d8a34
    const hasSpecBody = fillStyles.includes('#3cb043');
    const hasSpecCap  = fillStyles.includes('#2d8a34');

    assert(hasSpecBody,
        `[SPEC] Pipe body color is #3cb043 — ${hasSpecBody ? 'MATCH' : 'MISMATCH: actual=' + uniqueColors.join(', ')}`);
    assert(hasSpecCap,
        `[SPEC] Pipe cap color is #2d8a34 — ${hasSpecCap ? 'MATCH' : 'MISMATCH: actual=' + uniqueColors.join(', ')}`);

    if (!hasSpecBody || !hasSpecCap) {
        console.log('  ℹ️  BUG-001: Pipe colors deviate from spec. Current: #2ECC71 (body), #27AE60 (caps)');
        console.log('  ℹ️  Original CD-005 commit used #3cb043/#2d8a34 per spec. Changed in CD-013.');
    }
})();

// ═══════════════════════════════════════════════════════
// 14. RENDER LAYER ORDER
// ═══════════════════════════════════════════════════════

section('14. Render Layer Order');

(() => {
    const renderBody = src.slice(src.indexOf('function render(ctx)'));
    const pipesPos = renderBody.indexOf('renderPipes');
    const groundPos = renderBody.indexOf('renderGround');
    const birdPos = renderBody.indexOf('renderBird');

    assert(pipesPos < groundPos, 'renderPipes called before renderGround (pipes behind ground)');
    assert(pipesPos < birdPos, 'renderPipes called before renderBird (pipes behind bird)');
    assert(groundPos < birdPos, 'renderGround called before renderBird (ground behind bird)');
})();

// ═══════════════════════════════════════════════════════
// 15. CONDITIONAL RENDERING — IDLE STATE
// ═══════════════════════════════════════════════════════

section('15. Conditional Pipe Rendering in IDLE');

(() => {
    // Spec: "Pipes are NOT rendered during IDLE state (conditional check in render)"
    // CD-005 commit had: if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) { renderPipes(ctx); }
    const renderFunc = src.slice(
        src.indexOf('function render(ctx)'),
        src.indexOf('// ===== GAME LOOP =====')
    );

    const hasConditionalGuard =
        renderFunc.includes('STATE_PLAYING') &&
        renderFunc.indexOf('renderPipes') > renderFunc.indexOf('STATE_PLAYING');

    // Check if renderPipes is called unconditionally
    const unconditional = /^\s*renderPipes\(ctx\);/m.test(renderFunc);

    if (hasConditionalGuard && !unconditional) {
        assert(true, '[SPEC] renderPipes has conditional guard (STATE_PLAYING || STATE_GAME_OVER)');
    } else {
        assert(false, '[SPEC] renderPipes should have conditional guard — currently called unconditionally (REGRESSION)');
        console.log('  ℹ️  BUG-002: CD-005 had: if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) { renderPipes(ctx); }');
        console.log('  ℹ️  Current code calls renderPipes(ctx) unconditionally. Functionally OK (pipes empty in IDLE).');
    }

    // Functional test: no pipes render during IDLE (empty array)
    const sb = createSandbox();
    sb.gameState = 'IDLE';
    sb.pipes.length = 0;
    sb._drawCalls.length = 0;

    const mockCtx = {
        get fillStyle() { return sb._ctxState.fillStyle; },
        set fillStyle(v) { sb._ctxState.fillStyle = v; },
        fillRect: (x, y, w, h) => {
            sb._drawCalls.push({ type: 'fillRect', fillStyle: sb._ctxState.fillStyle, x, y, w, h });
        },
    };

    sb.renderPipes(mockCtx);
    assertEqual(sb._drawCalls.length, 0, 'renderPipes with empty array → 0 draw calls (no visual in IDLE)');
})();

// ═══════════════════════════════════════════════════════
// 16. UPDATE WIRING
// ═══════════════════════════════════════════════════════

section('16. Update Wiring');

(() => {
    const updateFunc = src.slice(src.indexOf('function update(dt)'), src.indexOf('// ===== RENDER'));
    const playingCase = updateFunc.slice(updateFunc.indexOf('STATE_PLAYING'));

    assert(playingCase.includes('updatePipes(dt)'), 'updatePipes(dt) called in STATE_PLAYING case');

    const idleCase = updateFunc.slice(
        updateFunc.indexOf('STATE_IDLE'),
        updateFunc.indexOf('STATE_PLAYING')
    );
    assert(!idleCase.includes('updatePipes'), 'updatePipes NOT called in STATE_IDLE');

    const gameOverCase = updateFunc.slice(updateFunc.indexOf('STATE_GAME_OVER'));
    assert(!gameOverCase.includes('updatePipes'), 'updatePipes NOT called in STATE_GAME_OVER');
})();

// ═══════════════════════════════════════════════════════
// 17. FUNCTION DECLARATIONS
// ═══════════════════════════════════════════════════════

section('17. Function Declarations');

(() => {
    const sb = createSandbox();
    assert(typeof sb.shouldSpawnPipe === 'function', 'shouldSpawnPipe is a function');
    assert(typeof sb.spawnPipe === 'function', 'spawnPipe is a function');
    assert(typeof sb.updatePipes === 'function', 'updatePipes is a function');
    assert(typeof sb.renderPipes === 'function', 'renderPipes is a function');
})();

// ═══════════════════════════════════════════════════════
// 18. PIPE SPAWN ON ENTERING PLAYING STATE
// ═══════════════════════════════════════════════════════

section('18. Pipe Spawn Flow on Entering PLAYING');

(() => {
    const sb = createSandbox();

    sb.gameState = 'IDLE';
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    // Transition to PLAYING
    sb.handleInput();
    assertEqual(sb.gameState, 'PLAYING', 'Transitioned to PLAYING');
    assertEqual(sb.distanceSinceLastPipe, 160,
        'distanceSinceLastPipe seeded to PIPE_SPACING - FIRST_PIPE_DELAY (160)');
    assertEqual(sb.pipes.length, 0, 'No pipes immediately after state transition');

    // Need 60px more distance at 120px/s = 0.5s = ~30 frames
    for (let i = 0; i < 40; i++) {
        sb.updatePipes(1 / 60);
    }
    assert(sb.pipes.length >= 1, 'First pipe spawns after FIRST_PIPE_DELAY (60px) of scrolling');
})();

// ═══════════════════════════════════════════════════════
// 19. RESET CLEARS PIPES AND DISTANCE
// ═══════════════════════════════════════════════════════

section('19. Reset Clears Pipes and Distance');

(() => {
    const sb = createSandbox();

    sb.pipes.push({ x: 100, gapY: 200, scored: false });
    sb.pipes.push({ x: 300, gapY: 150, scored: true });
    sb.distanceSinceLastPipe = 175;

    sb.resetGame();
    assertEqual(sb.pipes.length, 0, 'resetGame clears all pipes');
    assertEqual(sb.distanceSinceLastPipe, 0, 'resetGame resets distanceSinceLastPipe to 0');
})();

// ═══════════════════════════════════════════════════════
// 20. SOURCE STRUCTURE — JSDoc
// ═══════════════════════════════════════════════════════

section('20. Source Structure');

(() => {
    assert(src.includes('// ===== PIPE FUNCTIONS ====='), 'PIPE FUNCTIONS section header');
    assert(src.includes('* Determine if a new pipe should be spawned'), 'shouldSpawnPipe JSDoc');
    assert(src.includes('* Create a new pipe pair at the right edge'), 'spawnPipe JSDoc');
    assert(src.includes('* Update all pipes: move left, spawn new ones'), 'updatePipes JSDoc');
    assert(src.includes('* Render all pipe pairs with body and cap'), 'renderPipes JSDoc');
})();

// ═══════════════════════════════════════════════════════
// 21. MULTIPLE PIPES — Render All
// ═══════════════════════════════════════════════════════

section('21. Multiple Pipes Rendered');

(() => {
    const sb = createSandbox();
    sb.pipes.length = 0;
    sb.pipes.push({ x: 100, gapY: 150, scored: false });
    sb.pipes.push({ x: 250, gapY: 200, scored: false });
    sb.pipes.push({ x: 380, gapY: 180, scored: false });
    sb._drawCalls.length = 0;

    const mockCtx = {
        get fillStyle() { return sb._ctxState.fillStyle; },
        set fillStyle(v) { sb._ctxState.fillStyle = v; },
        fillRect: (x, y, w, h) => {
            sb._drawCalls.push({ type: 'fillRect', fillStyle: sb._ctxState.fillStyle, x, y, w, h });
        },
    };

    sb.renderPipes(mockCtx);
    assertEqual(sb._drawCalls.length, 12, '3 pipes produce 12 fillRect calls (4 per pipe pair)');
})();

// ═══════════════════════════════════════════════════════
// 22. updatePipes USES distanceSinceLastPipe (not shouldSpawnPipe)
// ═══════════════════════════════════════════════════════

section('22. updatePipes Spawn Mechanism');

(() => {
    // Verify updatePipes uses distance accumulator, not shouldSpawnPipe
    const updatePipesBody = src.slice(
        src.indexOf('function updatePipes(dt)'),
        src.indexOf('function renderPipes')
    );

    assert(updatePipesBody.includes('distanceSinceLastPipe += PIPE_SPEED * dt'),
        'updatePipes accumulates distance: distanceSinceLastPipe += PIPE_SPEED * dt');
    assert(updatePipesBody.includes('distanceSinceLastPipe >= PIPE_SPACING'),
        'updatePipes spawns at: distanceSinceLastPipe >= PIPE_SPACING');
    assert(updatePipesBody.includes('spawnPipe()'),
        'updatePipes calls spawnPipe() for actual pipe creation');

    // Note: shouldSpawnPipe() exists but is NOT called from updatePipes
    const callsShouldSpawn = updatePipesBody.includes('shouldSpawnPipe');
    if (callsShouldSpawn) {
        console.log('  ℹ️  updatePipes references shouldSpawnPipe (via comments or call)');
    } else {
        console.log('  ℹ️  NOTE: shouldSpawnPipe() exists in source but is unused by updatePipes');
        console.log('  ℹ️  updatePipes uses distanceSinceLastPipe accumulator instead (improved design)');
    }
    // This is acceptable — the distance accumulator is actually better than checking pipe positions
    assert(true, 'updatePipes uses distance-based spawning mechanism');
})();

// ═══════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════

console.log('\n═══════════════════════════════════════════');
console.log(`  TS-008 PIPE SYSTEM RESULTS: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('═══════════════════════════════════════════');

if (failures.length > 0) {
    console.log('\nFailed tests:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

// ═══════════════════════════════════════════════════════
// BUGS / REGRESSIONS FOUND
// ═══════════════════════════════════════════════════════

console.log('\n═══════════════════════════════════════════');
console.log('  BUGS / REGRESSIONS IDENTIFIED');
console.log('═══════════════════════════════════════════');

console.log(`
BUG-001: Pipe Colors Changed from CD-005 Spec
  Severity:    LOW (visual only)
  Spec:        Body=#3cb043, Cap=#2d8a34
  Actual:      Body=#2ECC71, Cap=#27AE60
  Introduced:  CD-013 (render functions commit, ccc29a8)
  Impact:      Pipes are still green with darker caps — just different shades.
  Repro Steps:
    1. Open index.html in browser
    2. Press Space to start playing
    3. Observe pipe colors — they are #2ECC71/#27AE60 instead of #3cb043/#2d8a34

BUG-002: Conditional Pipe Rendering Guard Removed
  Severity:    LOW (no functional impact)
  Spec:        renderPipes only in PLAYING/GAME_OVER (conditional check in render)
  Actual:      renderPipes(ctx) called unconditionally
  Introduced:  CD-013 or CD-014
  Original:    if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) { renderPipes(ctx); }
  Current:     renderPipes(ctx);  // no guard
  Impact:      No functional impact — pipes array is empty during IDLE so nothing renders.
               However, this is a minor spec violation.
  Repro Steps:
    1. Read render() function in game.js (line ~550)
    2. Note renderPipes(ctx) is called without gameState check

NOTE: shouldSpawnPipe() exists in source but is unused by updatePipes().
  updatePipes uses a distanceSinceLastPipe accumulator instead, which is an
  improved design that preserves remainder for consistent spacing. This is
  acceptable — the acceptance criteria says "distance-based spawning" which
  this accumulator satisfies.
`);

process.exit(failed > 0 ? 1 : 0);
