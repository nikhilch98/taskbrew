# Architecture Review: AR-017 - Branching Policy Trivial-Fix Exemptions

**Date:** 2026-02-25
**Task ID:** AR-017 (referenced in CD-076)
**Decision:** Approved

---

## Executive Summary

This architecture review establishes formal criteria for the trivial-fix exemption to the project's
default branching policy. Under strict conditions, developers may commit directly to `main` for
obvious, zero-risk cleanups without requiring a dedicated feature branch.

---

## Decision Statement

We approve a **trivial-fix exemption** that allows direct commits to `main` **ONLY when all six criteria
are met simultaneously**:

1. **≤ 5 net lines changed** (additions + deletions combined)
2. **Pure deletion of dead/duplicate code OR comment-only change**
3. **Affects a single file**
4. **Introduces NO functional behavior change** (runtime semantics unchanged)
5. **Commit message includes the task ID** (e.g., `CD-067`)
6. **Commit message includes the `(direct-fix)` tag**

---

## Rationale

### Why This Exemption?

The default branching policy requires all changes through dedicated branches. However, requiring
branches for trivial cleanups creates unnecessary process overhead without safety benefit:

- **Duplicate property cleanup**: 3 lines removed, obvious deletion, zero risk
- **Dead code removal**: Comments about why code is disabled
- **Typo fixes in comments**: Single-line changes to documentation

These activities are:
- Immediately auditable by looking at the diff
- Completely reversible without side effects
- So small that CI/CD adds minimal value
- Repetitive enough to benefit from exception criteria

### Why Six Criteria?

The six criteria work together to create a **defense-in-depth** system:

- **Criteria 1-4** define the scope: small, localized, obviously safe changes
- **Criteria 5-6** create the audit trail: the `(direct-fix)` tag in the commit message is a
  signal to reviewers that this change claimed exemption status

This allows code review processes to verify that claimed exemptions actually meet all criteria.

---

## Compliance Verification Process

### For Developers

Before committing directly to `main`, verify **all six criteria**:

```bash
# Example compliant commit:
# Removes duplicate CSS properties after merge
git commit -m "fix(css): remove duplicate properties (CD-067) (direct-fix)"

# This commit claims exemption — code review will verify:
# 1. ✓ Only 3 lines removed
# 2. ✓ Pure deletion
# 3. ✓ Single file (style.css)
# 4. ✓ No behavior change
# 5. ✓ Task ID included (CD-067)
# 6. ✓ (direct-fix) tag included
```

### For Reviewers

When a direct-to-main commit appears in code review with `(direct-fix)` tag:

1. Verify it claims exemption by searching for `(direct-fix)` tag
2. Check all six criteria against the actual commit diff
3. If all criteria met: approve without requiring branch/PR
4. If any criterion violated: flag as policy violation

---

## When NOT to Use This Exemption

- "Small" feature additions (even if < 5 lines)
- Logic changes (even in comments explaining changes)
- Changes to multiple files
- Anything affecting runtime behavior
- When you're uncertain whether criteria apply

### Default Guidance

**When in doubt, use a branch.** The exemption exists for unambiguous, zero-risk cleanup.

---

## Examples

### Compliant Example

```diff
// Commit: fix(css): remove duplicate properties (CD-067) (direct-fix)
- max-width: 100vw;
- max-height: 100vh;
- box-shadow: 0 0 20px rgba(0, 0, 0, 0.5);
  touch-action: none;
  max-width: 100vw;
  max-height: 100vh;
  box-shadow: 0 0 20px rgba(0, 0, 0, 0.5);
```

✓ Meets all six criteria
- 3 net lines deleted ≤ 5
- Pure deletion of duplicates
- Single file (style.css)
- No functional change
- Task ID included
- (direct-fix) tag included

### Non-Compliant Examples

```diff
// Commit: feat(game): improve bird rendering (CD-050)
- function render() {
+ function render() {
+   optimizeTransform();
-  }
+   }
```
❌ Fails criterion 4 (adds function call — behavior change)

```diff
// Commit: chore(build): update config (CD-051)
- version: 1.0
+ version: 1.1
```
❌ Fails criteria: Too vague, not obvious cleanup, affects build semantics

---

## Implementation

See `docs/branching-policy.md` for the formal policy text and guidance.

---

## Related Documents

- **Policy:** `docs/branching-policy.md`
- **Task:** CD-076 (docs/branching-policy.md implementation)
- **Revision:** CD-145 (accuracy fixes to branching policy)
