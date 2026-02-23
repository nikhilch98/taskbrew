# Flappy Bird

A browser-based Flappy Bird clone built with vanilla JavaScript and HTML5 Canvas.
No frameworks, no build tools, no server required -- just open and play.

---

## Quick Start

1. Open `index.html` in any modern web browser.
2. Press **Spacebar**, **click**, or **tap** to start playing.

That's it. No installation, no build step, no local server needed.

---

## How to Play

Guide the bird through gaps between pipes. Each pipe pair you pass earns one point.
The game ends when the bird hits a pipe or the ground.

### Controls

| Input        | Action                                |
|--------------|---------------------------------------|
| Spacebar     | Flap (keyboard)                       |
| Mouse click  | Flap (desktop)                        |
| Tap          | Flap (mobile / touchscreen)           |

### Game States

- **Start Screen** -- The bird bobs up and down in the center of the screen.
  Press Space or tap to begin.
- **Playing** -- The bird falls under gravity. Each flap gives an upward boost.
  Navigate through the gaps in the pipes to score points.
- **Game Over** -- The screen darkens and shows your final score.
  Press Space or tap to return to the Start Screen.

---

## Game Mechanics

- The bird is affected by gravity and accelerates downward each frame.
- Flapping sets the bird's vertical speed to a fixed upward value (it does not
  stack with the current velocity).
- The bird cannot fly above the top of the screen -- it stops at the ceiling.
- Falling speed is capped at a terminal velocity so the bird never moves
  unreasonably fast.
- Pipes scroll from right to left at a constant speed. The vertical gap
  position is randomized for each pipe pair.
- A point is scored when the bird passes the center of a pipe pair.
- Collision detection uses a circle (bird) vs. rectangle (pipe/ground) algorithm.

---

## File Structure

```
flappy-bird/
  index.html        Entry point -- open this file to play the game
  game.js           All game logic, rendering, physics, and input handling
  style.css         Page layout and canvas styling
  package.json      npm config for running tests
  tests/
    ts-073-duplicate-css-fix-qa.test.js   QA test suite
```

---

## Running Tests

Tests are plain Node.js scripts that use jsdom to simulate a browser environment.

1. Install test dependencies (first time only):

   ```
   npm install
   ```

2. Run the test suite:

   ```
   npm test
   ```

   This executes `tests/ts-073-duplicate-css-fix-qa.test.js`.

---

## Customization

All tunable game constants are defined at the top of `game.js`. You can adjust
these values to change how the game feels.

| Constant         | Default | Unit   | Description                                  |
|------------------|---------|--------|----------------------------------------------|
| `CANVAS_WIDTH`   | 400     | px     | Width of the game canvas                     |
| `CANVAS_HEIGHT`  | 600     | px     | Height of the game canvas                    |
| `GRAVITY`        | 980     | px/s^2 | Downward acceleration applied to the bird    |
| `FLAP_VELOCITY`  | -280    | px/s   | Upward impulse when the bird flaps           |
| `MAX_FALL_SPEED` | 600     | px/s   | Terminal velocity cap for falling             |
| `PIPE_GAP`       | 130     | px     | Vertical space between top and bottom pipes  |
| `PIPE_SPEED`     | 120     | px/s   | Horizontal scroll speed of the pipes         |
| `PIPE_SPACING`   | 220     | px     | Horizontal distance between pipe pairs       |
| `BIRD_RADIUS`    | 15      | px     | Size of the bird (collision and visual)       |

Tips:

- Increase `PIPE_GAP` or decrease `PIPE_SPEED` to make the game easier.
- Decrease `FLAP_VELOCITY` (make it more negative) for a stronger flap.
- Increase `GRAVITY` for a heavier, faster-falling bird.

---

## Technical Notes

- **Rendering**: All graphics are drawn with the Canvas 2D API. There are no
  image assets -- everything (bird, pipes, ground, sky) is rendered
  procedurally using shapes and colors.
- **Game loop**: Uses `requestAnimationFrame` with delta-time physics. Frame
  delta is capped at 50 ms to prevent physics glitches when the browser tab
  loses focus and regains it.
- **Input handling**: Keyboard events listen on `document` for broad capture.
  Mouse and touch events are bound to the canvas element. Touch events call
  `preventDefault()` to suppress duplicate synthetic mouse events on hybrid
  devices.
- **Collision detection**: The bird is modeled as a circle and pipes as
  axis-aligned rectangles. Collision uses a nearest-point-on-rect algorithm
  with squared-distance comparison (no square root needed).
- **Scoring**: A point is awarded when the bird's horizontal position passes
  the center line of a pipe pair. Each pipe can only be scored once.
- **State machine**: The game has three internal states that drive all update
  and render logic. Transitions happen via a single `handleInput()` function
  responding to any supported input.
- **Mobile support**: The viewport meta tag disables user scaling, and CSS
  `touch-action: none` prevents default touch gestures on the canvas. The
  game scales to fit the screen via `max-width` and `max-height` CSS rules.
