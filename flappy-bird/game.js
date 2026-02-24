// ===== CONSTANTS =====

// Canvas dimensions
const CANVAS_WIDTH    = 400;    // px
const CANVAS_HEIGHT   = 600;    // px

// Ground
const GROUND_HEIGHT   = 60;     // px — height of ground strip at bottom

// Bird
const BIRD_X          = 100;    // px — fixed horizontal position (25% of canvas width)
const BIRD_RADIUS     = 15;     // px — collision and visual radius
const BIRD_START_Y    = CANVAS_HEIGHT / 2;  // px — initial vertical position (300px)
const GRAVITY         = 980;    // px/s^2 — downward acceleration
const FLAP_VELOCITY   = -280;   // px/s — upward impulse (negative = up)
const MAX_FALL_SPEED  = 600;    // px/s — terminal velocity cap

// Pipes
const PIPE_WIDTH      = 52;     // px — width of each pipe column
const PIPE_GAP        = 130;    // px — vertical gap between top and bottom pipes
const PIPE_SPEED      = 120;    // px/s — horizontal movement speed (leftward)
const PIPE_SPACING    = 220;    // px — horizontal distance between consecutive pipe pairs
const PIPE_MIN_TOP    = 50;     // px — minimum height of top pipe (ensures visibility)
const PIPE_MAX_TOP    = CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50; // = 360px

// Idle animation
const BOB_AMPLITUDE   = 8;      // px — vertical bob range on start screen
const BOB_FREQUENCY   = 2;      // Hz — bob oscillation speed

// Pipe cap (visual polish)
const PIPE_CAP_HEIGHT    = 20;  // px
const PIPE_CAP_OVERHANG  = 3;   // px — extra width on each side

// ===== STATE CONSTANTS =====

const STATE_IDLE      = 'IDLE';
const STATE_PLAYING   = 'PLAYING';
const STATE_GAME_OVER = 'GAME_OVER';

// ===== CANVAS INITIALIZATION =====

const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');

// ===== GAME STATE VARIABLES =====

let bird = {
    x: BIRD_X,
    y: BIRD_START_Y,
    velocity: 0,
    radius: BIRD_RADIUS,
    rotation: 0
};

let pipes = [];
let score = 0;
let bobTimer = 0;
let groundOffset = 0;
let gameState = STATE_IDLE;
let lastTimestamp = 0;
let spacePressed = false;

// ===== STATE MACHINE =====

function handleInput() {
    switch (gameState) {
        case STATE_IDLE:
            gameState = STATE_PLAYING;
            flap();      // Immediate first flap so bird doesn't just drop
            break;
        case STATE_PLAYING:
            flap();
            break;
        case STATE_GAME_OVER:
            resetGame(); // Full reset, then state becomes IDLE
            break;
    }
}

function resetGame() {
    // State
    gameState = STATE_IDLE;

    // Bird
    bird.y        = BIRD_START_Y;    // Reset to vertical center
    bird.velocity = 0;               // No vertical motion
    bird.rotation = 0;               // Level orientation

    // Pipes
    pipes.length  = 0;               // Clear all pipes (empties array in-place)

    // Score
    score         = 0;               // Reset score counter

    // Timing
    bobTimer      = 0;               // Reset idle bob animation phase

    // Ground
    groundOffset  = 0;               // Reset ground scroll position
}

function flap() {
    bird.velocity = FLAP_VELOCITY;   // Set (not add) upward impulse
}

// ===== INPUT HANDLERS =====

// Keyboard — on document for broad capture
document.addEventListener('keydown', function(e) {
    if (e.code === 'Space') {
        e.preventDefault(); // Prevent page scroll
        if (!spacePressed) {
            spacePressed = true;
            handleInput();
        }
    }
});

document.addEventListener('keyup', function(e) {
    if (e.code === 'Space') {
        spacePressed = false;
    }
});

// Mouse — on canvas only
canvas.addEventListener('mousedown', function(e) {
    e.preventDefault();
    handleInput();
});

// Touch — on canvas with preventDefault for mobile
canvas.addEventListener('touchstart', function(e) {
    e.preventDefault(); // Prevents scroll, zoom, and double-tap-to-zoom
    handleInput();
}, { passive: false }); // passive: false required to allow preventDefault

// ===== UPDATE LOGIC =====

function update(dt) {
    switch (gameState) {
        case STATE_IDLE:
            // Placeholder: bob animation and ground scroll
            bobTimer += dt;
            bird.y = BIRD_START_Y + Math.sin(bobTimer * BOB_FREQUENCY * Math.PI * 2) * BOB_AMPLITUDE;
            break;

        case STATE_PLAYING:
            // Placeholder: full game update logic will go here
            break;

        case STATE_GAME_OVER:
            // No updates — everything frozen
            break;
    }
}

// ===== RENDER LOGIC =====

function render(ctx) {
    // Fill canvas with sky blue background (doubles as canvas clear)
    ctx.fillStyle = '#70c5ce';
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
}

// ===== GAME LOOP =====

function gameLoop(timestamp) {
    // 1. Compute delta-time in seconds
    if (lastTimestamp === 0) {
        lastTimestamp = timestamp;
    }
    let dt = (timestamp - lastTimestamp) / 1000; // ms -> seconds
    lastTimestamp = timestamp;

    // 2. Cap delta-time to prevent physics explosion on tab-refocus
    //    Max 50ms (0.05s) — equivalent to 20fps minimum simulation rate
    if (dt > 0.05) {
        dt = 0.05;
    }

    // 3. Update phase — all game logic
    update(dt);

    // 4. Render phase — all drawing
    render(ctx);

    // 5. Schedule next frame
    requestAnimationFrame(gameLoop);
}

// Kick off the loop
requestAnimationFrame(gameLoop);
