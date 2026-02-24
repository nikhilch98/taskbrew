/**
 * TS-004 — QA Verification: Input System (keyboard, mouse, touch) — CD-003
 * Automated behavioral test suite using Node.js (no external dependencies)
 *
 * Tests cover:
 *  1. Spacebar input — keydown triggers handleInput(), auto-repeat prevention
 *  2. Mouse input — mousedown on canvas triggers handleInput(), uses correct event
 *  3. Touch input — touchstart on canvas, preventDefault(), passive: false
 *  4. Cross-input consistency — all inputs route through handleInput()
 *  5. Input + state machine integration — correct transitions from all states
 *  6. Source code structural verification — no console errors path, correct binding
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

function section(title) {
    console.log(`\n━━━ ${title} ━━━`);
}

// ─── read source ───

const src = fs.readFileSync(path.join(__dirname, 'game.js'), 'utf8');

// ─── DOM stub with event capture ───

function createSandbox() {
    const listeners = {};
    const preventDefaultCalls = [];

    const domStub = `
        const _listeners = {};
        const _preventDefaultCalls = [];
        const document = {
            getElementById: (id) => ({
                getContext: () => ({
                    fillStyle: '',
                    fillRect: () => {},
                    strokeStyle: '',
                    lineWidth: 0,
                    stroke: () => {},
                    fill: () => {},
                    beginPath: () => {},
                    arc: () => {},
                    moveTo: () => {},
                    lineTo: () => {},
                    closePath: () => {},
                    ellipse: () => {},
                    save: () => {},
                    restore: () => {},
                    translate: () => {},
                    rotate: () => {},
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

        // Math polyfill (already available in Node, but just in case)
    `;

    const evalCode = `
        ${domStub}
        ${src}
        ({
            CANVAS_WIDTH, CANVAS_HEIGHT, GROUND_HEIGHT,
            BIRD_X, BIRD_RADIUS, BIRD_START_Y,
            GRAVITY, FLAP_VELOCITY, MAX_FALL_SPEED,
            STATE_IDLE, STATE_PLAYING, STATE_GAME_OVER,
            bird, pipes,
            get score() { return score; },
            set score(v) { score = v; },
            get gameState() { return gameState; },
            set gameState(v) { gameState = v; },
            get spacePressed() { return spacePressed; },
            set spacePressed(v) { spacePressed = v; },
            get bobTimer() { return bobTimer; },
            set bobTimer(v) { bobTimer = v; },
            get groundOffset() { return groundOffset; },
            set groundOffset(v) { groundOffset = v; },
            get lastTimestamp() { return lastTimestamp; },
            set lastTimestamp(v) { lastTimestamp = v; },
            handleInput, resetGame, flap,
            update, render, gameLoop,
            _listeners, _rafCallback
        })
    `;

    return eval(evalCode);
}

// Helper: create a mock keyboard event
function mockKeyEvent(code, opts = {}) {
    let defaultPrevented = false;
    return {
        code,
        preventDefault: () => { defaultPrevented = true; },
        get defaultPrevented() { return defaultPrevented; },
        repeat: opts.repeat || false,
        ...opts
    };
}

// Helper: create a mock mouse event
function mockMouseEvent(opts = {}) {
    let defaultPrevented = false;
    return {
        preventDefault: () => { defaultPrevented = true; },
        get defaultPrevented() { return defaultPrevented; },
        button: opts.button || 0,
        ...opts
    };
}

// Helper: create a mock touch event
function mockTouchEvent(opts = {}) {
    let defaultPrevented = false;
    return {
        preventDefault: () => { defaultPrevented = true; },
        get defaultPrevented() { return defaultPrevented; },
        touches: opts.touches || [{ clientX: 200, clientY: 300 }],
        ...opts
    };
}

// ═══════════════════════════════════════════════════════
// 1. SPACEBAR INPUT
// ═══════════════════════════════════════════════════════

section('1. Spacebar Input — Basic Behavior');

(() => {
    const sb = createSandbox();

    // 1a. Spacebar keydown fires handleInput when game is IDLE
    sb.gameState = 'IDLE';
    sb.spacePressed = false;

    const keydownHandler = sb._listeners['doc_keydown']?.fn;
    assert(typeof keydownHandler === 'function', 'keydown handler is a function');

    const spaceEvent = mockKeyEvent('Space');
    keydownHandler(spaceEvent);

    assertEqual(sb.gameState, 'PLAYING', 'Spacebar keydown transitions IDLE → PLAYING');
    assertEqual(sb.spacePressed, true, 'spacePressed flag set to true after keydown');
    assertEqual(sb.bird.velocity, -280, 'Spacebar triggers flap (velocity = FLAP_VELOCITY)');
})();

(() => {
    const sb = createSandbox();

    // 1b. Spacebar preventDefault is called (no page scroll)
    sb.gameState = 'IDLE';
    sb.spacePressed = false;
    const keydownHandler = sb._listeners['doc_keydown'].fn;
    const spaceEvent = mockKeyEvent('Space');
    keydownHandler(spaceEvent);
    assert(spaceEvent.defaultPrevented, 'Spacebar keydown calls preventDefault (prevents page scroll)');
})();

// ═══════════════════════════════════════════════════════
// 2. SPACEBAR AUTO-REPEAT PREVENTION
// ═══════════════════════════════════════════════════════

section('2. Spacebar — Auto-Repeat Prevention');

(() => {
    const sb = createSandbox();
    const keydownHandler = sb._listeners['doc_keydown'].fn;
    const keyupHandler = sb._listeners['doc_keyup'].fn;

    // Start fresh in PLAYING state (after first input)
    sb.gameState = 'PLAYING';
    sb.spacePressed = false;
    sb.bird.velocity = 100; // simulate falling

    // First press → should trigger flap
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.bird.velocity, -280, 'First spacebar press triggers flap');
    assertEqual(sb.spacePressed, true, 'spacePressed = true after first press');

    // Second keydown (held key / auto-repeat) → should NOT trigger another flap
    sb.bird.velocity = 50; // simulate some time passing
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.bird.velocity, 50, 'Held spacebar does NOT trigger second flap (auto-repeat blocked)');

    // Third keydown while held → still blocked
    sb.bird.velocity = 100;
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.bird.velocity, 100, 'Continued hold still blocked (3rd keydown ignored)');

    // Release key
    keyupHandler(mockKeyEvent('Space'));
    assertEqual(sb.spacePressed, false, 'spacePressed = false after keyup');

    // Press again after release → should trigger flap
    sb.bird.velocity = 200;
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.bird.velocity, -280, 'Spacebar press after release triggers flap again');
    assertEqual(sb.spacePressed, true, 'spacePressed = true after re-press');
})();

// ═══════════════════════════════════════════════════════
// 3. SPACEBAR — NON-SPACE KEYS IGNORED
// ═══════════════════════════════════════════════════════

section('3. Spacebar — Non-Space Keys Ignored');

(() => {
    const sb = createSandbox();
    const keydownHandler = sb._listeners['doc_keydown'].fn;

    sb.gameState = 'IDLE';
    sb.spacePressed = false;

    // Press Enter — should NOT trigger input
    const enterEvent = mockKeyEvent('Enter');
    keydownHandler(enterEvent);
    assertEqual(sb.gameState, 'IDLE', 'Enter key does not trigger state change');
    assertEqual(sb.spacePressed, false, 'spacePressed unchanged for non-Space key');

    // Press ArrowUp — should NOT trigger input
    keydownHandler(mockKeyEvent('ArrowUp'));
    assertEqual(sb.gameState, 'IDLE', 'ArrowUp does not trigger state change');

    // Press KeyW — should NOT trigger input
    keydownHandler(mockKeyEvent('KeyW'));
    assertEqual(sb.gameState, 'IDLE', 'KeyW does not trigger state change');
})();

// ═══════════════════════════════════════════════════════
// 4. MOUSE INPUT
// ═══════════════════════════════════════════════════════

section('4. Mouse Input — Canvas mousedown');

(() => {
    const sb = createSandbox();

    // Verify mousedown (not click) is the event used
    assert(sb._listeners['canvas_mousedown'] !== undefined, 'mousedown listener registered on canvas');
    assert(sb._listeners['canvas_click'] === undefined, 'click listener NOT registered (mousedown preferred)');

    const mousedownHandler = sb._listeners['canvas_mousedown'].fn;
    assert(typeof mousedownHandler === 'function', 'mousedown handler is a function');

    // mousedown transitions IDLE → PLAYING
    sb.gameState = 'IDLE';
    sb.bird.velocity = 0;
    mousedownHandler(mockMouseEvent());
    assertEqual(sb.gameState, 'PLAYING', 'mousedown on canvas transitions IDLE → PLAYING');
    assertEqual(sb.bird.velocity, -280, 'mousedown triggers flap');
})();

(() => {
    const sb = createSandbox();

    // mousedown calls preventDefault
    const mousedownHandler = sb._listeners['canvas_mousedown'].fn;
    const mouseEvent = mockMouseEvent();
    sb.gameState = 'PLAYING';
    sb.bird.velocity = 100;
    mousedownHandler(mouseEvent);
    assert(mouseEvent.defaultPrevented, 'mousedown calls preventDefault');
})();

// ═══════════════════════════════════════════════════════
// 5. MOUSE — Canvas-Only Scope (Source Analysis)
// ═══════════════════════════════════════════════════════

section('5. Mouse — Canvas-Only Scope');

(() => {
    // Verify mousedown is on canvas, not on document
    assert(
        src.includes("canvas.addEventListener('mousedown'"),
        'mousedown listener is on canvas (not document)'
    );

    // Verify there's no document-level mousedown listener
    assert(
        !src.includes("document.addEventListener('mousedown'"),
        'No document-level mousedown listener (clicks outside canvas ignored)'
    );
})();

// ═══════════════════════════════════════════════════════
// 6. TOUCH INPUT
// ═══════════════════════════════════════════════════════

section('6. Touch Input — Canvas touchstart');

(() => {
    const sb = createSandbox();

    // Verify touchstart listener exists
    assert(sb._listeners['canvas_touchstart'] !== undefined, 'touchstart listener registered on canvas');
    assert(typeof sb._listeners['canvas_touchstart'].fn === 'function', 'touchstart handler is a function');

    // Verify { passive: false } option
    const opts = sb._listeners['canvas_touchstart'].opts;
    assert(opts !== undefined, 'touchstart listener has options parameter');
    assertEqual(opts.passive, false, 'touchstart uses { passive: false }');

    // touchstart transitions IDLE → PLAYING
    const touchHandler = sb._listeners['canvas_touchstart'].fn;
    sb.gameState = 'IDLE';
    sb.bird.velocity = 0;
    const touchEvent = mockTouchEvent();
    touchHandler(touchEvent);
    assertEqual(sb.gameState, 'PLAYING', 'touchstart on canvas transitions IDLE → PLAYING');
    assertEqual(sb.bird.velocity, -280, 'touchstart triggers flap');
    assert(touchEvent.defaultPrevented, 'touchstart calls preventDefault (prevents scroll/zoom)');
})();

// ═══════════════════════════════════════════════════════
// 7. TOUCH — Source Code Verification
// ═══════════════════════════════════════════════════════

section('7. Touch — Source Code Verification');

(() => {
    // Verify touch listener uses canvas, not document
    assert(
        src.includes("canvas.addEventListener('touchstart'"),
        'touchstart listener is on canvas (not document)'
    );

    // Verify passive: false in source (line 135 area)
    assert(
        src.includes('{ passive: false }'),
        '{ passive: false } present in source code'
    );

    // Verify preventDefault in touchstart handler context
    // Extract the touchstart handler block
    const touchBlock = src.slice(
        src.indexOf("canvas.addEventListener('touchstart'"),
        src.indexOf('// ===== PIPE FUNCTIONS =====')
    );
    assert(
        touchBlock.includes('e.preventDefault()'),
        'preventDefault called inside touchstart handler'
    );
})();

// ═══════════════════════════════════════════════════════
// 8. CROSS-INPUT CONSISTENCY
// ═══════════════════════════════════════════════════════

section('8. Cross-Input Consistency — All inputs route through handleInput()');

(() => {
    // Verify all three handlers call handleInput() (source analysis)
    const keydownBlock = src.slice(
        src.indexOf("document.addEventListener('keydown'"),
        src.indexOf("document.addEventListener('keyup'")
    );
    assert(keydownBlock.includes('handleInput()'), 'keydown handler calls handleInput()');

    const mouseBlock = src.slice(
        src.indexOf("canvas.addEventListener('mousedown'"),
        src.indexOf("canvas.addEventListener('touchstart'")
    );
    assert(mouseBlock.includes('handleInput()'), 'mousedown handler calls handleInput()');

    const touchBlock = src.slice(
        src.indexOf("canvas.addEventListener('touchstart'"),
        src.indexOf('// ===== PIPE FUNCTIONS =====')
    );
    assert(touchBlock.includes('handleInput()'), 'touchstart handler calls handleInput()');
})();

(() => {
    // Behavioral test: all three inputs produce same game behavior
    const inputs = [
        { name: 'spacebar', trigger: (sb) => sb._listeners['doc_keydown'].fn(mockKeyEvent('Space')) },
        { name: 'mouse', trigger: (sb) => sb._listeners['canvas_mousedown'].fn(mockMouseEvent()) },
        { name: 'touch', trigger: (sb) => sb._listeners['canvas_touchstart'].fn(mockTouchEvent()) },
    ];

    for (const input of inputs) {
        const sb = createSandbox();
        sb.gameState = 'IDLE';
        sb.bird.velocity = 0;
        sb.spacePressed = false;

        input.trigger(sb);

        assertEqual(sb.gameState, 'PLAYING', `${input.name}: IDLE → PLAYING`);
        assertEqual(sb.bird.velocity, -280, `${input.name}: triggers flap (velocity = -280)`);
    }
})();

// ═══════════════════════════════════════════════════════
// 9. MIXED INPUT SEQUENCES
// ═══════════════════════════════════════════════════════

section('9. Mixed Input Sequences');

(() => {
    const sb = createSandbox();
    const keydownHandler = sb._listeners['doc_keydown'].fn;
    const keyupHandler = sb._listeners['doc_keyup'].fn;
    const mousedownHandler = sb._listeners['canvas_mousedown'].fn;
    const touchHandler = sb._listeners['canvas_touchstart'].fn;

    // Start in IDLE
    sb.gameState = 'IDLE';
    sb.spacePressed = false;
    sb.bird.velocity = 0;

    // Tap screen → PLAYING
    touchHandler(mockTouchEvent());
    assertEqual(sb.gameState, 'PLAYING', 'Touch starts game from IDLE');

    // Now use spacebar for a flap
    sb.bird.velocity = 100;
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.bird.velocity, -280, 'Spacebar flap works after touch start');

    // Release space, then click mouse for another flap
    keyupHandler(mockKeyEvent('Space'));
    sb.bird.velocity = 150;
    mousedownHandler(mockMouseEvent());
    assertEqual(sb.bird.velocity, -280, 'Mouse flap works after spacebar input');

    // Touch again for another flap
    sb.bird.velocity = 200;
    touchHandler(mockTouchEvent());
    assertEqual(sb.bird.velocity, -280, 'Touch flap works after mouse input');
})();

// ═══════════════════════════════════════════════════════
// 10. INPUT IN EACH GAME STATE
// ═══════════════════════════════════════════════════════

section('10. Input in Each Game State');

(() => {
    // Test each input type in each game state
    const inputMethods = [
        { name: 'spacebar', trigger: (sb) => { sb.spacePressed = false; sb._listeners['doc_keydown'].fn(mockKeyEvent('Space')); } },
        { name: 'mouse', trigger: (sb) => sb._listeners['canvas_mousedown'].fn(mockMouseEvent()) },
        { name: 'touch', trigger: (sb) => sb._listeners['canvas_touchstart'].fn(mockTouchEvent()) },
    ];

    for (const input of inputMethods) {
        // STATE: IDLE → PLAYING
        const sb1 = createSandbox();
        sb1.gameState = 'IDLE';
        sb1.bird.velocity = 0;
        input.trigger(sb1);
        assertEqual(sb1.gameState, 'PLAYING', `${input.name} in IDLE → PLAYING`);

        // STATE: PLAYING → stays PLAYING (flap)
        const sb2 = createSandbox();
        sb2.gameState = 'PLAYING';
        sb2.bird.velocity = 100;
        input.trigger(sb2);
        assertEqual(sb2.gameState, 'PLAYING', `${input.name} in PLAYING → stays PLAYING`);
        assertEqual(sb2.bird.velocity, -280, `${input.name} in PLAYING → triggers flap`);

        // STATE: GAME_OVER → IDLE (reset)
        const sb3 = createSandbox();
        sb3.gameState = 'GAME_OVER';
        sb3.bird.y = 500;
        sb3.bird.velocity = 200;
        sb3.score = 42;
        input.trigger(sb3);
        assertEqual(sb3.gameState, 'IDLE', `${input.name} in GAME_OVER → IDLE (reset)`);
        assertEqual(sb3.score, 0, `${input.name} in GAME_OVER → score reset to 0`);
        assertEqual(sb3.bird.y, 300, `${input.name} in GAME_OVER → bird.y reset`);
    }
})();

// ═══════════════════════════════════════════════════════
// 11. SPACEBAR FLAG ISOLATION
// ═══════════════════════════════════════════════════════

section('11. spacePressed Flag Isolation');

(() => {
    const sb = createSandbox();
    const keydownHandler = sb._listeners['doc_keydown'].fn;
    const keyupHandler = sb._listeners['doc_keyup'].fn;
    const mousedownHandler = sb._listeners['canvas_mousedown'].fn;
    const touchHandler = sb._listeners['canvas_touchstart'].fn;

    // Hold spacebar
    sb.gameState = 'PLAYING';
    sb.spacePressed = false;
    sb.bird.velocity = 100;
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.spacePressed, true, 'spacePressed = true while space held');

    // Mouse click should NOT be blocked by spacePressed
    sb.bird.velocity = 100;
    mousedownHandler(mockMouseEvent());
    assertEqual(sb.bird.velocity, -280, 'Mouse click works even when spacebar is held down');

    // Touch should NOT be blocked by spacePressed
    sb.bird.velocity = 100;
    touchHandler(mockTouchEvent());
    assertEqual(sb.bird.velocity, -280, 'Touch works even when spacebar is held down');

    // spacePressed flag should not be affected by mouse/touch
    assertEqual(sb.spacePressed, true, 'spacePressed flag not cleared by mouse/touch input');
})();

// ═══════════════════════════════════════════════════════
// 12. KEYBOARD — keyup for non-Space key
// ═══════════════════════════════════════════════════════

section('12. Keyboard — keyup for non-Space key');

(() => {
    const sb = createSandbox();
    const keydownHandler = sb._listeners['doc_keydown'].fn;
    const keyupHandler = sb._listeners['doc_keyup'].fn;

    // Press space (sets flag)
    sb.gameState = 'PLAYING';
    sb.spacePressed = false;
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.spacePressed, true, 'Space pressed');

    // Release Enter (not Space) — spacePressed should NOT be cleared
    keyupHandler(mockKeyEvent('Enter'));
    assertEqual(sb.spacePressed, true, 'keyup of Enter does NOT clear spacePressed');

    // Release ArrowUp — spacePressed should NOT be cleared
    keyupHandler(mockKeyEvent('ArrowUp'));
    assertEqual(sb.spacePressed, true, 'keyup of ArrowUp does NOT clear spacePressed');

    // Release Space — spacePressed should be cleared
    keyupHandler(mockKeyEvent('Space'));
    assertEqual(sb.spacePressed, false, 'keyup of Space DOES clear spacePressed');
})();

// ═══════════════════════════════════════════════════════
// 13. SOURCE STRUCTURE — LISTENER REGISTRATION
// ═══════════════════════════════════════════════════════

section('13. Source Structure — Listener Registration');

(() => {
    // Keyboard: keydown on document (broad capture)
    assert(
        /document\.addEventListener\s*\(\s*['"]keydown['"]/.test(src),
        'keydown registered on document (broad capture)'
    );

    // Keyboard: keyup on document
    assert(
        /document\.addEventListener\s*\(\s*['"]keyup['"]/.test(src),
        'keyup registered on document'
    );

    // Mouse: mousedown on canvas (NOT click)
    assert(
        /canvas\.addEventListener\s*\(\s*['"]mousedown['"]/.test(src),
        'mousedown registered on canvas'
    );
    assert(
        !/canvas\.addEventListener\s*\(\s*['"]click['"]/.test(src),
        'No click listener on canvas (mousedown fires on press, not release)'
    );

    // Touch: touchstart on canvas
    assert(
        /canvas\.addEventListener\s*\(\s*['"]touchstart['"]/.test(src),
        'touchstart registered on canvas'
    );

    // No mouseup or touchend handlers (not needed for this game)
    assert(
        !/canvas\.addEventListener\s*\(\s*['"]mouseup['"]/.test(src),
        'No mouseup listener (not needed)'
    );
})();

// ═══════════════════════════════════════════════════════
// 14. PREVENT DEFAULT — SPACEBAR
// ═══════════════════════════════════════════════════════

section('14. preventDefault — Spacebar Scroll Prevention');

(() => {
    const sb = createSandbox();
    const keydownHandler = sb._listeners['doc_keydown'].fn;

    // preventDefault should be called even when spacePressed is already true
    sb.spacePressed = true; // simulate held key
    const event1 = mockKeyEvent('Space');
    keydownHandler(event1);
    assert(event1.defaultPrevented, 'preventDefault called even on repeated spacebar (prevents scroll regardless)');

    // preventDefault should NOT be called for non-Space keys
    const enterEvent = mockKeyEvent('Enter');
    keydownHandler(enterEvent);
    assert(!enterEvent.defaultPrevented, 'preventDefault NOT called for Enter key');
})();

// ═══════════════════════════════════════════════════════
// 15. EDGE CASE: Rapid Input Switching
// ═══════════════════════════════════════════════════════

section('15. Edge Case — Rapid Input Switching');

(() => {
    const sb = createSandbox();
    const keydownHandler = sb._listeners['doc_keydown'].fn;
    const keyupHandler = sb._listeners['doc_keyup'].fn;
    const mousedownHandler = sb._listeners['canvas_mousedown'].fn;
    const touchHandler = sb._listeners['canvas_touchstart'].fn;

    sb.gameState = 'PLAYING';
    sb.spacePressed = false;

    // Rapid sequence: space, mouse, touch, space (release), space
    sb.bird.velocity = 100;
    keydownHandler(mockKeyEvent('Space')); // flap #1
    assertEqual(sb.bird.velocity, -280, 'Rapid #1: spacebar flap');

    sb.bird.velocity = 100;
    mousedownHandler(mockMouseEvent()); // flap #2
    assertEqual(sb.bird.velocity, -280, 'Rapid #2: mouse flap');

    sb.bird.velocity = 100;
    touchHandler(mockTouchEvent()); // flap #3
    assertEqual(sb.bird.velocity, -280, 'Rapid #3: touch flap');

    // Space is still held — shouldn't flap
    sb.bird.velocity = 100;
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.bird.velocity, 100, 'Rapid #4: spacebar blocked (still held)');

    // Release and re-press
    keyupHandler(mockKeyEvent('Space'));
    sb.bird.velocity = 100;
    keydownHandler(mockKeyEvent('Space'));
    assertEqual(sb.bird.velocity, -280, 'Rapid #5: spacebar flap after release');
})();

// ═══════════════════════════════════════════════════════
// 16. GAME_OVER RESET THEN IMMEDIATE INPUT
// ═══════════════════════════════════════════════════════

section('16. GAME_OVER → Reset → Immediate Re-Input');

(() => {
    const sb = createSandbox();
    const mousedownHandler = sb._listeners['canvas_mousedown'].fn;

    // In GAME_OVER state
    sb.gameState = 'GAME_OVER';
    sb.bird.y = 500;
    sb.bird.velocity = 300;
    sb.score = 15;

    // First click: resets game to IDLE
    mousedownHandler(mockMouseEvent());
    assertEqual(sb.gameState, 'IDLE', 'First click in GAME_OVER → IDLE');
    assertEqual(sb.score, 0, 'Score reset to 0');

    // Second click: starts playing
    mousedownHandler(mockMouseEvent());
    assertEqual(sb.gameState, 'PLAYING', 'Second click after reset → PLAYING');
    assertEqual(sb.bird.velocity, -280, 'Second click triggers first flap');
})();

// ═══════════════════════════════════════════════════════
// 17. NO DOCUMENT-LEVEL MOUSE/TOUCH HANDLERS
// ═══════════════════════════════════════════════════════

section('17. No Document-Level Mouse/Touch Handlers');

(() => {
    const sb = createSandbox();

    // Only document-level listeners should be keydown and keyup
    const docListeners = Object.keys(sb._listeners).filter(k => k.startsWith('doc_'));
    assertEqual(docListeners.length, 2, 'Exactly 2 document-level listeners (keydown, keyup)');
    assert(docListeners.includes('doc_keydown'), 'Document has keydown listener');
    assert(docListeners.includes('doc_keyup'), 'Document has keyup listener');

    // Canvas listeners should be mousedown and touchstart
    const canvasListeners = Object.keys(sb._listeners).filter(k => k.startsWith('canvas_'));
    assertEqual(canvasListeners.length, 2, 'Exactly 2 canvas-level listeners (mousedown, touchstart)');
    assert(canvasListeners.includes('canvas_mousedown'), 'Canvas has mousedown listener');
    assert(canvasListeners.includes('canvas_touchstart'), 'Canvas has touchstart listener');
})();

// ═══════════════════════════════════════════════════════
// 18. spacePressed INITIAL STATE
// ═══════════════════════════════════════════════════════

section('18. spacePressed Initial State');

(() => {
    // Source analysis: spacePressed declared with let, initialized to false
    assert(/let\s+spacePressed\s*=\s*false/.test(src), 'spacePressed declared as let = false');

    // Runtime
    const sb = createSandbox();
    assertEqual(sb.spacePressed, false, 'spacePressed starts as false at runtime');
})();

// ═══════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════

console.log('\n═══════════════════════════════════════════');
console.log(`  TS-004 INPUT SYSTEM RESULTS: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log('═══════════════════════════════════════════');

if (failures.length > 0) {
    console.log('\nFailed tests:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}

process.exit(failed > 0 ? 1 : 0);
