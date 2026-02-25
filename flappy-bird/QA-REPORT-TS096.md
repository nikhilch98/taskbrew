# QA Report: TS-096 — Full Game Logic (CD-018)

**Tester**: tester-1
**Date**: 2026-02-24
**Branch**: `feature/cd-018-flappy-bird-game-logic`
**Test File**: `flappy-bird/full-game-qa.test.js`

## Summary

| Metric | Value |
|--------|-------|
| Total tests | 174 |
| Passed | 174 |
| Failed | 0 |
| Bugs found | 0 |
| Verdict | **PASS** |

## Test Coverage by Category

### 1. Startup (7 tests) -- PASS
- Game loads without errors (sandbox creation succeeds)
- Initial state: IDLE, bird at center, score 0, no pipes
- Bird velocity and rotation at zero

### 2. Start Screen (2 tests) -- PASS
- "Flappy Bird" title rendered
- "Press Space or Tap to Start" instruction rendered

### 3. Bird Bob Animation (2 tests) -- PASS
- Bird bobs vertically during IDLE (~16px range, matches 2*BOB_AMPLITUDE)
- Oscillation confirmed across 60 frames

### 4. Ground Scrolling (4 tests) -- PASS
- Ground scrolls during IDLE
- Ground scrolls during PLAYING
- Ground freezes during GAME_OVER
- Ground offset wraps via modulo (stays in [0, 24))

### 5. Input & State Transitions (12 tests) -- PASS
- IDLE -> PLAYING on handleInput with immediate first flap
- PLAYING handleInput triggers flap
- GAME_OVER -> IDLE on handleInput (not directly to PLAYING)
- resetGame clears all 9 state variables

### 6. Input Listener Registration (5 tests) -- PASS
- keydown/keyup on document
- mousedown on canvas
- touchstart on canvas
- blur on window

### 7. Spacebar Auto-Repeat Prevention (5 tests) -- PASS
- spacePressed flag blocks held spacebar
- keyup resets flag
- Next press after release works

### 8. Window Blur Reset / R-3 (3 tests) -- PASS
- Blur resets spacePressed to false
- Spacebar works normally after tab-back

### 9. Bird Physics (10 tests) -- PASS
- Gravity, velocity cap, ceiling clamp, velocity reset at ceiling
- Flap sets (not adds) upward velocity
- Rotation tilts up on flap, down when falling

### 10. Pipe System (5 tests) -- PASS
- Spawn at right edge with valid gapY range
- Leftward movement, off-screen cleanup
- Distance accumulator spawn timing
- First pipe delay seeded correctly

### 11. Scoring (4 tests) -- PASS
- Score +1 per pipe pair passed
- No double-scoring (scored flag)
- Reset to 0 on resetGame

### 12. Collision Detection (8 tests) -- PASS
- Ground collision detected / not detected
- Top pipe, bottom pipe collision
- No collision in gap
- State transitions to GAME_OVER
- Bird clamped to ground surface

### 13. Game Over Overlay (3 tests) -- PASS
- "Game Over" text, final score, restart prompt rendered

### 14. Stability (16 tests) -- PASS
- 15 consecutive start/play/restart cycles without degradation

### 15. Architecture Review R-1 to R-5 (10 tests) -- PASS
- **R-1**: CSS `max-width: 100vw` and `max-height: 100vh` on canvas
- **R-2**: Viewport meta `maximum-scale=1.0, user-scalable=no`
- **R-3**: Window blur resets spacePressed
- **R-4**: touchstart comment documents preventDefault suppressing synthetic mouse events
- **R-5**: Collision early-exit uses `pipe.x > bird.x + bird.radius + PIPE_WIDTH`

### 16. HTML & CSS Mobile Support (8 tests) -- PASS
- touch-action: none, passive: false, overflow: hidden, user-select: none
- Viewport meta, canvas dimensions

### 17. Bird Visual (3 tests) -- PASS
- Wing rendered via ellipse() with different x/y radii

### 18. Delta-Time Cap (2 tests) -- PASS
- Physics explosion prevented on tab-refocus (yDelta=2.45px vs uncapped ~490px)

### 19. Pipe Gap Randomisation (3 tests) -- PASS
- Gaps randomised, within bounds, cover >50% of possible range

### 20. circleRectCollision Geometry (5 tests) -- PASS
- Inside, edge tangent, corner overlap, outside, near-miss corner

### 21. Score Display Per State (3 tests) -- PASS
- Score shown during PLAYING and GAME_OVER, hidden during IDLE

### 22. Update Ordering (3 tests) -- PASS
- bird -> pipes -> collision -> score (verified in source)

### 23. Integration Full Play Cycle (6 tests) -- PASS
- IDLE -> PLAYING -> gameplay -> GAME_OVER -> IDLE -> PLAYING

### 24. Constants Sanity (14 tests) -- PASS
- All 14 game constants match expected values

### 25. preventDefault (3 tests) -- PASS
- Space, mouse, touch all call preventDefault

### 26. Render Layer Ordering (3 tests) -- PASS
- background -> pipes -> ground -> bird (correct z-ordering)

## Items Not Testable in Node.js (Manual Verification Required)

The following require browser-based manual testing:

| Item | Status | Notes |
|------|--------|-------|
| 60fps smooth rendering | N/A | Requires browser DevTools Performance tab |
| No console errors | N/A | Requires browser DevTools Console |
| Canvas visual appearance | N/A | Requires visual inspection |
| Touch input on mobile device | N/A | Requires physical device or emulator |
| Pinch-zoom disabled | N/A | Requires mobile device |
| Canvas scales on narrow viewport | N/A | Requires browser resize test |

## Bugs Found

**None.** All 174 automated tests pass. No bugs identified.

## Verdict

**PASS** -- CD-018 implementation meets all testable acceptance criteria. The game logic is correct, all state transitions work properly, collision detection is geometrically sound, architecture review items R-1 through R-5 are implemented, and the system is stable through 15+ restart cycles.
