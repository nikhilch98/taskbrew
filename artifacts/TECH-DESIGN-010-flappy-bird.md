# Technical Design: Flappy Bird Web Game

**Document ID:** TECH-DESIGN-010
**PRD Reference:** PRD-010-flappy-bird-web-game.md
**Group:** GRP-010
**Author:** architect-1
**Date:** 2026-02-24
**Status:** Final

---

## Table of Contents

1. [File Structure Decision](#1-file-structure-decision)
2. [Game Loop Architecture](#2-game-loop-architecture)
3. [State Machine Design](#3-state-machine-design)
4. [Entity Data Structures](#4-entity-data-structures)
5. [Pipe Lifecycle Management](#5-pipe-lifecycle-management)
6. [Collision Detection Algorithm](#6-collision-detection-algorithm)
7. [Rendering Pipeline](#7-rendering-pipeline)
8. [Input System Design](#8-input-system-design)
9. [Constants & Tuning](#9-constants--tuning)
10. [FR-to-Design Traceability](#10-fr-to-design-traceability)

---

## 1. File Structure Decision

**Decision: Option A — Three separate files**

```
flappy-bird/
├── index.html      # Entry point: canvas element, minimal markup, loads style.css and game.js
├── style.css       # Page layout: canvas centering, page background, body reset
└── game.js         # All game logic: constants, state machine, entities, game loop, rendering, input
```

### Rationale

| Factor | Option A (3 files) | Option B (single file) |
|--------|-------------------|----------------------|
| Code readability | Better — CSS and JS are syntax-highlighted separately | Worse — mixed contexts in one file |
| Maintainability | Better — coder can work on logic without touching markup | Worse — scroll fatigue |
| `file://` support | Works — `<script src>` and `<link rel>` resolve relative paths over `file://` | Works |
| Deployment simplicity | Requires 3 files in same directory | Single file to distribute |
| Browser caching | CSS/JS cached independently | N/A |

Three files provides better code organization and readability at negligible cost. Both `<script src="game.js">` and `<link rel="stylesheet" href="style.css">` work correctly over `file://` protocol as long as all files are in the same directory.

### File Responsibilities

**`index.html`**
- Document structure: `<!DOCTYPE html>`, `<html>`, `<head>`, `<body>`
- `<meta charset="UTF-8">` and `<meta name="viewport" content="width=device-width, initial-scale=1.0">`
- `<link rel="stylesheet" href="style.css">`
- Single `<canvas id="gameCanvas" width="400" height="600"></canvas>`
- `<script src="game.js"></script>` placed at the **end of `<body>`** (ensures canvas DOM is ready, no `DOMContentLoaded` needed)
- No other DOM elements — all game UI is rendered on canvas

**`style.css`**
- `* { margin: 0; padding: 0; box-sizing: border-box; }` — reset
- `body` — dark background (`#2c2c2c`), flex centering, full viewport height, `overflow: hidden`
- `canvas` — `display: block`, optional subtle `box-shadow` for polish
- `user-select: none` and `-webkit-touch-callout: none` on body to prevent text selection on mobile
- `touch-action: none` on canvas to prevent browser gesture interference

**`game.js`**
- Single script file, no modules (`import`/`export` forbidden — `file://` incompatible)
- Structure (top-to-bottom):
  1. Constants block
  2. Canvas/context initialization
  3. Game state variables
  4. State machine (`handleInput`, `resetGame`, `flap`, `updateBird`)
  5. Input handler setup
  6. Pipe functions (spawn, update, render — grouped by concern)
  7. Collision detection
  8. Scoring
  9. Update logic (`update(dt)`)
  10. Remaining render functions and main `render(ctx)`
  11. Game loop (`gameLoop(timestamp)`)
  12. Initialization call

> **Note:** Pipe functions are grouped by domain concern (spawn/update/render together) rather than by execution phase. This provides better locality when modifying pipe behavior.

### Critical Constraints Confirmed
- No `import`/`export` — script uses global scope via a single `<script src>` tag
- No CDN resources — zero external `<script>` or `<link>` tags
- No build tools — files are authored directly and opened via `file://`

---

## 2. Game Loop Architecture

**Decision: Simple variable-timestep with capped delta-time**

A fixed-timestep accumulator is unnecessary for this game's complexity. A simple variable-step loop with a capped `deltaTime` provides frame-rate independence while keeping the code straightforward.

### Game Loop Pseudocode

```javascript
// game.js — at the bottom of the file

let lastTimestamp = 0;

function gameLoop(timestamp) {
    // 1. Compute delta-time in seconds
    if (lastTimestamp === 0) {
        lastTimestamp = timestamp;
    }
    let dt = (timestamp - lastTimestamp) / 1000; // ms → seconds
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
```

### Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Timestep model | Variable-step with cap | Simple, sufficient for this game, no sub-step complexity |
| Delta-time cap | 50ms (0.05s) | Prevents tunneling through pipes after tab-away. At 120 px/s pipe speed, max single-frame movement is 6px — well within collision detection range |
| Time unit | Seconds (float) | Constants are in px/s and px/s^2, so seconds avoids repeated `/1000` conversions |
| Initial frame | Skip first delta (set `lastTimestamp = timestamp` when 0) | Avoids a massive first-frame delta from page load to first `rAF` callback |
| Phase separation | `update(dt)` then `render(ctx)` | Clean separation of concerns; render always sees consistent post-update state |

### Why Not Fixed-Timestep Accumulator?

A fixed-timestep accumulator (`while (accumulator >= fixedDt) { update(fixedDt); accumulator -= fixedDt; }`) adds complexity for interpolation between physics steps. For a game with simple single-body physics (one bird, rectangular pipes), variable-step with a 50ms cap is deterministic enough and dramatically simpler to implement and debug.

---

## 3. State Machine Design

**Decision: String constants with a centralized `gameState` variable and per-state behavior in the `update`/`render` functions using `switch` statements.**

### State Representation

```javascript
// State constants (not a class enum — just string constants for clarity)
const STATE_IDLE      = 'IDLE';
const STATE_PLAYING   = 'PLAYING';
const STATE_GAME_OVER = 'GAME_OVER';

// Current state
let gameState = STATE_IDLE;
```

**Why string constants over classes/objects:** For a 3-state game, a class-based state pattern (where each state is an object with `enter()`/`update()`/`render()` methods) is over-engineered. String constants with `switch` statements are explicit, easy to read, and easy to debug. The coder can see all behavior for a given phase by searching for the state string.

### State Transition Table

| Current State | Trigger | Next State | Side Effects |
|---------------|---------|------------|--------------|
| `IDLE` | Any valid input (space/click/touch) | `PLAYING` | Apply first flap impulse; start spawning pipes |
| `PLAYING` | Collision with pipe or ground | `GAME_OVER` | Freeze all motion |
| `GAME_OVER` | Any valid input (space/click/touch) | `IDLE` | Execute full `resetGame()` |

### Transition Implementation

```javascript
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
```

### Per-State Behavior

**`update(dt)` behavior:**

| State | Behavior |
|-------|----------|
| `IDLE` | Update bird bob animation via sine wave: `bird.y = BIRD_START_Y + Math.sin(bobTimer * BOB_FREQUENCY * 2 * Math.PI) * BOB_AMPLITUDE`. Increment `bobTimer += dt`. Scroll ground. |
| `PLAYING` | Apply gravity to bird velocity. Update bird y-position. Update bird rotation. Move all pipes left. Spawn new pipes when needed. Check collisions (may transition to `GAME_OVER`). **Early exit if dead — dead bird cannot score.** Update score. Scroll ground. Collisions are checked BEFORE scoring so that a bird killed mid-frame never increments the score. |
| `GAME_OVER` | No updates to any entity positions. Optionally: could let bird fall to ground for "juice" but not required for MVP. |

**`render(ctx)` behavior:**

| State | Renders |
|-------|---------|
| `IDLE` | Sky background, ground (scrolling), bird (at bobbing position), title text overlay ("Flappy Bird"), instruction text ("Press Space or Tap to Start") |
| `PLAYING` | Sky background, pipes, ground (scrolling), bird (at game position with rotation), score text (top-center) |
| `GAME_OVER` | Sky background, pipes (frozen), ground (frozen), bird (frozen position), semi-transparent dark overlay, "Game Over" text, final score, "Press Space or Tap to Restart" text |

### Full Reset Checklist — `resetGame()`

Every variable that must be reset when transitioning from `GAME_OVER` to `IDLE`:

```javascript
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

    // Pipe spawn accumulator
    distanceSinceLastPipe = 0;       // Reset pipe spawn distance tracking
}
```

**Checklist (AC-1.4 compliance):**
- [x] `gameState` → `STATE_IDLE`
- [x] `bird.y` → `BIRD_START_Y`
- [x] `bird.velocity` → `0`
- [x] `bird.rotation` → `0`
- [x] `pipes` array → empty (`length = 0`)
- [x] `score` → `0`
- [x] `bobTimer` → `0`
- [x] `groundOffset` → `0`
- [x] `distanceSinceLastPipe` → `0`

---

## 4. Entity Data Structures

**Decision: Plain objects (object literals)**

### Rationale

| Approach | Pros | Cons |
|----------|------|------|
| Plain objects | Simplest, no boilerplate, easy to inspect in debugger | No encapsulation |
| ES6 classes | Encapsulation, methods on prototype | Overkill for 3 entity types with no inheritance |
| Factory functions | Encapsulation via closure | Unnecessary complexity |

Plain objects are the right choice because:
1. There are only 3 entity types with no shared behavior or inheritance
2. All entity logic (update, render) is handled by standalone functions, not methods
3. Object shapes are simple and static (no dynamic property addition)
4. Easiest to reset (just reassign properties)

### Bird

```javascript
// Initialization
const BIRD_START_Y = CANVAS_HEIGHT / 2; // Vertical center

let bird = {
    x: BIRD_X,            // number — fixed horizontal position (100px = 25% of 400)
    y: BIRD_START_Y,       // number — vertical position (center of bird circle), px from top
    velocity: 0,           // number — vertical velocity in px/s (positive = downward)
    radius: BIRD_RADIUS,   // number — collision and visual radius (15px)
    rotation: 0            // number — visual tilt in radians (0 = level, negative = nose-up, positive = nose-down)
};
```

**Bird physics functions:**

```javascript
function flap() {
    bird.velocity = FLAP_VELOCITY; // Set (not add) to -280 px/s
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
```

### Pipe

```javascript
// Each pipe pair is a single object
// Created by spawnPipe() and stored in the pipes[] array

/*
 * Pipe object shape:
 * {
 *     x:      number,  // horizontal position of pipe's LEFT edge, px from canvas left
 *     gapY:   number,  // Y-coordinate of the TOP of the gap opening, px from canvas top
 *     scored: boolean  // whether the bird has passed this pipe (prevents double-counting)
 * }
 *
 * Derived values (computed from constants, not stored):
 *   Top pipe:    rectangle from (x, 0) to (x + PIPE_WIDTH, gapY)
 *   Bottom pipe: rectangle from (x, gapY + PIPE_GAP) to (x + PIPE_WIDTH, CANVAS_HEIGHT - GROUND_HEIGHT)
 */
```

**Why `gapY` = top of gap (not center of gap):**
Using the top of the gap makes the rectangle math simpler:
- Top pipe bottom edge = `gapY`
- Bottom pipe top edge = `gapY + PIPE_GAP`

No `- GAP/2` arithmetic needed.

### Pipes Collection

```javascript
let pipes = [];  // Array of pipe objects, ordered by spawn time (leftmost first)
```

### Ground

```javascript
let groundOffset = 0;  // number — horizontal scroll offset in px, wraps modularly
```

Ground is not a full entity — it's a single offset value used for the scrolling visual effect.

### Score

```javascript
let score = 0;  // number — integer count of pipe pairs passed
```

### Timing

```javascript
let bobTimer = 0;  // number — elapsed time in seconds, used for sine-wave bobbing in IDLE state
```

---

## 5. Pipe Lifecycle Management

**Decision: Distance-based spawning with array push/shift cleanup**

### Spawn Strategy

**When to spawn:** Distance-accumulator — a `distanceSinceLastPipe` accumulator tracks how far the world has scrolled since the last pipe spawn. When the accumulator reaches `PIPE_SPACING`, a new pipe is spawned and the accumulator wraps by subtracting `PIPE_SPACING` (preserving the fractional remainder).

Spawn logic is embedded inside `updatePipes()` (see below). The first pipe is controlled via seeding in `handleInput()`:

```javascript
// In handleInput(), STATE_IDLE → STATE_PLAYING transition:
distanceSinceLastPipe = PIPE_SPACING - FIRST_PIPE_DELAY;
```

**Rationale:** Accumulator preserves fractional remainder for drift-free spacing and is decoupled from pipe array state. The accumulator approach is independent of array mutations and cleanly handles the first-pipe delay via seeding.

### Spawn Algorithm

```javascript
function spawnPipe() {
    // Random gap position, constrained to safe bounds
    // gapY is the TOP of the gap
    const minGapY = PIPE_MIN_TOP;                                    // 50px
    const maxGapY = CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50;  // 600 - 60 - 130 - 50 = 360px
    const gapY = minGapY + Math.random() * (maxGapY - minGapY);

    pipes.push({
        x: CANVAS_WIDTH,   // Spawn at right edge (400px)
        gapY: gapY,        // Random gap top position within [50, 360]
        scored: false       // Not yet passed by bird
    });
}
```

**Gap position bounds explained:**
- `PIPE_MIN_TOP = 50`: Top pipe is at least 50px tall (pipe cap is visible)
- `maxGapY = CANVAS_HEIGHT - GROUND_HEIGHT - PIPE_GAP - 50 = 360`: Bottom pipe is at least 50px tall above ground
- Gap range: `[50, 360]` — a 310px range ensures varied but fair layouts
- The gap is always fully above the ground line

### Update Algorithm

```javascript
function updatePipes(dt) {
    // 1. Move all pipes left
    for (let i = 0; i < pipes.length; i++) {
        pipes[i].x -= PIPE_SPEED * dt;
    }

    // 2. Accumulator-based spawn
    distanceSinceLastPipe += PIPE_SPEED * dt;
    if (distanceSinceLastPipe >= PIPE_SPACING) {
        distanceSinceLastPipe -= PIPE_SPACING;
        spawnPipe();
    }

    // 3. Cleanup: remove pipes that are fully off-screen left
    //    Using shift from front since pipes are ordered left-to-right
    while (pipes.length > 0 && pipes[0].x + PIPE_WIDTH < 0) {
        pipes.shift();
    }
}
```

**Why `shift()` instead of `filter()` or `splice()`:**
- Pipes are always ordered left-to-right (spawned at right edge, move left)
- Only the leftmost pipes can be off-screen
- `shift()` is O(1) amortized for removing from the front
- `filter()` would create a new array every frame — unnecessary allocation
- Typically only 0-1 pipes are removed per frame, so the `while` loop body runs at most once

### Scoring Check

```javascript
/**
 * updateScore() — Increment score when bird passing the pipe's center line.
 * Scores when the bird's center passes the pipe's midpoint, providing intuitive feedback.
 */
function updateScore() {
    for (let i = 0; i < pipes.length; i++) {
        if (!pipes[i].scored && pipes[i].x + PIPE_WIDTH / 2 < bird.x) {
            pipes[i].scored = true;
            score += 1;
        }
    }
}
```

**Double-count prevention:** The `scored` boolean flag on each pipe ensures that once a pipe is marked as scored, it will never increment the score again, regardless of how many frames the bird remains past it.

### Pipe Array Bounds Analysis

At any given time, the maximum number of pipes on screen:
- Canvas width = 400px, pipe spacing = 220px
- Max visible pipes = ceil(400 / 220) + 1 = 3 (includes one partially off-screen right and one partially off-screen left)
- With cleanup, array length stays in range [0, 4] — memory is bounded.

---

## 6. Collision Detection Algorithm

### Overview

Two types of collision checks each frame during `PLAYING` state:
1. **Bird vs. Ground** — simple y-threshold
2. **Bird vs. Pipes** — circle-vs-AABB for each nearby pipe pair

### Ground Collision

```javascript
function checkGroundCollision() {
    const groundY = CANVAS_HEIGHT - GROUND_HEIGHT; // 600 - 60 = 540
    return bird.y + bird.radius >= groundY;
}
```

If true → transition to `GAME_OVER`.

### Circle-vs-AABB Algorithm (Bird vs. Pipe)

For each pipe, we check the bird circle against two rectangles (top pipe and bottom pipe).

**Algorithm: Find nearest point on rectangle to circle center, then check distance.**

```
Given:
  Circle: center (cx, cy), radius r
  Rectangle: left edge x1, top edge y1, right edge x2, bottom edge y2

Step 1: Find the nearest point (nx, ny) on the rectangle to the circle center
  nx = clamp(cx, x1, x2)
  ny = clamp(cy, y1, y2)

Step 2: Compute squared distance from circle center to nearest point
  dx = cx - nx
  dy = cy - ny
  distSquared = dx * dx + dy * dy

Step 3: Collision if distSquared <= r * r
  (Using squared distance avoids a costly sqrt call)
```

### Full Collision Detection Pseudocode

```javascript
function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function circleRectCollision(cx, cy, r, rectX, rectY, rectW, rectH) {
    // Nearest point on rect to circle center
    const nearestX = clamp(cx, rectX, rectX + rectW);
    const nearestY = clamp(cy, rectY, rectY + rectH);

    // Distance squared
    const dx = cx - nearestX;
    const dy = cy - nearestY;

    return (dx * dx + dy * dy) <= (r * r);
}

function checkPipeCollisions() {
    const cx = bird.x;
    const cy = bird.y;
    const r  = bird.radius;

    for (let i = 0; i < pipes.length; i++) {
        const pipe = pipes[i];

        // Optimization: skip pipes that are too far horizontally
        // Bird can only collide with pipes whose x-range overlaps bird's x +/- radius
        if (pipe.x > cx + r + PIPE_WIDTH) continue;   // Pipe is far to the right
        if (pipe.x + PIPE_WIDTH < cx - r) continue;    // Pipe is far to the left

        // Top pipe rectangle: from (pipe.x, 0) to (pipe.x + PIPE_WIDTH, pipe.gapY)
        if (circleRectCollision(cx, cy, r, pipe.x, 0, PIPE_WIDTH, pipe.gapY)) {
            return true; // Collision with top pipe
        }

        // Bottom pipe rectangle: from (pipe.x, pipe.gapY + PIPE_GAP) to (pipe.x + PIPE_WIDTH, groundY)
        const bottomPipeTop = pipe.gapY + PIPE_GAP;
        const bottomPipeHeight = (CANVAS_HEIGHT - GROUND_HEIGHT) - bottomPipeTop;
        if (circleRectCollision(cx, cy, r, pipe.x, bottomPipeTop, PIPE_WIDTH, bottomPipeHeight)) {
            return true; // Collision with bottom pipe
        }
    }

    return false; // No collision
}

function checkCollisions() {
    // Ground collision (simple threshold)
    if (checkGroundCollision()) {
        gameState = STATE_GAME_OVER;
        return;
    }

    // Pipe collisions (circle-vs-AABB)
    if (checkPipeCollisions()) {
        gameState = STATE_GAME_OVER;
        return;
    }
}
```

### Optimization Notes

The horizontal early-exit optimization (`continue` on far-away pipes) means we only run the circle-vs-AABB math for pipes near the bird. Since the bird's x is fixed at 100px, at most 1-2 pipes will be checked per frame (those whose x-range [pipe.x, pipe.x + 52] overlaps [100 - 15, 100 + 15] = [85, 115]).

### Edge Case: Bird Skimming Gap Boundary (AC-4.5)

The circle-vs-AABB algorithm naturally handles this. When the bird's circle is exactly at the gap edge, the nearest point on the pipe rectangle to the circle center is the corner of the pipe. The distance check determines whether the circle overlaps that corner. This gives pixel-accurate collision at gap boundaries — no special-case code needed.

---

## 7. Rendering Pipeline

**All rendering uses the Canvas 2D API (`CanvasRenderingContext2D`).**

### Draw Order (Painter's Algorithm — Back to Front)

Every frame, the full canvas is redrawn in this order:

```
Layer 0: Background (sky)          — furthest back
Layer 1: Pipes                     — behind ground
Layer 2: Ground                    — covers pipe bottoms for polish
Layer 3: Bird                      — always on top of game elements
Layer 4: UI overlay (score, text)  — topmost
```

### Canvas Clear Strategy

**Decision: Full background fill (no `clearRect`).**

Instead of `ctx.clearRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)`, we fill the entire canvas with the sky background color as the first draw call. This is equivalent to clearing (every pixel is overwritten) and saves one API call.

### Layer 0: Background

```javascript
function renderBackground(ctx) {
    // Solid sky blue fill covering entire canvas
    ctx.fillStyle = '#70c5ce';  // Light sky blue
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
}
```

### Layer 1: Pipes

```javascript
function renderPipes(ctx) {
    for (let i = 0; i < pipes.length; i++) {
        const pipe = pipes[i];

        // Pipe body color
        ctx.fillStyle = '#3cb043';  // Green

        // Top pipe: from top of canvas down to gap
        ctx.fillRect(pipe.x, 0, PIPE_WIDTH, pipe.gapY);

        // Bottom pipe: from bottom of gap down to ground level
        const bottomPipeTop = pipe.gapY + PIPE_GAP;
        const groundY = CANVAS_HEIGHT - GROUND_HEIGHT;
        ctx.fillRect(pipe.x, bottomPipeTop, PIPE_WIDTH, groundY - bottomPipeTop);

        // Pipe caps (darker green, slightly wider) for visual polish
        const CAP_HEIGHT = 20;
        const CAP_OVERHANG = 3; // px wider on each side
        ctx.fillStyle = '#2d8a34'; // Darker green

        // Top pipe cap (at bottom of top pipe)
        ctx.fillRect(pipe.x - CAP_OVERHANG, pipe.gapY - CAP_HEIGHT,
                     PIPE_WIDTH + CAP_OVERHANG * 2, CAP_HEIGHT);

        // Bottom pipe cap (at top of bottom pipe)
        ctx.fillRect(pipe.x - CAP_OVERHANG, bottomPipeTop,
                     PIPE_WIDTH + CAP_OVERHANG * 2, CAP_HEIGHT);
    }
}
```

### Layer 2: Ground

```javascript
function renderGround(ctx) {
    const groundY = CANVAS_HEIGHT - GROUND_HEIGHT;

    // Main ground fill
    ctx.fillStyle = '#deb050';  // Sandy brown
    ctx.fillRect(0, groundY, CANVAS_WIDTH, GROUND_HEIGHT);

    // Top edge (grass line)
    ctx.fillStyle = '#5cb85c';  // Green grass
    ctx.fillRect(0, groundY, CANVAS_WIDTH, 4);

    // Ground texture: scrolling vertical lines for movement illusion
    // Uses modular offset to create infinite scroll effect
    ctx.strokeStyle = '#c8a040';
    ctx.lineWidth = 1;
    const LINE_SPACING = 20;
    // Calculate starting x based on offset, wrapping around
    const startX = -(groundOffset % LINE_SPACING);
    for (let x = startX; x < CANVAS_WIDTH; x += LINE_SPACING) {
        ctx.beginPath();
        ctx.moveTo(x, groundY + 10);
        ctx.lineTo(x, groundY + GROUND_HEIGHT);
        ctx.stroke();
    }
}
```

**Ground scroll update (in `update(dt)`):**

```javascript
function updateGround(dt) {
    // Scroll at same speed as pipes for visual consistency
    groundOffset += PIPE_SPEED * dt;
    // Modular wrap to prevent floating-point overflow over long sessions
    groundOffset = groundOffset % CANVAS_WIDTH;
}
```

Ground scrolls during `IDLE` and `PLAYING` states, but NOT during `GAME_OVER`.

### Layer 3: Bird

```javascript
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

    // Wing — small arc on the body
    ctx.fillStyle = '#e0b030';
    ctx.beginPath();
    ctx.ellipse(-2, 3, 8, 5, -0.3, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
}
```

**Key rendering technique:** `ctx.save()` → `ctx.translate(bird.x, bird.y)` → `ctx.rotate(bird.rotation)` → draw at (0,0) → `ctx.restore()`. This rotates the bird around its center point without complex coordinate math.

### Layer 4: UI Overlay

```javascript
function renderScore(ctx) {
    // Score text — top center, large white with dark stroke for readability
    ctx.font = 'bold 48px Arial, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';

    // Dark stroke (outline) for contrast against any background
    ctx.strokeStyle = '#000000';
    ctx.lineWidth = 4;
    ctx.lineJoin = 'round';
    ctx.strokeText(String(score), CANVAS_WIDTH / 2, 30);

    // White fill
    ctx.fillStyle = '#ffffff';
    ctx.fillText(String(score), CANVAS_WIDTH / 2, 30);
}

function renderIdleOverlay(ctx) {
    // Title
    ctx.font = 'bold 36px Arial, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#ffffff';
    ctx.strokeStyle = '#000000';
    ctx.lineWidth = 3;
    ctx.lineJoin = 'round';
    ctx.strokeText('Flappy Bird', CANVAS_WIDTH / 2, CANVAS_HEIGHT / 4);
    ctx.fillText('Flappy Bird', CANVAS_WIDTH / 2, CANVAS_HEIGHT / 4);

    // Instruction
    ctx.font = '18px Arial, sans-serif';
    ctx.strokeStyle = '#000000';
    ctx.lineWidth = 2;
    ctx.strokeText('Press Space or Tap to Start', CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2 + 80);
    ctx.fillText('Press Space or Tap to Start', CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2 + 80);
}

function renderGameOverOverlay(ctx) {
    // Semi-transparent dark overlay
    ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);

    // "Game Over" text
    ctx.font = 'bold 40px Arial, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#ffffff';
    ctx.strokeStyle = '#000000';
    ctx.lineWidth = 3;
    ctx.lineJoin = 'round';
    ctx.strokeText('Game Over', CANVAS_WIDTH / 2, CANVAS_HEIGHT / 3);
    ctx.fillText('Game Over', CANVAS_WIDTH / 2, CANVAS_HEIGHT / 3);

    // Final score
    ctx.font = 'bold 30px Arial, sans-serif';
    ctx.strokeText('Score: ' + score, CANVAS_WIDTH / 2, CANVAS_HEIGHT / 3 + 60);
    ctx.fillText('Score: ' + score, CANVAS_WIDTH / 2, CANVAS_HEIGHT / 3 + 60);

    // Restart instruction
    ctx.font = '18px Arial, sans-serif';
    ctx.lineWidth = 2;
    ctx.strokeText('Press Space or Tap to Restart', CANVAS_WIDTH / 2, CANVAS_HEIGHT / 3 + 120);
    ctx.fillText('Press Space or Tap to Restart', CANVAS_WIDTH / 2, CANVAS_HEIGHT / 3 + 120);
}
```

### Full Render Function

```javascript
function render(ctx) {
    // Layer 0: Background
    renderBackground(ctx);

    // Layer 1: Pipes (only in PLAYING and GAME_OVER)
    if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) {
        renderPipes(ctx);
    }

    // Layer 2: Ground (always visible)
    renderGround(ctx);

    // Layer 3: Bird (always visible)
    renderBird(ctx);

    // Layer 4: UI
    switch (gameState) {
        case STATE_IDLE:
            renderIdleOverlay(ctx);
            break;
        case STATE_PLAYING:
            renderScore(ctx);
            break;
        case STATE_GAME_OVER:
            renderGameOverOverlay(ctx);
            break;
    }
}
```

---

## 8. Input System Design

### Event Listeners

All listeners are attached to `document` (not the canvas) for broader capture, except `touchstart` which is on the `canvas` element to scope `preventDefault()`.

```javascript
// Track spacebar held state to prevent auto-repeat
let spacePressed = false;

// Keyboard — on document
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

// Mouse — on canvas (prevents clicks elsewhere from triggering)
const canvas = document.getElementById('gameCanvas');

canvas.addEventListener('mousedown', function(e) {
    e.preventDefault();
    handleInput();
});

// Touch — on canvas with preventDefault for mobile
canvas.addEventListener('touchstart', function(e) {
    e.preventDefault(); // Prevents scroll, zoom, and double-tap-to-zoom
    handleInput();
}, { passive: false }); // passive: false required to allow preventDefault
```

### Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Keyboard listener target | `document` | Spacebar should work even if canvas isn't focused |
| Mouse listener target | `canvas` | Only game area clicks should trigger flap |
| Touch listener target | `canvas` | Scoped `preventDefault()` — don't interfere with rest of page |
| Mouse event | `mousedown` (not `click`) | `mousedown` fires immediately; `click` waits for `mouseup` adding ~50-100ms perceived latency |
| Touch `passive` | `false` | Required to call `preventDefault()` in `touchstart` handler (Chrome enforces this) |
| Auto-repeat prevention | `spacePressed` boolean flag | `keydown` fires repeatedly when held; flag ensures only first press triggers `handleInput()` |
| Unified handler | `handleInput()` | All three input paths call the same function — behavior is identical regardless of input method |

### Edge Cases Handled

1. **Spacebar auto-repeat (AC-6.3):** The `spacePressed` flag tracks key state. Only the first `keydown` triggers a flap. Subsequent `keydown` events (auto-repeat) are ignored until `keyup` resets the flag.

2. **Touch scroll/zoom prevention (AC-6.4):** `touchstart` calls `e.preventDefault()` which blocks the browser's default touch behaviors (scrolling, pinch-zoom, double-tap-to-zoom).

3. **Input during transitions (AC-6.5):** The `handleInput()` function uses a `switch` on `gameState`, so input is always routed to the correct state handler. No race condition is possible because JavaScript is single-threaded and `requestAnimationFrame` callbacks are synchronous within their frame.

4. **Page scroll on spacebar:** `e.preventDefault()` on the spacebar `keydown` event prevents the browser from scrolling the page.

---

## 9. Constants & Tuning

### Confirmed Constants Table

All PRD-010 Section 6 constants are reviewed and confirmed. Values are well-suited for a 400x600 canvas.

```javascript
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

// First pipe timing
const FIRST_PIPE_DELAY = 60;  // px — scrolling distance before first pipe spawns

// Pipe cap (visual only — not in PRD, added for rendering polish)
const PIPE_CAP_HEIGHT    = 20;  // px
const PIPE_CAP_OVERHANG  = 3;   // px — extra width on each side
```

### Tuning Validation

| Constant | Value | Validation |
|----------|-------|------------|
| `GRAVITY = 980` | Standard Earth gravity scaled to px. At 980 px/s^2, bird falls 490px in 1 second from rest — covers most of the 600px canvas. This creates urgency to flap. **Confirmed.** |
| `FLAP_VELOCITY = -280` | At 280 px/s upward with 980 px/s^2 gravity, the bird rises for ~286ms and peaks ~40px above flap point. This feels snappy without being overpowered. **Confirmed.** |
| `MAX_FALL_SPEED = 600` | Caps fall at 600 px/s. At 120 px/s pipe speed, with dt capped at 50ms, max vertical movement per frame = 30px (which is 2x bird radius). Tunneling through 130px gap is impossible. **Confirmed.** |
| `PIPE_GAP = 130` | Gap is 130px with bird diameter of 30px, leaving 100px clearance. With the bird's vertical speed range, this is challenging but fair. **Confirmed.** |
| `PIPE_SPEED = 120` | At 120 px/s, a pipe takes ~3.3s to cross the 400px canvas. New pipes spawn every 220/120 = 1.83s. This gives comfortable reaction time. **Confirmed.** |
| `PIPE_SPACING = 220` | Horizontal gap between pipes. With pipe width of 52px, the gap between trailing edge of one pipe and leading edge of the next is 220 - 52 = 168px. At 120 px/s, that's 1.4s of clear flight time. **Confirmed.** |
| `FIRST_PIPE_DELAY = 60` | At 120 px/s pipe speed, this gives 0.5s of clear flight before the first pipe appears. Enough time for the player to orient after the first flap. **Confirmed.** |
| `BOB_AMPLITUDE = 8, BOB_FREQUENCY = 2` | 8px bob at 2Hz gives a gentle, noticeable hovering effect. **Confirmed.** |

### Units Summary

| Unit | Used By | Note |
|------|---------|------|
| `px` | All position/size values | Absolute canvas pixels |
| `px/s` | `FLAP_VELOCITY`, `MAX_FALL_SPEED`, `PIPE_SPEED` | Multiply by `dt` (seconds) each frame |
| `px/s^2` | `GRAVITY` | Multiply by `dt` to get velocity delta, then velocity by `dt` for position delta |
| `Hz` | `BOB_FREQUENCY` | Used in `sin(bobTimer * BOB_FREQUENCY * 2 * PI)` |
| `radians` | `bird.rotation` | Used in `ctx.rotate()` |

---

## 10. FR-to-Design Traceability

| Functional Requirement | Design Section(s) | Coverage Notes |
|----------------------|-------------------|----------------|
| **FR-1: Game State Machine** | Section 3 (State Machine Design) | All 3 states defined, transition table, per-state update/render behavior, full reset checklist |
| **FR-2: Bird Mechanics** | Section 4 (Entity Data Structures), Section 9 (Constants) | Bird data shape, `updateBird(dt)` with gravity/flap/rotation/clamping, all physics constants with units |
| **FR-3: Pipe Obstacles** | Section 4 (Entity Data Structures), Section 5 (Pipe Lifecycle) | Pipe data shape, distance-based spawning, gap randomization bounds, cleanup via shift, array bounds analysis |
| **FR-4: Collision Detection** | Section 6 (Collision Detection Algorithm) | Complete circle-vs-AABB pseudocode, ground collision, horizontal optimization, edge case analysis |
| **FR-5: Scoring** | Section 5 (Pipe Lifecycle — Scoring Check), Section 7 (Rendering — UI Overlay) | Score increment logic with double-count prevention, score rendering with font/style spec, game-over display, reset to 0 |
| **FR-6: Input Handling** | Section 8 (Input System Design) | All 3 input types (keyboard/mouse/touch), unified handler, auto-repeat prevention, preventDefault, passive:false |
| **FR-7: Rendering & Visual Design** | Section 7 (Rendering Pipeline) | 5-layer draw order, all element colors/styles, bird rotation rendering, ground scroll, score text styling, canvas sizing |

### Acceptance Criteria Cross-Reference

| AC | Design Evidence |
|----|----------------|
| AC-1.1 | Section 7: distinct render paths per state in `render()` |
| AC-1.2 | Section 3: transition table with explicit triggers |
| AC-1.3 | Section 3: only 3 states, all transitions mapped |
| AC-1.4 | Section 3: `resetGame()` with full variable checklist |
| AC-2.1–2.6 | Section 4: `updateBird(dt)` pseudocode covers all |
| AC-3.1–3.6 | Section 5: spawn/update/cleanup algorithm |
| AC-4.1–4.5 | Section 6: collision pseudocode with edge case analysis |
| AC-5.1–5.5 | Section 5 (scoring) + Section 7 (rendering) + Section 3 (reset) |
| AC-6.1–6.5 | Section 8: all input edge cases addressed |
| AC-7.1–7.6 | Section 7: full rendering pipeline |

---

## Appendix A: Complete `update(dt)` Flow

```javascript
function update(dt) {
    switch (gameState) {
        case STATE_IDLE:
            bobTimer += dt;
            bird.y = BIRD_START_Y + Math.sin(bobTimer * BOB_FREQUENCY * Math.PI * 2) * BOB_AMPLITUDE;
            updateGround(dt);
            break;

        case STATE_PLAYING:
            updateBird(dt);
            updatePipes(dt);
            checkCollisions();  // May transition to GAME_OVER
            if (gameState !== STATE_PLAYING) break;  // Early exit: dead bird cannot score
            updateScore();
            updateGround(dt);
            break;

        case STATE_GAME_OVER:
            // No updates — everything frozen
            break;
    }
}
```

---

## Appendix B: Complete `render(ctx)` Flow

```javascript
function render(ctx) {
    renderBackground(ctx);

    if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) {
        renderPipes(ctx);
    }

    renderGround(ctx);
    renderBird(ctx);

    switch (gameState) {
        case STATE_IDLE:
            renderIdleOverlay(ctx);
            break;
        case STATE_PLAYING:
            renderScore(ctx);
            break;
        case STATE_GAME_OVER:
            renderGameOverOverlay(ctx);
            break;
    }
}
```

---

## Appendix C: Initialization

```javascript
// Canvas setup (at top of game.js, after constants)
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');

// Game variables
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

// Set up input listeners (Section 8)
// ... (event listener code from Section 8)

// Start game loop
requestAnimationFrame(gameLoop);
```

---

*End of Technical Design Document*
