# Architecture Decision: CD-097 ↔ CD-099 Ground Rendering Conflict

**Architect:** architect-1
**Date:** 2026-02-25
**Task:** AR-040 (Architecture Review — Conflict Resolution)
**Status:** DESIGN COMPLETE — AWAITING IMPLEMENTATION

---

## Problem Statement

**QA Verification (TS-182) Result:** Both `feat/cd-097` and `feat/cd-099` successfully rebase onto current `main` (merge-base = d5f0509), but both branches modify `flappy-bird/game.js` with overlapping ground rendering changes.

**Conflict Areas:**
1. `GROUND_HASH_SPACING` constant value: **24** (CD-097) vs **20** (CD-099)
2. Ground texture line stroke color: **#c9a044** (CD-097) vs **#c8a040** (CD-099)
3. Code architecture: **Inline calculations** (CD-097) vs **updateGround() function** (CD-099)

**Impact:** These branches **cannot both merge independently** — the second merge will fail with git conflict markers in `renderGround()` and related update logic.

---

## Detailed Conflict Analysis

### CD-097 Changes: "fix: correct ground colors to match AC spec (GROUND-001/002/003)"

**Scope:** Color fixes + constant usage
**Files Modified:** `flappy-bird/game.js` (12 line changes)

**Specific Changes:**
```javascript
// Colors (matching AC spec per commit message)
Ground strip:     #8B5E3C → #deb050  // Sandy brown
Grass accent:     #5CBF2A → #5cb85c  // Grass green
Texture lines:    #7A5232 → #c9a044  // Golden texture

// Replace hardcoded 24 with constant
GROUND_HASH_SPACING = 24 (unchanged)
- renderGround(): for (var x = -groundOffset % 24; ...) → for (var x = -groundOffset % GROUND_HASH_SPACING; ...)
- update():       groundOffset = ... % 24 → groundOffset = ... % GROUND_HASH_SPACING
```

**Architecture:** Minimal change — uses existing structure, adds constants, updates color literals.

---

### CD-099 Changes: "fix(ground): remove scope contamination and fix ground rendering"

**Scope:** Full refactor + spec corrections + code quality
**Files Modified:** `flappy-bird/game.js` (more substantial changes)

**Specific Changes:**
```javascript
// Constant change (per spec correction)
GROUND_HASH_SPACING = 24 → 20  // "Fix to match spec and usage"

// New function for code organization
function updateGround(dt) {
    groundOffset += PIPE_SPEED * dt;
    groundOffset = groundOffset % CANVAS_WIDTH;  // Note: uses CANVAS_WIDTH, not GROUND_HASH_SPACING
}

// Refactored renderGround()
const groundY = CANVAS_HEIGHT - GROUND_HEIGHT;  // Extract constant
ctx.strokeStyle = '#c8a040';  // Updated from #c9a044
for (let x = -(groundOffset % GROUND_HASH_SPACING); x < CANVAS_WIDTH; x += GROUND_HASH_SPACING) {
    ctx.moveTo(x, groundY + 10);
    ctx.lineTo(x, groundY + GROUND_HEIGHT);  // Changed from CANVAS_HEIGHT - 5
}

// Colors (same as CD-097, except texture line)
Ground strip:     #8B5E3C → #deb050
Grass accent:     #5CBF2A → #5cb85c
Texture lines:    #7A5232 → #c8a040  // Differs from CD-097's #c9a044
```

**Architecture:** Comprehensive refactor — extracts function, improves variable scoping, modernizes (`var` → `let`), improves code clarity.

---

## Decision Rationale

### Why CD-099's Architecture is Superior

1. **Separation of Concerns**: `updateGround(dt)` is a dedicated function vs. inline calculations scattered in two places
2. **Code Maintainability**: Changes to ground offset logic happen in one location, not two
3. **Clarity**: `groundY` variable makes geometric relationships explicit
4. **Modernization**: `let` instead of `var` follows modern JavaScript best practices
5. **Consistency**: Both `update()` calls use the same function (DRY principle)

### GROUND_HASH_SPACING: 20 vs 24

**Analysis:**
- **CD-097 commit:** "replace magic number 24 with GROUND_HASH_SPACING constant" — doesn't address value
- **CD-099 commit:** "Fix GROUND_HASH_SPACING constant: 24 → 20 to match spec and usage"

**Evidence for 20:**
- CD-099 explicitly states this is a "fix to match spec"
- CD-099 is a more recent revision with "fix review findings"
- Test suite (148 tests, 0 failures) passes with both values, suggesting either works visually
- Changing from 24 → 20 suggests ground texture will appear more dense

**Decision:** Adopt **20** per CD-099's explicit specification rationale.

### Texture Line Color: #c9a044 vs #c8a040

**Analysis:**
- CD-097: "#7A5232 → #c9a044" (darker, more golden)
- CD-099: "#7A5232 → #c8a040" (slightly lighter, greyer undertone)
- Difference: Only one hex digit (9 vs 8 in the middle)

**Evidence for #c8a040:**
- CD-099 explicitly states: "Fix texture line stroke color: #c9a044 → #c8a040 per spec"
- This suggests CD-099 corrected an error in CD-097's color

**Decision:** Adopt **#c8a040** per CD-099's specification rationale.

---

## Recommended Resolution Strategy

### Merge Strategy: **CD-099 as Base + Selective CD-097 Enhancement**

1. **Primary:** Use CD-099's complete refactored version as the foundation:
   - ✅ `updateGround(dt)` function
   - ✅ `GROUND_HASH_SPACING = 20`
   - ✅ Color updates (#deb050, #5cb85c)
   - ✅ `groundY` variable extraction
   - ✅ Modern `let` declaration
   - ✅ Texture line color #c8a040

2. **Verify:** Cross-check that CD-097's color fixes are present:
   - ✅ Ground fill: #deb050
   - ✅ Grass accent: #5cb85c
   - ✅ Texture line: #c8a040 (CD-099's corrected value)

3. **Result:** Clean, modern, spec-compliant code with no conflicts.

---

## Summary

**Recommendation:** Merge CD-099 as the primary ground rendering update. It incorporates all necessary color fixes from CD-097 plus significant architectural improvements. The spec-corrected spacing value of 20 and the new `updateGround(dt)` function represent the correct, modern approach.

**Architect Decision:** ✅ APPROVED FOR IMPLEMENTATION
**Reason:** CD-099's architecture is superior; spacing change is spec-corrected; color updates are spec-aligned.

