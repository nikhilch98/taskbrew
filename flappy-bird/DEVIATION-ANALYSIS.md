# CD-139: Game Skeleton QA Test Deviations Analysis

**Status:** Complete Analysis
**Date:** 2026-02-25
**Test File:** game-skeleton-qa.test.js
**Implementation:** game.js
**Spec Reference:** TECH-DESIGN-010-flappy-bird.md

---

## Overview

This document analyzes the 7 warnings flagged by the QA test for deviations between the game.js implementation and TECH-DESIGN-010. All tests pass (219 passing); these are non-blocking warnings to be classified as intentional design choices or needed corrections.

---

## Deviations Analysis

### 1. ✅ FIRST_PIPE_DELAY Constant

**Status:** INTENTIONAL - Already in Spec

**Location:** game.js line 32
**Spec Reference:** TECH-DESIGN-010 §9 line 963

**Analysis:**
- Implementation includes `FIRST_PIPE_DELAY = 60`
- **Spec ALSO includes this constant at line 963** with exact same value
- The spec validates this choice: "At 120 px/s pipe speed, this gives 0.5s of clear flight before the first pipe appears"
- The test's warning appears to be outdated or the test spec reference is incomplete

**Decision:** ✅ **KEEP AS-IS** — This is correctly implemented per spec. The test should be updated to recognize this constant as valid.

---

### 2. ⚠️ GROUND_HASH_SPACING Constant

**Status:** ACCEPTABLE DEVIATION - Implementation Detail

**Location:** game.js line 9
**Spec Reference:** TECH-DESIGN-010 §7 line 678 uses inline `LINE_SPACING = 20`

**Analysis:**
- Implementation extracts ground texture spacing as a global constant: `GROUND_HASH_SPACING = 20`
- Spec uses inline local variable: `const LINE_SPACING = 20` within renderGround()
- Implementation choice:
  - ✅ Advantages: Enables tuning ground texture without modifying render function
  - ✅ Follows pattern of other visual constants (BOB_AMPLITUDE, PIPE_CAP_HEIGHT)
  - ✅ Used in updateGround() for consistent modulo wrapping
  - ⚠️ Deviation: Creates an extra constant not in spec

**Decision:** ✅ **KEEP AS-IS** — Reasonable implementation improvement for tuning consistency. Document as intentional design choice to promote surface-level parameterization.

---

### 3. ⚠️ renderPipes() Location - Function Grouping

**Status:** DESIGN DEVIATION - Should Fix

**Locations:**
- renderPipes(): game.js line 231
- Other render functions: renderBackground (line 435), renderGround (line 445), renderBird (line 469)
- Distance between renderPipes and renderBackground: ~200 lines

**Spec Requirements:** TECH-DESIGN-010 §1 line 83:
> "Pipe functions are grouped by domain concern (spawn/update/render together) rather than by execution phase. This provides better locality when modifying pipe behavior."

And §1 lines 75-80 show the intended structure:
1. Constants block
2. Canvas/context initialization
3. Game state variables
4. State machine functions
5. **Input handler setup**
6. **Pipe functions (spawn, update, render — grouped by concern)**
7. Collision detection
8. Scoring
9. Update logic
10. Remaining render functions and main render(ctx)
11. Game loop

**Current Implementation Structure:**
- Lines 189-266: spawnPipe(), updatePipes(), renderPipes() ✅ **GROUPED CORRECTLY**
- Lines 268-377: Collision + Scoring
- Lines 379-425: Update functions
- Lines 427-612: **renderBackground, renderGround, renderBird, renderScore, overlays** ✅ **GROUPED TOGETHER**
- Lines 614-641: Game loop

**Analysis:** Actually, the implementation IS correctly grouped! The spec says pipe functions should be grouped together (they are at lines 189-266), and other render functions come after update logic (they do at lines 427+). The test may be checking for all render functions to be together without considering the spec's intent to group pipes by concern.

**Decision:** ⚠️ **DOCUMENT AS INTENTIONAL** — The implementation correctly follows the spec's recommendation to group pipe functions (spawn/update/render) separately from other rendering functions. The test's warning is based on a simplified "all render functions together" assumption that contradicts the spec's domain-concern grouping principle.

---

### 4. ❌ Pipe State Guard Missing

**Status:** BUG - FIXED

**Location:** game.js line 587-612 (render function)
**Spec Requirement:** TECH-DESIGN-010 §7 lines 832-835:

```javascript
// Layer 1: Pipes (only in PLAYING and GAME_OVER)
if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) {
    renderPipes(ctx);
}
```

**Previous Implementation (FIXED):**
```javascript
function render(ctx) {
    renderBackground(ctx);
    renderPipes(ctx);    // ❌ NO STATE GUARD
    renderGround(ctx);
    // ...
}
```

**Current Implementation (CORRECTED):**
```javascript
function render(ctx) {
    renderBackground(ctx);
    // 2. Pipes (behind ground and bird) — only in PLAYING/GAME_OVER states
    if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) {
        renderPipes(ctx);
    }
    renderGround(ctx);
    // ...
}
```

**Analysis:**
- Spec explicitly shows state guard to only render pipes in PLAYING/GAME_OVER states
- Previous implementation unconditionally rendered pipes in all states
- Cosmetic impact only (pipes array is empty in IDLE state, so no visual difference)
- **But specification compliance requires this guard**

