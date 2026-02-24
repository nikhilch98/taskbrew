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

function updateBird(dt) {
    // Apply gravity
    bird.velocity += GRAVITY * dt;

    // Cap fall speed (terminal velocity)
    if (bird.velocity > MAX_FALL_SPEED) {
        bird.velocity = MAX_FALL_SPEED;
    }

    // Update position
    bird.y += bird.velocity * dt;

    // Clamp to top of canvas (bird can't fly above screen)
    if (bird.y - bird.radius < 0) {
        bird.y = bird.radius;
        bird.velocity = 0; // Stop upward movement at ceiling
    }

    // Update rotation based on velocity
    // Map velocity range [-280, 600] to rotation range [-0.52rad (-30deg), 1.57rad (90deg)]
    bird.rotation = Math.min(
        Math.max(bird.velocity / MAX_FALL_SPEED * (Math.PI / 2), -Math.PI / 6),
        Math.PI / 2
    );
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

// ===== PIPE FUNCTIONS =====

/**
 * Determine if a new pipe should be spawned.
 * Distance-based: spawn when the rightmost pipe has scrolled far enough
 * that the next pipe would appear at the right edge of the canvas.
 */
function shouldSpawnPipe() {
    if (pipes.length === 0) {
        return true; // Spawn first pipe immediately when entering PLAYING
    }
    const lastPipe = pipes[pipes.length - 1];
    // Spawn when rightmost pipe has scrolled far enough
    return lastPipe.x <= CANVAS_WIDTH - PIPE_SPACING;
}

/**
 * Create a new pipe pair at the right edge of the canvas
 * with a randomized gap position within safe bounds.
 */
function spawnPipe() {
    // Random gap position, constrained to safe bounds
    // gapY is the TOP of the gap
    const gapY = PIPE_MIN_TOP + Math.random() * (PIPE_MAX_TOP - PIPE_MIN_TOP);

    pipes.push({
        x: CANVAS_WIDTH,   // Spawn at right edge (400px)
        gapY: gapY,        // Random gap top position within [50, 360]
        scored: false       // Not yet passed by bird
    });
}

/**
 * Update all pipes: move left, spawn new ones, cleanup off-screen.
 * @param {number} dt - Delta time in seconds
 */
function updatePipes(dt) {
    // 1. Move all pipes left
    for (let i = 0; i < pipes.length; i++) {
        pipes[i].x -= PIPE_SPEED * dt;
    }

    // 2. Spawn new pipe if needed
    if (shouldSpawnPipe()) {
        spawnPipe();
    }

    // 3. Cleanup: remove pipes that are fully off-screen left
    //    Using shift from front since pipes are ordered left-to-right
    while (pipes.length > 0 && pipes[0].x + PIPE_WIDTH < 0) {
        pipes.shift();
    }
}

/**
 * Render all pipe pairs with body and cap details.
 * Pipes are green rectangles with darker green caps at gap edges.
 * @param {CanvasRenderingContext2D} ctx - Canvas rendering context
 */
function renderPipes(ctx) {
    const groundY = CANVAS_HEIGHT - GROUND_HEIGHT;

    for (let i = 0; i < pipes.length; i++) {
        const pipe = pipes[i];
        const bottomPipeTop = pipe.gapY + PIPE_GAP;

        // Pipe body color
        ctx.fillStyle = '#3cb043';  // Green

        // Top pipe: from top of canvas down to gap
        ctx.fillRect(pipe.x, 0, PIPE_WIDTH, pipe.gapY);

        // Bottom pipe: from bottom of gap down to ground level
        ctx.fillRect(pipe.x, bottomPipeTop, PIPE_WIDTH, groundY - bottomPipeTop);

        // Pipe caps (darker green, slightly wider) for visual polish
        ctx.fillStyle = '#2d8a34'; // Darker green

        // Top pipe cap (at bottom of top pipe)
        ctx.fillRect(
            pipe.x - PIPE_CAP_OVERHANG,
            pipe.gapY - PIPE_CAP_HEIGHT,
            PIPE_WIDTH + PIPE_CAP_OVERHANG * 2,
            PIPE_CAP_HEIGHT
        );

        // Bottom pipe cap (at top of bottom pipe)
        ctx.fillRect(
            pipe.x - PIPE_CAP_OVERHANG,
            bottomPipeTop,
            PIPE_WIDTH + PIPE_CAP_OVERHANG * 2,
            PIPE_CAP_HEIGHT
        );
    }
}

// ===== UPDATE LOGIC =====

function update(dt) {
    switch (gameState) {
        case STATE_IDLE:
            // Bob animation — bird oscillates up/down at 2Hz, 8px amplitude
            bobTimer += dt;
            bird.y = BIRD_START_Y + Math.sin(bobTimer * BOB_FREQUENCY * Math.PI * 2) * BOB_AMPLITUDE;
            // Ground scrolling (matches pipe speed for visual consistency)
            groundOffset = (groundOffset + PIPE_SPEED * dt) % 24;
            break;

        case STATE_PLAYING:
            updateBird(dt);
            updatePipes(dt);
            // Ground scrolling continues during play
            groundOffset = (groundOffset + PIPE_SPEED * dt) % 24;
            break;

        case STATE_GAME_OVER:
            // No updates — everything frozen
            break;
    }
}

// ===== RENDER LOGIC =====

function renderBird(ctx) {
    ctx.save();

    // Translate to bird center, rotate, then draw at origin
    ctx.translate(bird.x, bird.y);
    ctx.rotate(bird.rotation);

    // Body — yellow/orange filled circle
    ctx.fillStyle = '#f5c842';  // Golden yellow
    ctx.beginPath();
    ctx.arc(0, 0, bird.radius, 0, Math.PI * 2);
    ctx.fill();

    // Body outline
    ctx.strokeStyle = '#d4a020';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Eye — small white circle with black pupil
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(6, -5, 4, 0, Math.PI * 2);  // Offset right and up from center
    ctx.fill();

    ctx.fillStyle = '#000000';
    ctx.beginPath();
    ctx.arc(7, -5, 2, 0, Math.PI * 2);  // Pupil
    ctx.fill();

    // Beak — small orange triangle
    ctx.fillStyle = '#e07020';
    ctx.beginPath();
    ctx.moveTo(bird.radius, -3);
    ctx.lineTo(bird.radius + 8, 0);
    ctx.lineTo(bird.radius, 3);
    ctx.closePath();
    ctx.fill();

    // Wing — small ellipse on the body
    ctx.fillStyle = '#e0b030';
    ctx.beginPath();
    ctx.ellipse(-2, 3, 8, 5, -0.3, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
}

function render(ctx) {
    // Layer 0: Fill canvas with sky blue background (doubles as canvas clear)
    ctx.fillStyle = '#70c5ce';
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);

    // Layer 1: Pipes (only in PLAYING and GAME_OVER — NOT in IDLE)
    if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) {
        renderPipes(ctx);
    }

    // Layer 2: Ground will be rendered here (by future task)

    // Layer 3: Bird — always on top of game elements
    renderBird(ctx);
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
