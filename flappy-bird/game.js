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

// Pipe timing
const FIRST_PIPE_DELAY   = 60;  // px — scrolling distance before first pipe spawns

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
let distanceSinceLastPipe = 0;
let gameState = STATE_IDLE;
let lastTimestamp = 0;
let spacePressed = false;

// ===== STATE MACHINE =====

function handleInput() {
    switch (gameState) {
        case STATE_IDLE:
            distanceSinceLastPipe = PIPE_SPACING - FIRST_PIPE_DELAY; // Seed so first pipe appears shortly after start
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

    // Pipe timing
    distanceSinceLastPipe = 0;       // Reset pipe spawn distance tracker
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
 * Uses distanceSinceLastPipe accumulator for spawn timing.
 * @param {number} dt - Delta time in seconds
 */
function updatePipes(dt) {
    // 1. Move all pipes left
    for (let i = 0; i < pipes.length; i++) {
        pipes[i].x -= PIPE_SPEED * dt;
    }

    // 2. Spawn new pipe based on accumulated distance
    distanceSinceLastPipe += PIPE_SPEED * dt;
    if (distanceSinceLastPipe >= PIPE_SPACING) {
        distanceSinceLastPipe -= PIPE_SPACING; // Preserve remainder for consistent spacing
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
        ctx.fillStyle = '#2ECC71';  // Green

        // Top pipe: from top of canvas down to gap
        ctx.fillRect(pipe.x, 0, PIPE_WIDTH, pipe.gapY);

        // Bottom pipe: from bottom of gap down to ground level
        ctx.fillRect(pipe.x, bottomPipeTop, PIPE_WIDTH, groundY - bottomPipeTop);

        // Pipe caps (darker green, slightly wider) for visual polish
        ctx.fillStyle = '#27AE60'; // Darker green

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

// ===== COLLISION DETECTION =====

/**
 * Check if a circle overlaps with an axis-aligned rectangle.
 * Uses closest-point-on-rect to circle-center distance check.
 * @param {number} cx - Circle center X
 * @param {number} cy - Circle center Y
 * @param {number} cr - Circle radius
 * @param {number} rx - Rectangle left X
 * @param {number} ry - Rectangle top Y
 * @param {number} rw - Rectangle width
 * @param {number} rh - Rectangle height
 * @returns {boolean} True if circle and rectangle overlap
 */
function circleRectCollision(cx, cy, cr, rx, ry, rw, rh) {
    // Find closest point on rectangle to circle center
    let closestX = Math.max(rx, Math.min(cx, rx + rw));
    let closestY = Math.max(ry, Math.min(cy, ry + rh));
    let dx = cx - closestX;
    let dy = cy - closestY;
    return (dx * dx + dy * dy) < (cr * cr);
}

/**
 * Check if the bird collides with ground, ceiling, or any pipe.
 * @returns {boolean} True if any collision is detected
 */
function checkCollision() {
    // Ground collision
    if (bird.y + bird.radius >= CANVAS_HEIGHT - GROUND_HEIGHT) return true;

    // Ceiling collision
    if (bird.y - bird.radius <= 0) return true;

    // Pipe collision (circle vs rectangle for each pipe pair)
    for (let i = 0; i < pipes.length; i++) {
        let p = pipes[i];

        // Top pipe rect: from canvas top to gap top
        if (circleRectCollision(bird.x, bird.y, bird.radius,
            p.x, 0, PIPE_WIDTH, p.gapY)) return true;

        // Bottom pipe rect: from gap bottom to ground top
        let bottomY = p.gapY + PIPE_GAP;
        let bottomH = CANVAS_HEIGHT - GROUND_HEIGHT - bottomY;
        if (circleRectCollision(bird.x, bird.y, bird.radius,
            p.x, bottomY, PIPE_WIDTH, bottomH)) return true;
    }

    return false;
}

// ===== SCORING =====

/**
 * Check all pipes for scoring — bird passing the pipe's center line.
 * Each pipe can only be scored once (tracked by pipe.scored flag).
 */
function updateScore() {
    for (let i = 0; i < pipes.length; i++) {
        if (!pipes[i].scored && pipes[i].x + PIPE_WIDTH / 2 < bird.x) {
            pipes[i].scored = true;
            score++;
        }
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
            // 1. Bird physics (gravity, velocity cap, position, rotation)
            updateBird(dt);

            // 2. Ground scrolling continues during play
            groundOffset = (groundOffset + PIPE_SPEED * dt) % 24;

            // 3. Pipe spawning, movement & cleanup
            updatePipes(dt);

            // 4. Scoring — check if bird passed pipe center
            updateScore();

            // 5. Collision detection → GAME_OVER transition
            if (checkCollision()) {
                gameState = STATE_GAME_OVER;
                // Clamp bird to ground if it hit ground (prevents sinking through)
                if (bird.y + bird.radius >= CANVAS_HEIGHT - GROUND_HEIGHT) {
                    bird.y = CANVAS_HEIGHT - GROUND_HEIGHT - bird.radius;
                }
            }
            break;

        case STATE_GAME_OVER:
            // No updates — everything frozen
            break;
    }
}

// ===== RENDER LOGIC =====

function renderGround(ctx) {
    // Brown dirt strip
    ctx.fillStyle = '#8B5E3C';
    ctx.fillRect(0, CANVAS_HEIGHT - GROUND_HEIGHT, CANVAS_WIDTH, GROUND_HEIGHT);

    // Green grass accent at top edge of ground (~4px tall)
    ctx.fillStyle = '#5CBF2A';
    ctx.fillRect(0, CANVAS_HEIGHT - GROUND_HEIGHT, CANVAS_WIDTH, 4);

    // Scrolling vertical hash lines for ground texture
    ctx.strokeStyle = '#7A5232';
    ctx.lineWidth = 1;
    for (var x = -groundOffset % 24; x < CANVAS_WIDTH; x += 24) {
        ctx.beginPath();
        ctx.moveTo(x, CANVAS_HEIGHT - GROUND_HEIGHT + 10);
        ctx.lineTo(x, CANVAS_HEIGHT - 5);
        ctx.stroke();
    }
}

function renderBird(ctx) {
    ctx.save();
    ctx.translate(bird.x, bird.y);
    ctx.rotate(bird.rotation);

    // Body — yellow circle
    ctx.fillStyle = '#F7DC6F';
    ctx.beginPath();
    ctx.arc(0, 0, bird.radius, 0, Math.PI * 2);
    ctx.fill();

    // Outline
    ctx.strokeStyle = '#D4A017';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Eye — white circle (radius 5px, offset ~(5, -5) from center)
    ctx.fillStyle = '#FFFFFF';
    ctx.beginPath();
    ctx.arc(5, -5, 5, 0, Math.PI * 2);
    ctx.fill();

    // Pupil — black circle (radius 2.5px, offset slightly right)
    ctx.fillStyle = '#000000';
    ctx.beginPath();
    ctx.arc(7, -5, 2.5, 0, Math.PI * 2);
    ctx.fill();

    // Beak — orange triangle protruding from right side (~8px wide)
    ctx.fillStyle = '#E67E22';
    ctx.beginPath();
    ctx.moveTo(bird.radius, -4);
    ctx.lineTo(bird.radius + 8, 0);
    ctx.lineTo(bird.radius, 4);
    ctx.closePath();
    ctx.fill();

    ctx.restore();
}

function renderScore(ctx) {
    if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) {
        ctx.save();
        ctx.fillStyle = '#FFFFFF';
        ctx.font = 'bold 48px Arial';
        ctx.textAlign = 'center';
        // Black outline for readability against sky
        ctx.strokeStyle = '#000000';
        ctx.lineWidth = 3;
        ctx.strokeText(score, CANVAS_WIDTH / 2, 60);
        ctx.fillText(score, CANVAS_WIDTH / 2, 60);
        ctx.restore();
    }
}

function render(ctx) {
    // 1. Sky background (canvas clear)
    ctx.fillStyle = '#70c5ce';
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);

    // 2. Pipes (behind ground and bird)
    renderPipes(ctx);

    // 3. Ground (covers pipe bottoms)
    renderGround(ctx);

    // 4. Bird (always on top)
    renderBird(ctx);

    // 5. Score (topmost UI layer)
    renderScore(ctx);
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