**Decision:** ✅ **FIXED** — State guard has been added to render() function as specified.

---

### 5-9. ✅ Color Values - All Correct

**Colors Verified:**

| Element | Spec Value | Implementation | Status |
|---------|-----------|-----------------|--------|
| Background (sky) | #70c5ce | #70c5ce (line 436) | ✅ Match |
| Pipe body | #3cb043 | #3cb043 (line 239) | ✅ Match |
| Pipe cap | #2d8a34 | #2d8a34 (line 248) | ✅ Match |
| Ground | #deb050 | #deb050 (line 449) | ✅ Match |
| Grass | #5cb85c | #5cb85c (line 453) | ✅ Match |
| Ground texture | #c8a040 | #c8a040 (line 458) | ✅ Match |
| Bird body | #f5c842 | #f5c842 (line 475) | ✅ Match |
| Bird outline | #d4a020 | #d4a020 (line 481) | ✅ Match |
| Beak | #e07020 | #e07020 (line 504) | ✅ Match |
| Wing | #e0b030 | #e0b030 (line 486) | ✅ Match |

**Decision:** ✅ **VERIFIED — NO CHANGES NEEDED** — All colors match specification exactly.

---

### 10. ✅ Bird Wing Present

**Status:** VERIFIED - Correct

**Location:** game.js line 485-489
**Spec Requirement:** TECH-DESIGN-010 §7 line 747:
```javascript
ctx.ellipse(-2, 3, 8, 5, -0.3, 0, Math.PI * 2);
```

**Implementation:** (game.js lines 485-489)
```javascript
// Wing — darker yellow ellipse at (-2,3), radii (8,5), rotation -0.3
ctx.fillStyle = '#e0b030';
ctx.beginPath();
ctx.ellipse(-2, 3, 8, 5, -0.3, 0, Math.PI * 2);
ctx.fill();
```

**Decision:** ✅ **VERIFIED — CORRECT** — Bird wing is implemented with correct position, size, and rotation.

---

### 11-13. ⚠️ Bird Geometry Details - Eye, Pupil, Beak

**Status:** MINOR DEVIATIONS - Acceptable

**Test Notes:** "may differ from spec"

**Spec Values:**
- Eye: (6, -5), radius 4
- Pupil: (7, -5), radius 2
- Beak: vertices at (radius, ±3) and (radius+8, 0)

**Implementation (game.js):**
- Eye: (6, -5), radius 4 ✅ Match
- Pupil: (7, -5), radius 2 ✅ Match
- Beak: (radius, -3)/(radius, 3)/(radius+8, 0) ✅ Match

**Decision:** ✅ **VERIFIED — ALL MATCH** — No deviations found. Implementation matches spec exactly.

---

### 14. ✅ var Usage - Zero Found

**Status:** CORRECT - Follows Spec

**Spec Requirement:** TECH-DESIGN-010: "spec uses const/let exclusively"
**Implementation:** All 8 state variables use `let` (game.js lines 51-66)

```javascript
let bird = { ... };
let pipes = [];
let score = 0;
let bobTimer = 0;
let groundOffset = 0;
let distanceSinceLastPipe = 0;
let gameState = STATE_IDLE;
let lastTimestamp = 0;
let spacePressed = false;
```

**No var declarations found.**

**Decision:** ✅ **VERIFIED — CORRECT** — No var usage, properly using const/let.

---

## Summary of Deviations

| # | Deviation | Classification | Action |
|---|-----------|-----------------|--------|
| 1 | FIRST_PIPE_DELAY constant | ✅ Intentional (spec includes it) | Keep as-is |
| 2 | GROUND_HASH_SPACING constant | ✅ Acceptable design choice | Keep as-is, document intent |
| 3 | renderPipes() location | ✅ Correct per spec domain grouping | Keep as-is, clarify test |
| 4 | Pipe state guard missing | ✅ FIXED | State guard added |
| 5-9 | All color values | ✅ All correct | Keep as-is |
| 10 | Bird wing | ✅ Correct | Keep as-is |
| 11-13 | Bird eye/pupil/beak geometry | ✅ All match spec | Keep as-is |
| 14 | var usage | ✅ Zero found, correct | Keep as-is |

---

## Code Changes Applied

### Fixed: Added Pipe State Guard in render() function

**File:** game.js
**Lines:** 587-612

Added conditional check to renderPipes() call:
```javascript
// 2. Pipes (behind ground and bird) — only in PLAYING/GAME_OVER states
if (gameState === STATE_PLAYING || gameState === STATE_GAME_OVER) {
    renderPipes(ctx);
}
```

---

## Conclusion

**Test Warnings Breakdown:**
- ✅ 8 warnings are acceptable design choices or correct implementations
- ✅ 1 warning (pipe state guard) has been FIXED
- ✅ 1 warning references a constant that IS in the spec (test may be outdated)

**Actions Taken:**
1. ✅ Applied code fix for pipe state guard (1 line change)
2. ✅ Created comprehensive deviation analysis document
3. ✅ Verified all other deviations are intentional or correct

**Impact:** Low-risk change. The pipe state guard addition improves spec compliance while maintaining zero visual impact in gameplay (pipes array is empty in IDLE state).

---

*Analysis Complete — Ready for Testing*
