/**
 * TS-015 — Additional QA Verification: PLAYING state gameplay mechanics (CD-015)
 * Supplementary tests covering gaps found during initial QA run.
 *
 * Coverage:
 *  A. BUG-001: Score-on-death-frame — no early exit after checkCollisions()
 *  B. Ceiling collision behavior change (removed GAME_OVER on ceiling hit)
 *  C. Corrected execution order source verification (fixes test bug in Section 16)
 *  D. Multi-pipe spawn with large dt (single-if vs while loop)
 *  E. Collision detection boundary precision
 *  F. circleRectCollision edge-on-boundary (<=) vs strict-less-than (<)
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

// ─── read source once ───

const src = fs.readFileSync(path.join(__dirname, '..', 'game.js'), 'utf8');

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
            textBaseline: '',
            lineJoin: '',
            fillRect: () => {},
            strokeRect: () => {},
            clearRect: () => {},
            beginPath: () => {},
            closePath: () => {},
            arc: () => {},
            ellipse: () => {},
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
            spawnPipe,

            // Test hooks
            _listeners, _rafCallback, _renderCalls, _ctxStub
        })
    `;

    return eval(evalCode);
}


// ═══════════════════════════════════════════════════════
// A. BUG-001: Score on Death Frame (No Early Exit)
// ═══════════════════════════════════════════════════════

section('A. BUG-001: Score on Death Frame — No Early Exit After Collision');

(() => {
    // Scenario: Bird hits ground while a scored-eligible pipe exists.
    // Expected: score should NOT increment on the death frame.
    // Actual: score increments because updateScore() runs after checkCollisions().
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.score = 0;

    // Place bird near ground (will trigger ground collision in update)
    sb.bird.y = 530;
    sb.bird.velocity = 100; // Falling

    // Place a pipe already passed by bird (eligible for scoring)
    // Pipe center = 20 + 26 = 46, bird.x = 100 → 46 < 100 → scoreable
    sb.pipes.push({ x: 20, gapY: 200, scored: false });
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    // After update: checkCollisions() sets GAME_OVER, then updateScore() still runs
    assertEqual(sb.gameState, 'GAME_OVER', 'Ground collision detected');

    // This is the BUG: score SHOULD be 0, but is 1 due to missing early exit
    if (sb.score === 1) {
        logBug('001',
            'Score increments on death frame — no early exit after checkCollisions()',
            '1. Set bird.y=530 (near ground), add pipe at x=20 (passed). 2. Call update(0.016).',
            'score = 0 (collision should prevent scoring)',
            'score = 1 (updateScore runs after checkCollisions sets GAME_OVER)'
        );
        // Mark as known failure for tracking
        assert(false, 'BUG-001: Score should NOT increment on death frame (score=' + sb.score + ')');
    } else {
        assert(sb.score === 0, 'Score correctly stays 0 on death frame');
    }
})();

(() => {
    // Same scenario with pipe collision instead of ground
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.score = 0;
    sb.bird.y = 300;
    sb.bird.velocity = 0;

    // Pipe directly at bird's position — will trigger pipe collision
    // gapY=400 means top pipe goes from 0 to 400, bird at y=300 hits top pipe
    sb.pipes.push({ x: sb.BIRD_X - sb.PIPE_WIDTH / 2, gapY: 400, scored: false });

    // Also add a fully-passed pipe eligible for scoring
    sb.pipes.push({ x: 20, gapY: 200, scored: false });
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'GAME_OVER', 'Pipe collision detected');

    if (sb.score > 0) {
        logBug('001b',
            'Score increments on pipe-collision death frame',
            '1. Place pipe at bird position (collision) + passed pipe (scoreable). 2. Call update(0.016).',
            'score = 0',
            `score = ${sb.score}`
        );
        assert(false, 'BUG-001b: Score should NOT increment on pipe-collision death frame (score=' + sb.score + ')');
    } else {
        assert(sb.score === 0, 'Score correctly stays 0 on pipe-collision death frame');
    }
})();

(() => {
    // Verify the missing guard in source code
    // The fix would be: if (gameState !== STATE_PLAYING) break; after checkCollisions()
    const updateFn = src.substring(
        src.indexOf('function update(dt)'),
        src.indexOf('// ===== RENDER LOGIC =====')
    );

    const playingSection = updateFn.substring(
        updateFn.indexOf("case STATE_PLAYING:"),
        updateFn.indexOf("case STATE_GAME_OVER:")
    );

    const collisionIdx = playingSection.indexOf('checkCollisions');
    const scoreIdx = playingSection.indexOf('updateScore');

    // Check if there's a guard between collision check and scoring
    if (collisionIdx >= 0 && scoreIdx >= 0) {
        const betweenCollisionAndScore = playingSection.substring(collisionIdx, scoreIdx);
        const hasGuard = betweenCollisionAndScore.includes('gameState') &&
                         (betweenCollisionAndScore.includes('break') || betweenCollisionAndScore.includes('return'));

        if (!hasGuard) {
            logBug('001c',
                'No state guard between checkCollisions() and updateScore() in PLAYING case',
                'Read source: update() → case STATE_PLAYING',
                'Guard like "if (gameState !== STATE_PLAYING) break;" between checkCollisions() and updateScore()',
                'updateScore() runs unconditionally after checkCollisions()'
            );
        }
        assert(hasGuard, 'Guard exists between checkCollisions() and updateScore() in PLAYING case');
    }
})();


// ═══════════════════════════════════════════════════════
// B. Ceiling Collision Behavior Change
// ═══════════════════════════════════════════════════════

section('B. Ceiling Collision Behavior');

(() => {
    // The old checkCollision() had: if (bird.y - bird.radius <= 0) return true;
    // The new checkCollisions() does NOT check ceiling.
    // updateBird() clamps bird at ceiling but does NOT trigger GAME_OVER.
    // Document this behavioral change.

    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 5;  // Near ceiling
    sb.bird.velocity = -500; // Flying upward hard

    sb.updateBird(0.016);

    // Bird should be clamped to ceiling
    assertEqual(sb.bird.y, sb.BIRD_RADIUS,
        'Bird clamped to ceiling (bird.y = BIRD_RADIUS = 15) when flying above top');
    assertEqual(sb.bird.velocity, 0,
        'Bird velocity zeroed at ceiling (stops upward movement)');
})();

(() => {
    // Verify ceiling does NOT trigger GAME_OVER
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 1; // At ceiling
    sb.bird.velocity = -100;
    sb.distanceSinceLastPipe = 0;
    sb.pipes.length = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'PLAYING',
        'Ceiling contact does NOT trigger GAME_OVER (bird is clamped, not killed)');
})();

(() => {
    // Verify checkCollisions() doesn't include ceiling check
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 0; // Exactly at top edge
    sb.pipes.length = 0;

    sb.checkCollisions();

    assertEqual(sb.gameState, 'PLAYING',
        'checkCollisions() at bird.y=0 with no pipes → stays PLAYING (no ceiling check)');
})();

(() => {
    // Document: old code killed on ceiling, new code does not
    // This verifies the DESIGN CHOICE (not a bug) — ceiling is non-lethal
    const checkCollisionsSrc = src.substring(
        src.indexOf('function checkCollisions()'),
        src.indexOf('// ===== SCORING =====')
    );

    const hasCeilingCheck = checkCollisionsSrc.includes('bird.y - bird.radius') &&
                            checkCollisionsSrc.includes('GAME_OVER');

    // We expect NO ceiling check in the new implementation
    assert(!hasCeilingCheck,
        'checkCollisions() correctly omits ceiling → GAME_OVER (non-lethal ceiling is a design choice)');
})();


// ═══════════════════════════════════════════════════════
// C. Corrected Execution Order Source Verification
//    (Fixes test bug in original Section 16)
// ═══════════════════════════════════════════════════════

section('C. Corrected Execution Order — Source Code Verification');

(() => {
    // ORIGINAL BUG: Section 16 used src.indexOf('case STATE_PLAYING:')
    // which finds the FIRST occurrence in handleInput(), not in update().
    // Fix: extract the update() function body first.

    const updateFnStart = src.indexOf('function update(dt)');
    const updateFnEnd = src.indexOf('// ===== RENDER LOGIC =====');
    const updateFn = src.substring(updateFnStart, updateFnEnd);

    const playingCase = updateFn.substring(
        updateFn.indexOf('case STATE_PLAYING:'),
        updateFn.indexOf('case STATE_GAME_OVER:')
    );

    assert(playingCase.length > 0, 'PLAYING case block extracted from update() function');

    const birdIdx = playingCase.indexOf('updateBird');
    const pipeIdx = playingCase.indexOf('updatePipes');
    const collIdx = playingCase.indexOf('checkCollisions');
    const scoreIdx = playingCase.indexOf('updateScore');
    const groundIdx = playingCase.indexOf('groundOffset');

    assert(birdIdx >= 0, 'updateBird() found in update() PLAYING case');
    assert(pipeIdx >= 0, 'updatePipes() found in update() PLAYING case');
    assert(collIdx >= 0, 'checkCollisions() found in update() PLAYING case');
    assert(scoreIdx >= 0, 'updateScore() found in update() PLAYING case');
    assert(groundIdx >= 0, 'groundOffset update found in update() PLAYING case');

    // Verify correct execution order
    if (birdIdx >= 0 && pipeIdx >= 0) {
        assert(pipeIdx > birdIdx, 'Order: updatePipes() after updateBird()');
    }
    if (pipeIdx >= 0 && collIdx >= 0) {
        assert(collIdx > pipeIdx, 'Order: checkCollisions() after updatePipes()');
    }
    if (collIdx >= 0 && scoreIdx >= 0) {
        assert(scoreIdx > collIdx, 'Order: updateScore() after checkCollisions()');
    }
    if (scoreIdx >= 0 && groundIdx >= 0) {
        assert(groundIdx > scoreIdx, 'Order: groundOffset after updateScore()');
    }
})();

(() => {
    // Verify the order matches the PRD specification:
    // bird physics → ground scroll → pipes → scoring → collision (per PRD section 6)
    // ACTUAL order: bird physics → pipes → collision → scoring → ground scroll
    // The implementation has collision BEFORE scoring (correct for preventing death-frame scoring,
    // if the early exit guard were present).

    const updateFnStart = src.indexOf('function update(dt)');
    const updateFnEnd = src.indexOf('// ===== RENDER LOGIC =====');
    const updateFn = src.substring(updateFnStart, updateFnEnd);

    const playingCase = updateFn.substring(
        updateFn.indexOf('case STATE_PLAYING:'),
        updateFn.indexOf('case STATE_GAME_OVER:')
    );

    // Check numbered comments in source for documentation accuracy
    assert(playingCase.includes('// 1. Bird physics'), 'Step 1 documented: Bird physics');
    assert(playingCase.includes('// 2. Pipe spawning'), 'Step 2 documented: Pipe spawning');
    assert(playingCase.includes('// 3. Collision detection'), 'Step 3 documented: Collision detection');
    assert(playingCase.includes('// 4. Scoring'), 'Step 4 documented: Scoring');
    assert(playingCase.includes('// 5. Ground scrolling'), 'Step 5 documented: Ground scrolling');
})();


// ═══════════════════════════════════════════════════════
// D. Multi-Pipe Spawn with Large dt
// ═══════════════════════════════════════════════════════

section('D. Multi-Pipe Spawn Behavior with Large dt');

(() => {
    // updatePipes uses `if` (not `while`) for spawn check.
    // With large dt, only ONE pipe is spawned per call even if distance exceeds 2x PIPE_SPACING.
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.distanceSinceLastPipe = 0;
    sb.pipes.length = 0;

    // 5 seconds → 600px distance → floor(600/220) = 2 pipes expected with `while`, 1 with `if`
    sb.updatePipes(5.0);

    // Document: single `if` means only 1 pipe per call
    assertEqual(sb.pipes.length, 1,
        'Large dt: only 1 pipe spawned per updatePipes call (single if, not while loop)');

    // After 1 spawn: remainder = 600 - 220 = 380
    assertApprox(sb.distanceSinceLastPipe, 380, 0.1,
        'Remainder after single spawn: 600 - 220 = 380 (still >= PIPE_SPACING)');
})();

(() => {
    // Verify that repeated calls drain the accumulated distance properly
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.distanceSinceLastPipe = 0;
    sb.pipes.length = 0;

    // Accumulate 600px
    sb.updatePipes(5.0); // Spawn 1, remainder 380
    assertEqual(sb.pipes.length, 1, 'First call: 1 pipe');

    // Call again with small dt, remainder 380 + 1.2 = 381.2 >= 220 → spawn
    sb.updatePipes(0.01);
    assertEqual(sb.pipes.length, 2, 'Second call: 2 pipes total (accumulated remainder triggers spawn)');

    // Remainder: 381.2 - 220 = 161.2
    assertApprox(sb.distanceSinceLastPipe, 161.2, 0.2, 'Remainder after second spawn ≈ 161.2');
})();


// ═══════════════════════════════════════════════════════
// E. Collision Detection Boundary Precision
// ═══════════════════════════════════════════════════════

section('E. Collision Detection Boundary Precision');

(() => {
    // circleRectCollision uses `<=` (not `<`) for collision check
    // distSquared <= r * r
    // Old code used `<` (strict less-than)
    // This is a behavioral change: touching (distance = exactly radius) now counts as collision
    const sb = createSandbox();

    // Circle center at (50, 50), radius 15
    // Rectangle at (65, 40, 20, 20) — left edge at x=65
    // Nearest point: (65, 50). Distance: 15.0 = radius exactly
    const result = sb.circleRectCollision(50, 50, 15, 65, 40, 20, 20);

    assert(result === true,
        'circleRectCollision returns true when distance = exactly radius (<=, touching counts)');
})();

(() => {
    // Compare: old code used `<` which would return false for touching
    const sb = createSandbox();

    // Distance slightly > radius → should NOT collide
    // Circle center at (50, 50), radius 15
    // Rectangle at (65.1, 40, 20, 20)
    // Nearest point: (65.1, 50). Distance: 15.1 > 15 → no collision
    const result = sb.circleRectCollision(50, 50, 15, 65.1, 40, 20, 20);

    assert(result === false,
        'circleRectCollision returns false when distance > radius (15.1 > 15)');
})();

(() => {
    // Ground collision boundary: exactly at threshold
    const sb = createSandbox();

    // bird.y + bird.radius >= CANVAS_HEIGHT - GROUND_HEIGHT
    // 525 + 15 = 540 >= 540 → collision
    sb.bird.y = sb.CANVAS_HEIGHT - sb.GROUND_HEIGHT - sb.BIRD_RADIUS; // = 525
    const result = sb.checkGroundCollision();
    assert(result === true, 'Ground collision at exact boundary: 525 + 15 = 540 >= 540');
})();

(() => {
    // Just below ground collision boundary
    const sb = createSandbox();
    sb.bird.y = sb.CANVAS_HEIGHT - sb.GROUND_HEIGHT - sb.BIRD_RADIUS - 0.001; // = 524.999
    const result = sb.checkGroundCollision();
    assert(result === false, 'No ground collision at 524.999 + 15 = 539.999 < 540');
})();


// ═══════════════════════════════════════════════════════
// F. clamp() Function Verification
// ═══════════════════════════════════════════════════════

section('F. clamp() Helper Function');

(() => {
    const sb = createSandbox();

    assertEqual(sb.clamp(5, 0, 10), 5, 'clamp(5, 0, 10) = 5 (within range)');
    assertEqual(sb.clamp(-5, 0, 10), 0, 'clamp(-5, 0, 10) = 0 (below min)');
    assertEqual(sb.clamp(15, 0, 10), 10, 'clamp(15, 0, 10) = 10 (above max)');
    assertEqual(sb.clamp(0, 0, 10), 0, 'clamp(0, 0, 10) = 0 (at min)');
    assertEqual(sb.clamp(10, 0, 10), 10, 'clamp(10, 0, 10) = 10 (at max)');
})();


// ═══════════════════════════════════════════════════════
// G. Pipe Collision Horizontal Optimization
// ═══════════════════════════════════════════════════════

section('G. Pipe Collision Horizontal Optimization');

(() => {
    // Pipes far to the right should be skipped
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 300;

    // Pipe far right: x = 400 → 400 > 100 + 15 + 52 = 167 → skip
    sb.pipes.push({ x: 400, gapY: 200, scored: false });
    const result = sb.checkPipeCollisions();
    assert(result === false, 'Pipe far right (x=400) correctly skipped — no collision');
})();

(() => {
    // Pipes far to the left should be skipped
    const sb = createSandbox();
    sb.bird.y = 300;

    // Pipe far left: x = -100 → -100 + 52 = -48 < 100 - 15 = 85 → skip
    sb.pipes.push({ x: -100, gapY: 200, scored: false });
    const result = sb.checkPipeCollisions();
    assert(result === false, 'Pipe far left (x=-100) correctly skipped — no collision');
})();

(() => {
    // Pipe at bird's x — in gap — no collision
    const sb = createSandbox();
    sb.bird.y = 300; // Middle of gap

    // Pipe at bird's x, gap from 250 to 380 (250 + 130)
    // Bird circle: center 300, radius 15 → spans 285 to 315
    // Top pipe: 0 to 250, Bottom pipe: 380 to 540
    // Bird is in gap (285-315 is within 250-380)
    sb.pipes.push({ x: sb.BIRD_X - sb.PIPE_WIDTH / 2, gapY: 250, scored: false });
    const result = sb.checkPipeCollisions();
    assert(result === false, 'Bird safely in pipe gap — no collision');
})();

(() => {
    // Bird edge touching top pipe edge
    const sb = createSandbox();

    // gapY = 300, bird at y = 300 - BIRD_RADIUS = 285
    // Bird top = 285 - 15 = 270, bird bottom = 285 + 15 = 300
    // Top pipe bottom = 300 (gapY)
    // Circle at (100, 285), radius 15
    // Nearest point on top pipe (74, 0, 52, 300): (100, 285) clamped → (100, 285) — inside rect!
    // Actually bird.y = 285 is within the top pipe rect (0 to 300 in y), so distance = 0 → collision
    sb.pipes.push({ x: sb.BIRD_X - sb.PIPE_WIDTH / 2, gapY: 300, scored: false });
    sb.bird.y = 285;
    const result = sb.checkPipeCollisions();
    assert(result === true, 'Bird overlapping top pipe edge → collision');
})();


// ═══════════════════════════════════════════════════════
// H. shouldSpawnPipe() Legacy Function
// ═══════════════════════════════════════════════════════

section('H. shouldSpawnPipe() Function (legacy, still in codebase)');

(() => {
    // shouldSpawnPipe is defined but no longer called by updatePipes
    // updatePipes now uses the distance accumulator instead
    // Verify the function exists but is not used in updatePipes
    const updatePipesSrc = src.substring(
        src.indexOf('function updatePipes(dt)'),
        src.indexOf('function renderPipes')
    );

    const usedInUpdate = updatePipesSrc.includes('shouldSpawnPipe');
    assert(!usedInUpdate,
        'shouldSpawnPipe() is NOT called inside updatePipes() (distance accumulator used instead)');

    // But verify the function still exists in source (dead code)
    assert(src.includes('function shouldSpawnPipe()'),
        'shouldSpawnPipe() function still exists in source (dead code — candidate for cleanup)');
})();


// ═══════════════════════════════════════════════════════
// I. Ground Scrolling Continues in PLAYING
// ═══════════════════════════════════════════════════════

section('I. Ground Scrolling in PLAYING State');

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.groundOffset = 0;
    sb.bird.y = 300;
    sb.bird.velocity = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.1);

    // groundOffset = (0 + 120 * 0.1) % 24 = 12 % 24 = 12
    assertApprox(sb.groundOffset, 12, 0.01,
        'Ground offset updates during PLAYING: (0 + 120*0.1) % 24 = 12');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.groundOffset = 20;
    sb.bird.y = 300;
    sb.bird.velocity = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.1);

    // groundOffset = (20 + 12) % 24 = 32 % 24 = 8
    assertApprox(sb.groundOffset, 8, 0.01,
        'Ground offset wraps around: (20 + 12) % 24 = 8');
})();


// ═══════════════════════════════════════════════════════
// RESULTS
// ═══════════════════════════════════════════════════════

console.log('\n═══════════════════════════════════════════');
console.log(`  TS-015 ADDITIONAL QA: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('═══════════════════════════════════════════');

if (failures.length > 0) {
    console.log('\n❌ FAILURES:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

if (bugs.length > 0) {
    console.log('\n🐛 BUGS FOUND:\n');
    bugs.forEach(b => {
        console.log(`  BUG-${b.id}: ${b.summary}`);
        console.log(`    Steps:    ${b.steps}`);
        console.log(`    Expected: ${b.expected}`);
        console.log(`    Actual:   ${b.actual}`);
        console.log();
    });
}

console.log('\n═══════════════════════════════════════════');
console.log('  ADDITIONAL COVERAGE SUMMARY');
console.log('═══════════════════════════════════════════');
console.log('  A.  Score-on-death-frame bug (BUG-001)        — Documented & verified');
console.log('  B.  Ceiling collision behavior change          — Non-lethal verified');
console.log('  C.  Execution order (corrected extraction)     — Verified');
console.log('  D.  Multi-pipe spawn with large dt             — Single-spawn-per-call verified');
console.log('  E.  Collision boundary precision               — <= vs < verified');
console.log('  F.  clamp() helper                             — Verified');
console.log('  G.  Pipe collision horizontal optimization     — Verified');
console.log('  H.  shouldSpawnPipe() dead code                — Documented');
console.log('  I.  Ground scrolling in PLAYING                — Verified');

if (failed > 0) {
    process.exit(1);
}
