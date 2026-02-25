# AR-019: Branch Hygiene Audit & Stale Branch Cleanup

**Author:** architect-2
**Date:** 2026-02-24
**Status:** Design Complete
**Group:** GRP-010
**Source:** RV-007 code review of CD-004

---

## 1. Problem Statement

The branch `feat/bird-physics-rendering` (created for CD-004: bird physics and rendering) was never implemented. It remains at the initial skeleton commit (`e6bbdf2`), 38 commits behind `main`, with zero unique work. The actual bird physics and rendering was delivered through different tasks (CD-013, CD-015, CD-023).

A broader audit reveals this is **not an isolated case** — the repository has accumulated **82 total local branches**, of which **39 are `feat/*` branches**. Of those 39:

- **19 are stale** (strict ancestors of `main`, containing zero unique work)
- **20 are diverged** (have 1–2 unique commits not on `main`)

No remote is configured, so all branches are local-only.

---

## 2. Audit Results

### 2.1 Stale Branches (19) — Safe to Delete

These branches are strict ancestors of `main`. Every commit they contain is already on `main`. Deleting them with `git branch -d` is guaranteed safe (git itself enforces this).

| Branch | Behind Main | Notes |
|--------|------------|-------|
| `feat/bird-physics-rendering` | 38 | Original issue — CD-004 work done via CD-013/CD-015/CD-023 |
| `feat/independent-agents` | 40 | Old feature branch, fully merged |
| `feat/cd-020` | 1 | |
| `feat/cd-023` | 1 | |
| `feat/cd-028` | 1 | |
| `feat/cd-029` | 1 | |
| `feat/cd-032` | 1 | |
| `feat/cd-042` | 1 | |
| `feat/cd-074` | 1 | |
| `feat/rv-005` | 1 | |
| `feat/rv-007` | 1 | |
| `feat/rv-011` | 2 | |
| `feat/rv-012` | 0 | Points at same commit as main |
| `feat/rv-080` | 1 | |
| `feat/rv-085` | 1 | |
| `feat/rv-088` | 1 | |
| `feat/ts-074` | 1 | |
| `feat/ts-103` | 1 | |
| `feat/ts-104` | 1 | |

### 2.2 Diverged Branches (20) — Require Review

These branches have 1–2 commits not on `main`. They fall into categories:

**Category A: Superseded game logic fixes (likely safe to delete)**
- `feat/cd-008-scoring-system` — 2 ahead (scoring + ground rendering); scoring was re-done via CD-014/CD-015, ground via CD-013
- `feat/cd-019` — ceiling collision fix (1 ahead, 15 insertions); later ceiling behavior re-specified via AR-009
- `feat/cd-031` — render test updates (1 ahead); likely stale vs current tests
- `feat/cd-040` — ceiling test update (1 ahead); superseded by later ceiling rulings
- `feat/cd-041` — collision test update per AR-009 (1 ahead)
- `feat/cd-064` — ceiling test + BUG-001 removal (1 ahead)

**Category B: Pipe color QA test branches (likely safe to delete)**
Six near-identical QA verification branches for the same pipe color fix (CD-021):
- `feat/ts-055`, `feat/ts-061`, `feat/ts-089`, `feat/ts-092`, `feat/ts-095`, `feat/ts-098`, `feat/ts-100`

**Category C: Potentially valuable unmerged work (needs architect review)**
- `feat/cd-063` — worktree hooks fix (2-line change): `git rev-parse` for MERGE_HEAD path
- `feat/cd-076` — branching policy doc (54 insertions)
- `feat/cd-077` — trivial-fix exemption in coder system prompt (2 insertions)
- `feat/cd-079` — BUG-001 regression tests (unmerged)
- `feat/cd-080` — replace hardcoded `24` with `GROUND_HASH_SPACING`, `var→let` (3-line fix)
- `feat/rv-075` — review branch with accidental file cleanup (67 insertions)

---

## 3. Recommended Actions

### Phase 1: Delete 19 confirmed stale branches (Coder Task)
- Run `git branch -d <branch>` for all 19 stale branches
- This is a zero-risk operation; git will refuse if the branch has unmerged work
- Verify with `git branch --list 'feat/*'` after cleanup

### Phase 2: Audit diverged branches (Architect Task)
- Review each of the 20 diverged branches
- For each branch, classify as: **merge** (cherry-pick to main), **delete** (superseded), or **keep** (in-progress work)
- Focus especially on Category C branches which may contain valuable fixes
- Create follow-up coder tasks for any branches that should be merged

---

## 4. Process Improvement Recommendations

1. **Close task loops**: When work from a task (e.g., CD-004) is delivered under different task IDs, the original task should be explicitly marked as superseded on the board
2. **Branch lifecycle**: Branches should be deleted immediately after merge (consider a post-merge hook)
3. **Periodic hygiene**: Schedule branch audits when branch count exceeds ~20
