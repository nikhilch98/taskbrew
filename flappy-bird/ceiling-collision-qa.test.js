/**
 * TS-029 -- QA Verification: Ceiling Collision Bug Fix (CD-025)
 * Automated test suite using Node.js (no external dependencies)
 *
 * Tests cover:
 *  1. No duplicate checkCeilingCollision function in game.js
 *  2. Ceiling clamp works: bird stops at top, does NOT trigger GAME_OVER
 *  3. Ceiling velocity reset: after hitting ceiling, bird falls normally (velocity resets to 0)
 *  4. Ground collision still triggers GAME_OVER correctly
 *  5. Pipe collision still triggers GAME_OVER correctly
 *  6. Rapid flapping to ceiling does not cause death
 *  7. Integration: full play cycle including ceiling interaction
 *
 * PRD References:
 *  AC-2.4: "Bird y-position is clamped -- cannot fly above canvas top" (clamp only, no death)
 *  AC-2.5: "Bird hitting ground triggers GAME_OVER" (should still work)
 *  AC-4.1: "Collision with any pipe surface ends the game" (should still work)
 */

const fs   = require('fs');
const path = require('path');

// --- helpers ---

let passed = 0;
let failed = 0;
const failures = [];
const bugs = [];

function assert(condition, message) {
    if (condition) {
        passed++;
        console.log(`  PASS ${message}`);
    } else {
        failed++;
        console.log(`  FAIL ${message}`);
        failures.push(message);
    }
}

function assertEqual(actual, expected, message) {
    if (actual === expected) {
        passed++;
        console.log(`  PASS ${message}`);
    } else {
        failed++;
        const msg = `${message}  -- expected: ${JSON.stringify(expected)}, got: ${JSON.stringify(actual)}`;
        console.log(`  FAIL ${msg}`);
        failures.push(msg);
    }
}

function assertApprox(actual, expected, tolerance, message) {
    if (Math.abs(actual - expected) <= tolerance) {
        passed++;
        console.log(`  PASS ${message}`);
    } else {
        failed++;
        const msg = `${message}  -- expected ~${expected} (+-${tolerance}), got: ${actual}`;
        console.log(`  FAIL ${msg}`);
        failures.push(msg);
    }
}

function logBug(id, summary, steps, expected, actual) {
    bugs.push({ id, summary, steps, expected, actual });
    console.log(`  BUG-${id}: ${summary}`);
}

function section(title) {
    console.log(`\n=== ${title} ===`);
}

// --- read source once ---

const src = fs.readFileSync(path.join(__dirname, 'game.js'), 'utf8');

