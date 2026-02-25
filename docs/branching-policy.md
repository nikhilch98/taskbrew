# Branching Policy

This document formalizes the project's branching rules, including the trivial-fix
exemption approved in AR-017.

---

## Default Branching Rule

All changes **MUST** go through a dedicated branch before being merged to `main`:

| Branch type | Naming convention | Use case |
|---|---|---|
| Feature branch | `feature/<task-id>-<short-desc>` | New features, enhancements |
| Bugfix branch | `fix/<task-id>-<short-desc>` | Bug fixes, corrections |

Branches are merged to `main` via **merge commit** (no fast-forward).

---

## Trivial-Fix Exemption

A change may be committed **directly to `main`** if — and only if — it meets
**all six** of the following criteria:

1. **≤ 5 net lines changed** (additions + deletions).
2. **Pure deletion of dead/duplicate code, OR a comment-only change.**
3. **Affects a single file.**
4. **Introduces NO functional behavior change** (runtime semantics unchanged).
5. **Commit message includes the task ID** (e.g., `CD-067`).
6. **Commit message includes the `(direct-fix)` tag.**

### Example commit message

```
fix(css): remove duplicate properties (CD-XXX) (direct-fix)
```

> Note: A fully compliant, real-world example is being identified and will be added
> in a follow-up commit. All future direct-fix commits must include the `(direct-fix)` tag
> in the commit message to demonstrate compliance with all six criteria.

---

## Guidance

> **When in doubt, use a branch.**
> The exemption exists for obvious, zero-risk cleanups — not for "small" features
> or logic changes.

---

## Reference

- Full policy text: `artifacts/ARCH-REVIEW-017-branching-policy-trivial-fix-exemptions.md` Section 6
- Compliant examples will be maintained in the reference artifact
