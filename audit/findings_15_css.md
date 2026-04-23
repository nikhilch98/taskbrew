# Audit Findings — CSS

**Files reviewed:** dashboard/static/css/main.css, metrics.css
**Reviewer:** audit-agent-15

## CSS inventory
- External url(https://...) references: 0
- External @import: 0
- !important count: 14 (main.css 6, metrics.css 8)
- Media queries present: yes (main.css 6, metrics.css 5)

## Finding 1 — z-index escalation without a documented scale
- **Severity:** LOW
- **Category:** perf
- **Location:** main.css:312, 1896, 3156; metrics.css:860 (all 9999 or 10000)
- **Finding:** Stacking uses ad-hoc values spanning 0–10000 (9+ distinct tiers) with no tokens.
- **Impact:** Future overlays risk z-index wars.
- **Fix:** Define CSS vars (--z-modal, --z-toast) and replace literals.

## Systemic issues
- No supply-chain risk (zero external url/@import).
- `!important` count low (14) but non-zero — minor specificity workarounds.
- Z-index has no documented scale.
- Responsive design is light (11 media queries across ~127KB).
- main.css at 107KB is large for one file; consider splitting.

**Counts:** LOW 1 (total 1)