// --- DOM/Canvas stub ---

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
            update, render, gameLoop,
            spawnPipe,

            // Test hooks
            _listeners, _rafCallback, _renderCalls, _ctxStub
        })
    `;

    return eval(evalCode);
}

console.log('============================================================');
console.log('  TS-029: QA Verification -- Ceiling Collision Bug Fix');
console.log('============================================================');


// ===========================================================
// 0. Sandbox Smoke Test
// ===========================================================

section('0. Sandbox Smoke Test');

let sb;
try {
    sb = createSandbox();
    assert(sb !== null && sb !== undefined, 'Sandbox created successfully');
    assert(typeof sb.updateBird === 'function', 'updateBird is a function');
    assert(typeof sb.checkCollisions === 'function', 'checkCollisions is a function');
    assert(typeof sb.checkGroundCollision === 'function', 'checkGroundCollision is a function');
    assert(typeof sb.checkPipeCollisions === 'function', 'checkPipeCollisions is a function');
    assert(typeof sb.update === 'function', 'update is a function');
} catch (e) {
    console.error(`  FAIL Sandbox creation failed: ${e.message}`);
    failed++;
    failures.push(`Sandbox creation failed: ${e.message}`);
}


// ===========================================================
// 1. No Duplicate checkCeilingCollision Function
// ===========================================================

section('1. No Duplicate checkCeilingCollision Function');

(() => {
    // Search for any function named checkCeilingCollision
    const hasCeilingFn = src.includes('function checkCeilingCollision') ||
                          src.includes('checkCeilingCollision =');
    assertEqual(hasCeilingFn, false, 'No checkCeilingCollision function exists in game.js');
})();

(() => {
    // Count occurrences of 'checkCeilingCollision' anywhere in source
    const matches = src.match(/checkCeilingCollision/g);
    const count = matches ? matches.length : 0;
    assertEqual(count, 0, `checkCeilingCollision appears ${count} times in source (expected 0)`);
})();

(() => {
    // Verify ceiling handling is done ONLY in updateBird as a clamp
    const updateBirdFn = src.slice(
        src.indexOf('function updateBird('),
        src.indexOf('// ===== INPUT HANDLERS')
    );
    const hasCeilingClamp = updateBirdFn.includes('bird.y - bird.radius < 0') ||
                             updateBirdFn.includes('bird.y - bird.radius <= 0');
    assert(hasCeilingClamp, 'Ceiling clamp logic exists in updateBird()');
})();


// ===========================================================
// 2. Ceiling Clamp Works -- Bird Stops at Top, No GAME_OVER
// ===========================================================

section('2. Ceiling Clamp -- Bird Stops at Top, No GAME_OVER (AC-2.4)');

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    // Place bird well above ceiling
    sb.bird.y = -50;
    sb.bird.velocity = -280; // FLAP_VELOCITY (going up fast)
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    sb.updateBird(0.016);

    // Bird should be clamped to radius (15) from top
    assertEqual(sb.bird.y, sb.BIRD_RADIUS, 'Bird clamped to y=BIRD_RADIUS (15) when above ceiling');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    // Bird at ceiling edge: y - radius = 0
    sb.bird.y = sb.BIRD_RADIUS; // y=15, y-r=0
    sb.bird.velocity = -280; // trying to go up
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    sb.updateBird(0.016);

    // After gravity and position update, if bird goes above ceiling, it should be clamped
    assert(sb.bird.y >= sb.BIRD_RADIUS, 'Bird does not go above ceiling (y >= BIRD_RADIUS)');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 5; // bird.y - bird.radius = 5-15 = -10 (above ceiling)
    sb.bird.velocity = -200;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    // Run full update (not just updateBird), state should stay PLAYING
    sb.update(0.016);

    assertEqual(sb.gameState, 'PLAYING', 'Game stays PLAYING when bird hits ceiling (no GAME_OVER)');
    assert(sb.bird.y >= sb.BIRD_RADIUS, 'Bird position clamped at ceiling after full update');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 50;
    sb.bird.velocity = -280;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    // Rapidly flap multiple times to try to reach ceiling
    for (let i = 0; i < 20; i++) {
        sb.flap();
        sb.update(0.016);
    }

    assertEqual(sb.gameState, 'PLAYING', 'Rapid flapping to ceiling does NOT trigger GAME_OVER');
    assert(sb.bird.y >= sb.BIRD_RADIUS, 'Bird clamped at ceiling despite rapid flapping');
})();

// Test: checkCollisions does NOT check ceiling
(() => {
    const sb = createSandbox();
    sb.bird.y = 5; // Above ceiling
    sb.bird.velocity = 0;
    sb.pipes.length = 0;

    // Call checkCollisions directly - should NOT trigger game over
    sb.checkCollisions();
    assertEqual(sb.gameState, 'IDLE', 'checkCollisions() does not trigger GAME_OVER for ceiling position');
})();

// Verify via source: checkCollisions has no ceiling check
(() => {
    const checkCollisionsFn = src.slice(
        src.indexOf('function checkCollisions()'),
        src.indexOf('// ===== SCORING')
    );

    const hasCeilingInCollision = checkCollisionsFn.includes('bird.y - bird.radius') ||
                                   checkCollisionsFn.includes('ceiling');
    assertEqual(hasCeilingInCollision, false, 'checkCollisions() has NO ceiling check (clamp-only per AC-2.4)');
})();


// ===========================================================
// 3. Ceiling Velocity Reset -- Bird Falls Normally
// ===========================================================

section('3. Ceiling Velocity Reset (bird falls normally after ceiling hit)');

(() => {
    const sb = createSandbox();
    sb.bird.y = 5; // Above ceiling (5-15 = -10)
    sb.bird.velocity = -280; // Going up

    sb.updateBird(0.016);

    assertEqual(sb.bird.velocity, 0, 'Velocity reset to 0 after hitting ceiling');
})();

(() => {
    const sb = createSandbox();
    sb.bird.y = 5;
    sb.bird.velocity = -280;

    // First frame: hit ceiling, velocity reset
    sb.updateBird(0.016);
    assertEqual(sb.bird.velocity, 0, 'Frame 1: Velocity is 0 at ceiling');
    assertEqual(sb.bird.y, sb.BIRD_RADIUS, 'Frame 1: Bird clamped at ceiling');

    // Second frame: gravity kicks in, bird starts falling
    sb.updateBird(0.016);
    const expectedVelocity = sb.GRAVITY * 0.016; // 980 * 0.016 = 15.68
    assertApprox(sb.bird.velocity, expectedVelocity, 0.1, 'Frame 2: Bird gains downward velocity from gravity');
    assert(sb.bird.y > sb.BIRD_RADIUS, 'Frame 2: Bird moves down from ceiling');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 5;
    sb.bird.velocity = -280;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    // Hit ceiling
    sb.update(0.016);
    const yAfterCeiling = sb.bird.y;

    // Subsequent frames: bird should fall under gravity
    sb.update(0.016);
    assert(sb.bird.y > yAfterCeiling, 'After ceiling hit, bird falls normally under gravity');
    assertEqual(sb.gameState, 'PLAYING', 'Still PLAYING during normal fall from ceiling');

    sb.update(0.016);
    assert(sb.bird.y > yAfterCeiling, 'Bird continues falling away from ceiling');
})();


// ===========================================================
// 4. Ground Collision Still Triggers GAME_OVER (AC-2.5)
// ===========================================================

section('4. Ground Collision Still Triggers GAME_OVER (AC-2.5)');

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    // Place bird at ground: y + radius >= CANVAS_HEIGHT - GROUND_HEIGHT
    // 600 - 60 = 540. Bird needs y + 15 >= 540, so y >= 525
    sb.bird.y = 526;
    sb.bird.velocity = 100;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.001);

    assertEqual(sb.gameState, 'GAME_OVER', 'Ground collision triggers GAME_OVER');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 300;
    sb.bird.velocity = 600; // Max fall speed
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    // Run frames until ground hit
    let frames = 0;
    while (sb.gameState === 'PLAYING' && frames < 300) {
        sb.update(0.016);
        frames++;
    }

    assertEqual(sb.gameState, 'GAME_OVER', 'Falling bird eventually hits ground -> GAME_OVER');
    assert(frames > 0 && frames < 300, `Ground collision detected within ${frames} frames`);
    // Bird should be clamped to ground surface
    assertEqual(sb.bird.y, sb.CANVAS_HEIGHT - sb.GROUND_HEIGHT - sb.BIRD_RADIUS,
        'Bird clamped to ground surface (y = 525)');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 400;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    // Let bird fall naturally from y=400
    let frames = 0;
    while (sb.gameState === 'PLAYING' && frames < 500) {
        sb.update(0.016);
        frames++;
    }

    assertEqual(sb.gameState, 'GAME_OVER', 'Natural gravity fall to ground -> GAME_OVER');
})();


// ===========================================================
// 5. Pipe Collision Still Triggers GAME_OVER (AC-4.1)
// ===========================================================

section('5. Pipe Collision Still Triggers GAME_OVER (AC-4.1)');

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 50; // Near top, will hit top pipe
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 80, gapY: 200, scored: false }); // Top pipe from 0 to 200
    sb.distanceSinceLastPipe = 0;

    sb.update(0.001);

    assertEqual(sb.gameState, 'GAME_OVER', 'Top pipe collision triggers GAME_OVER');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    // Bottom pipe starts at gapY + PIPE_GAP = 200 + 130 = 330
    sb.bird.y = 400; // Inside bottom pipe area
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 80, gapY: 200, scored: false });
    sb.distanceSinceLastPipe = 0;

    sb.update(0.001);

    assertEqual(sb.gameState, 'GAME_OVER', 'Bottom pipe collision triggers GAME_OVER');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    // Bird safely in gap: gapY=200, gap ends at 330, bird at 265 (center of gap)
    sb.bird.y = 265;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.pipes.push({ x: 80, gapY: 200, scored: false });
    sb.distanceSinceLastPipe = 0;

    sb.update(0.001);

    assertEqual(sb.gameState, 'PLAYING', 'Bird safely through gap -> stays PLAYING');
})();


// ===========================================================
// 6. Rapid Flapping to Ceiling -- Stress Test
// ===========================================================

section('6. Rapid Flapping to Ceiling -- Stress Test');

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = sb.BIRD_START_Y; // Start at center (300)
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    // Simulate rapid flapping (every frame for 100 frames — enough to reach ceiling from y=300)
    // Each frame moves ~4.23px up, need ~68 frames from y=300 to reach ceiling at y=15
    for (let i = 0; i < 100; i++) {
        sb.flap(); // FLAP_VELOCITY = -280
        sb.updateBird(0.016);
    }

    assertEqual(sb.gameState, 'PLAYING', 'Rapid flapping for 100 frames -> still PLAYING');
    assert(sb.bird.y >= sb.BIRD_RADIUS, 'Bird never goes above canvas top after 100 rapid flaps');
    assertEqual(sb.bird.y, sb.BIRD_RADIUS, 'Bird is clamped at ceiling (y = BIRD_RADIUS)');
})();

(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = sb.BIRD_START_Y;
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    // Simulate rapid flapping via full update() cycle (includes collision checks)
    for (let i = 0; i < 60; i++) {
        sb.flap();
        sb.update(0.016);
        if (sb.gameState !== 'PLAYING') break;
    }

    assertEqual(sb.gameState, 'PLAYING', 'Full update() with rapid flapping -> still PLAYING (no false GAME_OVER)');
})();


// ===========================================================
// 7. Source-Level Verification
// ===========================================================

section('7. Source-Level Verification');

// Verify updateBird has ceiling clamp with velocity reset
(() => {
    const updateBirdFn = src.slice(
        src.indexOf('function updateBird('),
        src.indexOf('// ===== INPUT HANDLERS')
    );

    // Should clamp bird.y
    const hasBirdYClamp = updateBirdFn.includes('bird.y = bird.radius') ||
                           updateBirdFn.includes('bird.y = BIRD_RADIUS');
    assert(hasBirdYClamp, 'updateBird() clamps bird.y to bird.radius at ceiling');

    // Should reset velocity to 0
    const hasVelocityReset = updateBirdFn.includes('bird.velocity = 0');
    assert(hasVelocityReset, 'updateBird() resets bird.velocity to 0 at ceiling');
})();

// Verify checkCollisions does NOT have ceiling-related death
(() => {
    const checkCollisionsFn = src.slice(
        src.indexOf('function checkCollisions()'),
        src.indexOf('// ===== SCORING')
    );

    // Should NOT contain any ceiling check
    const hasCeilingDeath = checkCollisionsFn.includes('bird.y - bird.radius <= 0') ||
                             checkCollisionsFn.includes('bird.y - bird.radius < 0') ||
                             checkCollisionsFn.includes('bird.y <= 0') ||
                             checkCollisionsFn.includes('bird.y < bird.radius');
    assertEqual(hasCeilingDeath, false, 'checkCollisions() does NOT have ceiling death logic');
})();

// Verify there's only ONE place where ceiling is handled
(() => {
    const ceilingMatches = src.match(/bird\.y\s*-\s*bird\.radius\s*<\s*0/g) || [];
    const ceilingMatchesLE = src.match(/bird\.y\s*-\s*bird\.radius\s*<=\s*0/g) || [];
    const totalCeilingChecks = ceilingMatches.length + ceilingMatchesLE.length;

    assertEqual(totalCeilingChecks, 1, `Exactly 1 ceiling check in entire source (found ${totalCeilingChecks})`);
})();


// ===========================================================
// 8. Integration -- Full Play Cycle with Ceiling Interaction
// ===========================================================

section('8. Integration -- Full Play Cycle with Ceiling');

(() => {
    const sb = createSandbox();

    // 1. Start in IDLE
    assertEqual(sb.gameState, 'IDLE', '8a: Starts in IDLE');

    // 2. Transition to PLAYING
    sb.handleInput();
    assertEqual(sb.gameState, 'PLAYING', '8b: handleInput -> PLAYING');

    // 3. Flap to ceiling
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;
    for (let i = 0; i < 30; i++) {
        sb.flap();
        sb.update(0.016);
    }
    assertEqual(sb.gameState, 'PLAYING', '8c: Still PLAYING after reaching ceiling');
    assert(sb.bird.y >= sb.BIRD_RADIUS, '8d: Bird at or near ceiling');

    // 4. Stop flapping, let bird fall
    for (let i = 0; i < 10; i++) {
        sb.update(0.016);
    }
    assert(sb.bird.y > sb.BIRD_RADIUS, '8e: Bird falling away from ceiling');
    assertEqual(sb.gameState, 'PLAYING', '8f: Still PLAYING during fall');

    // 5. Let bird fall to ground
    let frames = 0;
    while (sb.gameState === 'PLAYING' && frames < 500) {
        sb.update(0.016);
        frames++;
    }
    assertEqual(sb.gameState, 'GAME_OVER', '8g: Eventually hits ground -> GAME_OVER');

    // 6. Reset
    sb.handleInput();
    assertEqual(sb.gameState, 'IDLE', '8h: Reset -> IDLE');
    assertEqual(sb.score, 0, '8i: Score reset to 0');
})();


// ===========================================================
// 9. Edge Cases
// ===========================================================

section('9. Edge Cases');

// Bird exactly at ceiling boundary (y - radius == 0)
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = sb.BIRD_RADIUS; // Exactly at ceiling boundary
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    sb.update(0.016);

    assertEqual(sb.gameState, 'PLAYING', 'Bird at exact ceiling boundary -> stays PLAYING');
})();

// Bird with extreme negative velocity
(() => {
    const sb = createSandbox();
    sb.bird.y = 50;
    sb.bird.velocity = -10000; // Extreme upward velocity

    sb.updateBird(0.016);

    assertEqual(sb.bird.y, sb.BIRD_RADIUS, 'Extreme upward velocity -> clamped at ceiling');
    assertEqual(sb.bird.velocity, 0, 'Extreme upward velocity -> reset to 0');
})();

// Multiple ceiling bounces in sequence
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = 50;
    sb.pipes.length = 0;
    sb.distanceSinceLastPipe = 0;

    for (let i = 0; i < 5; i++) {
        // Flap to ceiling
        sb.flap();
        sb.update(0.016);
        sb.flap();
        sb.update(0.016);
        sb.flap();
        sb.update(0.016);

        // Let fall a bit
        sb.update(0.016);
        sb.update(0.016);
    }

    assertEqual(sb.gameState, 'PLAYING', 'Multiple ceiling bounces -> still PLAYING');
})();

// Ceiling interaction near a pipe -- should not cause false collision
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = sb.BIRD_RADIUS; // At ceiling
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    // Pipe with gap starting at y=50 (top pipe from 0 to 50)
    // Bird at y=15, radius=15, so bird spans 0..30
    // Top pipe spans x=80..132, y=0..50
    // Bird at x=100, in x-range of pipe. Bird bottom edge=30 < pipe bottom=50 -> collision
    sb.pipes.push({ x: 80, gapY: 50, scored: false });
    sb.distanceSinceLastPipe = 0;

    sb.update(0.001);

    // This SHOULD collide because the bird at the ceiling overlaps with a very short top pipe
    assertEqual(sb.gameState, 'GAME_OVER', 'Bird at ceiling overlapping short top pipe -> GAME_OVER (pipe collision, not ceiling death)');
})();

// Ceiling interaction near a pipe with generous gap -- should NOT collide
(() => {
    const sb = createSandbox();
    sb.gameState = 'PLAYING';
    sb.bird.y = sb.BIRD_RADIUS; // At ceiling
    sb.bird.velocity = 0;
    sb.pipes.length = 0;
    // Pipe far to the right
    sb.pipes.push({ x: 300, gapY: 200, scored: false });
    sb.distanceSinceLastPipe = 0;

    sb.update(0.001);

    assertEqual(sb.gameState, 'PLAYING', 'Bird at ceiling with distant pipe -> stays PLAYING');
})();


// ===========================================================
// 10. Verify Constants (sanity checks)
// ===========================================================

section('10. Constants Sanity Check');

(() => {
    const sb = createSandbox();
    assertEqual(sb.CANVAS_HEIGHT, 600, 'CANVAS_HEIGHT = 600');
    assertEqual(sb.CANVAS_WIDTH, 400, 'CANVAS_WIDTH = 400');
    assertEqual(sb.GROUND_HEIGHT, 60, 'GROUND_HEIGHT = 60');
    assertEqual(sb.BIRD_RADIUS, 15, 'BIRD_RADIUS = 15');
    assertEqual(sb.BIRD_X, 100, 'BIRD_X = 100');
    assertEqual(sb.FLAP_VELOCITY, -280, 'FLAP_VELOCITY = -280');
    assertEqual(sb.GRAVITY, 980, 'GRAVITY = 980');
    assertEqual(sb.MAX_FALL_SPEED, 600, 'MAX_FALL_SPEED = 600');
})();


// ===========================================================
// SUMMARY
// ===========================================================

console.log('\n============================================================');
console.log(`  TS-029 CEILING COLLISION FIX QA: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('============================================================');

if (failures.length > 0) {
    console.log('\nFAILURES:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

if (bugs.length > 0) {
    console.log('\nBUGS FOUND:');
    bugs.forEach(b => {
        console.log(`  BUG-${b.id}: ${b.summary}`);
        console.log(`    Steps: ${b.steps}`);
        console.log(`    Expected: ${b.expected}`);
        console.log(`    Actual: ${b.actual}`);
    });
}

if (failed === 0) {
    console.log('\nQA VERDICT: CEILING COLLISION BUG FIX VERIFIED');
    console.log('  - AC-2.4: Bird y-position is clamped at canvas top (no death) -- PASS');
    console.log('  - AC-2.5: Bird hitting ground triggers GAME_OVER -- PASS');
    console.log('  - AC-4.1: Collision with pipe surface ends game -- PASS');
    console.log('  - No duplicate checkCeilingCollision function -- PASS');
    console.log('  - Velocity reset at ceiling -- PASS');
    console.log('  - No regressions in collision system -- PASS');
} else {
    console.log('\nQA VERDICT: ISSUES FOUND -- See failures above');
}

process.exit(failed > 0 ? 1 : 0);
