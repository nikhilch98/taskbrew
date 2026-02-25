/**
 * TS-030 — QA Verification: BUG-002 Fix — No Score on Death Frame (CD-020)
 * Automated test suite using Node.js (no external dependencies)
 *
 * Tests cover all 5 acceptance scenarios:
 *  1. Bug repro: Collision + scoring coincide in same frame → score stays 0
 *  2. Normal scoring still works: Bird safely passes pipe → score increments
 *  3. Ground collision + nearby pipe: Score does NOT increment on death frame
 *  4. Multiple pipes: Bird passes several, then collides → only prior-frame pipes count
 *  5. Ground scrolling stops on death: Early exit also skips ground scroll update
 *
 * Additionally tests:
 *  6. Source-level guard verification: The early-exit line exists in game.js
 *  7. Execution order verification: checkCollisions() before updateScore()
 *  8. Edge cases: Simultaneous scoring threshold + ground/pipe collision
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
        const msg = message;
        console.log(`  ❌ ${msg}`);
        failures.push(msg);
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

// ─── read source once ───

const src = fs.readFileSync(path.join(__dirname, 'game.js'), 'utf8');

// ─── DOM/Canvas stub ───

function createSandbox() {
    const domStub = `
        const _listeners = {};
        const _renderCalls = [];
        const _ctxStub = {
            fillStyle: '',
            strokeStyle: '',
            lineWidth: 0,
            font: '',
            textAlign: '',
            fillRect: () => {},
            strokeRect: () => {},
            clearRect: () => {},
            beginPath: () => {},
            closePath: () => {},
            arc: () => {},
            moveTo: () => {},
            lineTo: () => {},
            fill: () => {},
            stroke: () => {},
            save: () => {},
            restore: () => {},
            translate: () => {},
            rotate: () => {},
            fillText: function(text) { _renderCalls.push({ fn: 'fillText', args: [text] }); },
            strokeText: function(text) { _renderCalls.push({ fn: 'strokeText', args: [text] }); },
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
            BOB_AMPLITUDE, BOB_FREQUENCY,
            FIRST_PIPE_DELAY,
            PIPE_CAP_HEIGHT, PIPE_CAP_OVERHANG,
            STATE_IDLE, STATE_PLAYING, STATE_GAME_OVER,

            // Mutable state via getters/setters
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

            // Functions
            handleInput, resetGame, flap,
            updateBird, updatePipes, updateScore,
            clamp,
            circleRectCollision,
            checkGroundCollision, checkPipeCollisions, checkCollisions,
            update, render, renderScore, gameLoop,
            shouldSpawnPipe, spawnPipe,

            // Test hooks
            _listeners, _rafCallback, _renderCalls, _ctxStub
        })
    `;

    return eval(evalCode);
}

console.log('╔═══════════════════════════════════════════════════════════╗');
console.log('║  TS-030: QA Verification — BUG-002 Fix (CD-020)         ║');
console.log('║  No Score on Death Frame                                 ║');
console.log('╚═══════════════════════════════════════════════════════════╝');


// ═══════════════════════════════════════════════════════
// SCENARIO 1: Bug Repro — Collision + Scoring Same Frame
// ═══════════════════════════════════════════════════════

section('1. Bug Repro — Pipe Collision + Scoring Coincide in Same Frame');

// Test 1a: Original bug repro from task description
(() => {
    const sb = createSandbox();

    // Setup: bird hitting a pipe that it's also just passing (collision + scoring threshold)
    sb.gameState = 'PLAYING';
    sb.bird.y = 180;     // hits top pipe (gapY=200, top pipe goes from 0..200, bird at 180 with radius 15 → collision)
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 40, gapY: 200, scored: false });
    // Pipe center = x + PIPE_WIDTH/2 = 40 + 26 = 66 < BIRD_X(100) → scoreable
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '1a: Bird dies on pipe collision');
    assertEqual(sb.score, 0, '1a: Dead bird did NOT score on death frame (score === 0)');
})();

// Test 1b: Bird exactly at collision boundary AND scoring threshold
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    // Place bird so its edge just barely touches the top pipe
    // Top pipe extends from y=0 to y=gapY. Bird center at gapY - radius + 1 → barely colliding
    const gapY = 200;
    sb.bird.y = gapY - sb.BIRD_RADIUS + 1;  // 200 - 15 + 1 = 186 → circle at 186, bottom at 201 → collides with top pipe ending at 200
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    // Place pipe so its center has just been passed
    sb.pipes.push({ x: 40, gapY: gapY, scored: false });
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '1b: Boundary collision triggers GAME_OVER');
    assertEqual(sb.score, 0, '1b: Score stays 0 at collision boundary');
})();

// Test 1c: Bird collides with BOTTOM pipe while at scoring position
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    // Bottom pipe starts at gapY + PIPE_GAP
    const gapY = 200;
    const bottomPipeY = gapY + sb.PIPE_GAP; // 200 + 130 = 330
    sb.bird.y = bottomPipeY + sb.BIRD_RADIUS - 1;  // barely touching bottom pipe
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 40, gapY: gapY, scored: false }); // center=66 < 100, scoreable
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '1c: Bottom pipe collision triggers GAME_OVER');
    assertEqual(sb.score, 0, '1c: No score when hitting bottom pipe at scoring position');
})();


// ═══════════════════════════════════════════════════════
// SCENARIO 2: Normal Scoring Still Works
// ═══════════════════════════════════════════════════════

section('2. Normal Scoring — Bird Safely Passes Pipe Gap');

// Test 2a: Bird safely in gap, pipe center passes bird.x → score increments
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    // Bird safely in the gap center
    const gapY = 200;
    sb.bird.y = gapY + sb.PIPE_GAP / 2;  // 200 + 65 = 265, center of gap
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    // Pipe center already past bird: x + PIPE_WIDTH/2 = 40 + 26 = 66 < 100
    sb.pipes.push({ x: 40, gapY: gapY, scored: false });
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'PLAYING', '2a: Bird stays PLAYING when safely in gap');
    assertEqual(sb.score, 1, '2a: Score increments to 1 after passing pipe');
})();

// Test 2b: Pipe center hasn't passed bird yet → no score
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    const gapY = 200;
    sb.bird.y = gapY + sb.PIPE_GAP / 2;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    // Pipe center at x + 26 = 226 > bird.x(100) → not yet scoreable
    sb.pipes.push({ x: 200, gapY: gapY, scored: false });
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'PLAYING', '2b: Bird stays PLAYING');
    assertEqual(sb.score, 0, '2b: Score stays 0 when pipe center hasn\'t passed bird');
})();

// Test 2c: Already-scored pipe does not double-count
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    const gapY = 250;
    sb.bird.y = gapY + sb.PIPE_GAP / 2;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 40, gapY: gapY, scored: true }); // Already scored
    sb.score = 3;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'PLAYING', '2c: Bird stays PLAYING');
    assertEqual(sb.score, 3, '2c: Already-scored pipe does not double-count');
})();

// Test 2d: Multiple consecutive safe pipe passes
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    const gapY = 250;
    sb.bird.y = gapY + sb.PIPE_GAP / 2;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    // Three pipes all past bird, all unscored
    sb.pipes.push({ x: 10, gapY: gapY, scored: false });  // center=36 < 100 ✓
    sb.pipes.push({ x: -20, gapY: gapY, scored: false }); // center=6 < 100 ✓
    sb.pipes.push({ x: 40, gapY: gapY, scored: false });  // center=66 < 100 ✓
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'PLAYING', '2d: Bird stays PLAYING with safe passes');
    assertEqual(sb.score, 3, '2d: All 3 unscored passed pipes increment score');
})();


// ═══════════════════════════════════════════════════════
// SCENARIO 3: Ground Collision + Nearby Pipe
// ═══════════════════════════════════════════════════════

section('3. Ground Collision + Nearby Scoreable Pipe');

// Test 3a: Bird hits ground near a pipe that was just passed — no score increment
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    // Place bird at ground level: y + radius >= CANVAS_HEIGHT - GROUND_HEIGHT → collision
    // CANVAS_HEIGHT=600, GROUND_HEIGHT=60 → ground top at 540
    sb.bird.y = 540 - sb.BIRD_RADIUS; // exactly at ground
    sb.bird.velocity = 100; // falling, so after updateBird it moves further down
    sb.pipes.length = 0;
    // Pipe is past bird and unscored → would score if not for death
    sb.pipes.push({ x: 30, gapY: 200, scored: false }); // center=56 < 100
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '3a: Ground collision triggers GAME_OVER');
    assertEqual(sb.score, 0, '3a: No score increment on ground-death frame');
})();

// Test 3b: Bird hits ground with multiple passed pipes — no scoring
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 540 - sb.BIRD_RADIUS;
    sb.bird.velocity = 200; // Falling fast
    sb.pipes.length = 0;
    sb.pipes.push({ x: 10, gapY: 200, scored: false });  // center=36 < 100
    sb.pipes.push({ x: -30, gapY: 250, scored: false }); // center=-4 < 100
    sb.score = 5;  // Already had some score
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '3b: Ground collision GAME_OVER with multiple pipes');
    assertEqual(sb.score, 5, '3b: Score unchanged (was 5, still 5) on ground-death');
})();

// Test 3c: Bird hits ground, but nearest pipe is ahead (not yet passed)
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 540 - sb.BIRD_RADIUS;
    sb.bird.velocity = 100;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 200, gapY: 200, scored: false }); // center=226 > 100, not passed
    sb.score = 2;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '3c: Ground collision regardless of pipe position');
    assertEqual(sb.score, 2, '3c: Score unchanged when pipe is still ahead');
})();


// ═══════════════════════════════════════════════════════
// SCENARIO 4: Multiple Pipes — Only Pre-Death Pipes Count
// ═══════════════════════════════════════════════════════

section('4. Multiple Pipes — Bird Passes Several, Then Collides');

// Test 4a: Bird scored 3 pipes in prior frames, then collides on 4th — score stays at 3
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    // 3 pipes already scored in prior frames
    sb.pipes.length = 0;
    sb.pipes.push({ x: -100, gapY: 250, scored: true });  // Already scored
    sb.pipes.push({ x: -50, gapY: 250, scored: true });   // Already scored
    sb.pipes.push({ x: 10, gapY: 250, scored: true });    // Already scored

    // 4th pipe: bird is colliding with it AND at scoring position
    sb.pipes.push({ x: 60, gapY: 200, scored: false });   // center=86 < 100, scoreable
    sb.bird.y = 180; // collides with top pipe at gapY=200
    sb.bird.velocity = 0;
    sb.score = 3;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '4a: Collision on 4th pipe → GAME_OVER');
    assertEqual(sb.score, 3, '4a: Only the 3 prior-frame pipes counted (score stays 3)');
})();

// Test 4b: Bird scored 2 in prior frames, 2 more scoreable + collision → only 2 count
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.pipes.length = 0;
    sb.pipes.push({ x: -100, gapY: 250, scored: true });  // Already scored
    sb.pipes.push({ x: -50, gapY: 250, scored: true });   // Already scored
    // These would score if bird were alive:
    sb.pipes.push({ x: 20, gapY: 250, scored: false });   // center=46 < 100
    sb.pipes.push({ x: 50, gapY: 200, scored: false });   // center=76 < 100, AND colliding

    sb.bird.y = 180; // collides with pipe at gapY=200
    sb.bird.velocity = 0;
    sb.score = 2;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '4b: Pipe collision triggers GAME_OVER');
    assertEqual(sb.score, 2, '4b: Score stays at 2, new scoreable pipes NOT counted');
})();

// Test 4c: Many pipes scenario — stress test with 10 pipes
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.pipes.length = 0;
    // 7 already-scored pipes (prior frames)
    for (let i = 0; i < 7; i++) {
        sb.pipes.push({ x: -200 + i * 30, gapY: 250, scored: true });
    }
    // 2 more unscored pipes that have been passed
    sb.pipes.push({ x: 20, gapY: 250, scored: false });  // center=46 < 100
    sb.pipes.push({ x: 40, gapY: 250, scored: false });  // center=66 < 100

    // The colliding pipe
    sb.pipes.push({ x: 70, gapY: 200, scored: false }); // center=96 < 100, AND collision

    sb.bird.y = 180; // collides with pipe at gapY=200
    sb.bird.velocity = 0;
    sb.score = 7;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '4c: Collision in 10-pipe scenario');
    assertEqual(sb.score, 7, '4c: Score stays at 7, no new points on death frame');
})();


// ═══════════════════════════════════════════════════════
// SCENARIO 5: Ground Scrolling Stops on Death
// ═══════════════════════════════════════════════════════

section('5. Ground Scrolling Stops on Death (Early Exit Side Effect)');

// Test 5a: Ground scroll updates during normal play
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 265;   // Safe in gap
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.groundOffset = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    // groundOffset = (0 + PIPE_SPEED * dt) % 24 = (120 * 0.016) % 24 = 1.92 % 24 = 1.92
    assert(sb.groundOffset > 0, '5a: Ground scrolls during normal PLAYING state');
    assertApprox(sb.groundOffset, 1.92, 0.01, '5a: Ground offset = PIPE_SPEED * dt (1.92)');
})();

// Test 5b: Ground scroll does NOT update on death frame (early exit skips it)
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 180;  // collides with pipe
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 60, gapY: 200, scored: false });
    sb.groundOffset = 10.5;  // Set to a known value
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '5b: Death frame transitions to GAME_OVER');
    assertEqual(sb.groundOffset, 10.5, '5b: Ground offset unchanged on death frame (early exit skips ground scroll)');
})();

// Test 5c: Ground also frozen in GAME_OVER state (by design)
(() => {
    const sb = createSandbox();

    sb.gameState = 'GAME_OVER';
    sb.groundOffset = 15.0;

    sb.update(0.016);

    assertEqual(sb.groundOffset, 15.0, '5c: Ground offset unchanged in GAME_OVER state (frozen)');
})();

// Test 5d: Verify this matches expected behavior — ground freezing on death IS acceptable
(() => {
    const sb = createSandbox();

    // Play a normal frame → ground scrolls
    sb.gameState = 'PLAYING';
    sb.bird.y = 265;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.groundOffset = 0;
    sb.distanceSinceLastPipe = 0;
    sb.update(0.016);
    const offsetAfterPlay = sb.groundOffset;
    assert(offsetAfterPlay > 0, '5d: Ground scrolled during PLAYING');

    // Now die → ground stops
    sb.pipes.push({ x: 60, gapY: 200, scored: false });
    sb.bird.y = 180;
    sb.bird.velocity = 0;
    sb.update(0.016);
    assertEqual(sb.gameState, 'GAME_OVER', '5d: Transitioned to GAME_OVER');
    assertEqual(sb.groundOffset, offsetAfterPlay, '5d: Ground froze at same position as death frame');

    // Multiple GAME_OVER frames → still frozen
    sb.update(0.016);
    sb.update(0.016);
    assertEqual(sb.groundOffset, offsetAfterPlay, '5d: Ground still frozen after multiple GAME_OVER frames');
})();


// ═══════════════════════════════════════════════════════
// 6. Source-Level Guard Verification
// ═══════════════════════════════════════════════════════

section('6. Source-Level Verification — Early Exit Guard');

// Test 6a: The guard line exists in source
(() => {
    const guardPattern = /if\s*\(\s*gameState\s*!==\s*STATE_PLAYING\s*\)\s*break/;
    assert(guardPattern.test(src), '6a: Guard "if (gameState !== STATE_PLAYING) break" exists in source');
})();

// Test 6b: Guard appears after checkCollisions()
(() => {
    const checkCollIdx = src.indexOf('checkCollisions()');
    const guardIdx = src.indexOf('if (gameState !== STATE_PLAYING) break');
    assert(checkCollIdx > -1, '6b: checkCollisions() call found in source');
    assert(guardIdx > -1, '6b: Guard line found in source');
    assert(guardIdx > checkCollIdx, '6b: Guard appears AFTER checkCollisions() call');
})();

// Test 6c: Guard appears before updateScore() call within update() function
(() => {
    // Extract just the update() function body to avoid matching function definitions
    const updateFnMatch = src.match(/function\s+update\s*\(\s*dt\s*\)\s*\{([\s\S]*?)\n\}/);
    assert(updateFnMatch !== null, '6c: update() function found in source');
    if (updateFnMatch) {
        const updateBody = updateFnMatch[1];
        const guardIdx = updateBody.indexOf('if (gameState !== STATE_PLAYING) break');
        const updateScoreIdx = updateBody.indexOf('updateScore()');
        assert(guardIdx > -1 && updateScoreIdx > -1, '6c: Both guard and updateScore() call found in update()');
        assert(guardIdx < updateScoreIdx, '6c: Guard appears BEFORE updateScore() call in update()');
    }
})();

// Test 6d: The full execution order in STATE_PLAYING case
(() => {
    // Extract the STATE_PLAYING case block from within update()
    const updateFnMatch = src.match(/function\s+update\s*\(\s*dt\s*\)\s*\{([\s\S]*?)\n\}/);
    assert(updateFnMatch !== null, '6d: update() function found');

    if (updateFnMatch) {
        const updateBody = updateFnMatch[1];
        // Extract the full STATE_PLAYING case (up to STATE_GAME_OVER, since the guard's
        // break; would prematurely end a greedy break; match)
        const playingCaseMatch = updateBody.match(/case\s+STATE_PLAYING\s*:([\s\S]*?)case\s+STATE_GAME_OVER/);
        assert(playingCaseMatch !== null, '6d: STATE_PLAYING case block found');

        if (playingCaseMatch) {
            const block = playingCaseMatch[1];
            const updateBirdPos = block.indexOf('updateBird(');
            const updatePipesPos = block.indexOf('updatePipes(');
            const checkCollisionsPos = block.indexOf('checkCollisions()');
            const guardPos = block.indexOf('gameState !== STATE_PLAYING');
            const updateScorePos = block.indexOf('updateScore()');
            const groundOffsetPos = block.indexOf('groundOffset =');

            assert(updateBirdPos > -1 && updateBirdPos < updatePipesPos, '6d: updateBird() before updatePipes()');
            assert(updatePipesPos > -1 && updatePipesPos < checkCollisionsPos, '6d: updatePipes() before checkCollisions()');
            assert(checkCollisionsPos > -1 && checkCollisionsPos < guardPos, '6d: checkCollisions() before guard');
            assert(guardPos > -1 && guardPos < updateScorePos, '6d: guard before updateScore()');
            assert(updateScorePos > -1 && updateScorePos < groundOffsetPos, '6d: updateScore() before groundOffset update');
        }
    }
})();


// ═══════════════════════════════════════════════════════
// 7. Edge Cases
// ═══════════════════════════════════════════════════════

section('7. Edge Cases');

// Test 7a: Bird at exact pipe center threshold (x + PIPE_WIDTH/2 === bird.x) — not scoreable
// NOTE: updatePipes(dt) moves pipe left by PIPE_SPEED*dt = 120*0.016 = 1.92 BEFORE scoring,
// so we must account for that movement when setting up the threshold test.
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 265;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    // After updatePipes: new_x = x - 1.92. Center = new_x + 26.
    // For center === 100 after movement: new_x = 74, so x = 74 + 1.92 = 75.92
    // Scoring requires center < bird.x, so 100 < 100 is false → no score
    sb.pipes.push({ x: 75.92, gapY: 250, scored: false });
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.score, 0, '7a: Pipe center exactly at bird.x after movement → no score (strict < comparison)');
})();

// Test 7b: Pipe at center = bird.x - 0.001 → scores (just barely past)
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 265;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    // x + 26 < 100 → x < 74. Use x=73.999 → center = 99.999 < 100 ✓
    sb.pipes.push({ x: 73.999, gapY: 250, scored: false });
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    // After updatePipes, pipe moves left by PIPE_SPEED*dt = 120*0.016 = 1.92
    // New x = 73.999 - 1.92 = 72.079. Center = 72.079 + 26 = 98.079 < 100 ✓
    assertEqual(sb.score, 1, '7b: Pipe center barely past bird.x → scores');
})();

// Test 7c: Death on frame with no pipes → GAME_OVER with no scoring attempt
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 540 - sb.BIRD_RADIUS; // at ground
    sb.bird.velocity = 100;
    sb.pipes.length = 0;  // No pipes at all
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', '7c: Ground death with no pipes → GAME_OVER');
    assertEqual(sb.score, 0, '7c: Score stays 0 with no pipes');
})();

// Test 7d: State doesn't change to anything unexpected (no intermediate states)
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 180;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 60, gapY: 200, scored: false });
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assert(sb.gameState === 'GAME_OVER', '7d: State is exactly GAME_OVER (no intermediate states)');
    assert(sb.gameState !== 'PLAYING', '7d: Not still PLAYING');
    assert(sb.gameState !== 'IDLE', '7d: Not IDLE');
})();

// Test 7e: After death, subsequent update() calls don't change score
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 180;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 60, gapY: 200, scored: false });
    sb.score = 5;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);
    assertEqual(sb.gameState, 'GAME_OVER', '7e: Died');
    assertEqual(sb.score, 5, '7e: Score frozen at 5 on death');

    // Multiple subsequent frames
    sb.update(0.016);
    sb.update(0.016);
    sb.update(0.016);
    assertEqual(sb.score, 5, '7e: Score still 5 after multiple GAME_OVER frames');
})();

// Test 7f: Rapid dt (very large timestep) — collision still triggers before scoring
(() => {
    const sb = createSandbox();

    sb.gameState = 'PLAYING';
    sb.bird.y = 300;  // Starting position
    sb.bird.velocity = 500;  // Falling fast
    sb.pipes.length = 0;
    sb.pipes.push({ x: 40, gapY: 100, scored: false }); // Very high pipe, scoreable
    sb.score = 0;
    sb.distanceSinceLastPipe = 0;

    // Large dt — bird will fall through ground (300 + 500*0.5 = 550 → ground collision)
    sb.update(0.5);

    assertEqual(sb.gameState, 'GAME_OVER', '7f: Large dt → ground collision');
    assertEqual(sb.score, 0, '7f: Score stays 0 even with large dt');
})();


// ═══════════════════════════════════════════════════════
// 8. Regression — Full Play Cycle
// ═══════════════════════════════════════════════════════

section('8. Regression — Full Play Cycle (IDLE → PLAYING → score → GAME_OVER → reset)');

(() => {
    const sb = createSandbox();

    // Start in IDLE
    assertEqual(sb.gameState, 'IDLE', '8: Starts in IDLE');

    // Transition to PLAYING
    sb.handleInput();
    assertEqual(sb.gameState, 'PLAYING', '8: handleInput → PLAYING');

    // Simulate a few safe frames
    sb.bird.y = 265;
    sb.bird.velocity = -100; // going up slightly
    sb.pipes.length = 0;
    sb.pipes.push({ x: 50, gapY: 200, scored: false });
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);
    assertEqual(sb.gameState, 'PLAYING', '8: Still PLAYING after safe frame');
    assertEqual(sb.score, 1, '8: Scored 1 after first pipe pass');

    // Now cause a collision
    sb.bird.y = 180;
    sb.bird.velocity = 0;
    sb.pipes.push({ x: 70, gapY: 200, scored: false });

    sb.update(0.016);
    assertEqual(sb.gameState, 'GAME_OVER', '8: Collision → GAME_OVER');
    assertEqual(sb.score, 1, '8: Score frozen at 1 (no death-frame scoring)');

    // Reset
    sb.handleInput();  // In GAME_OVER, handleInput calls resetGame → IDLE
    assertEqual(sb.gameState, 'IDLE', '8: resetGame → IDLE');
    assertEqual(sb.score, 0, '8: Score reset to 0');
})();


// ═══════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════

console.log('\n═══════════════════════════════════════════════════════');
console.log(`  TS-030 BUG-002 FIX QA RESULTS: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('═══════════════════════════════════════════════════════');

if (failures.length > 0) {
    console.log('\n❌ FAILURES:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

if (bugs.length > 0) {
    console.log('\n🐛 BUGS FOUND:');
    bugs.forEach(b => {
        console.log(`  BUG-${b.id}: ${b.summary}`);
        console.log(`    Steps: ${b.steps}`);
        console.log(`    Expected: ${b.expected}`);
        console.log(`    Actual: ${b.actual}`);
    });
}

if (failed === 0) {
    console.log('\n✅ QA VERDICT: BUG-002 FIX VERIFIED — All scenarios pass');
    console.log('   The early-exit guard correctly prevents scoring on death frames.');
    console.log('   Normal scoring, ground collision, multi-pipe, and ground-freeze behaviors verified.');
} else {
    console.log('\n❌ QA VERDICT: BUG-002 FIX HAS ISSUES — See failures above');
}

process.exit(failed > 0 ? 1 : 0);
