# Audit Findings — Frontend JS

**Files reviewed:** dashboard-core.js, dashboard-ui.js, features.js, intelligence.js, metrics.js
**Reviewer:** audit-agent-13

## XSS-sink inventory
- `.innerHTML =` total: 323 (core 25, ui 30, features 124, intelligence 128, metrics 16)
- `.outerHTML =`: 0
- `insertAdjacentHTML`: 0
- `document.write`: 0
- `eval(`: 0
- `new Function(`: 0
- `.srcdoc =`: 0
- String-form timers: 0
- Hardcoded secrets / Authorization/Bearer headers: 0

## Finding 1 — v2RenderTable injects cell HTML unescaped (helper unsafe-by-default)
- **Severity:** HIGH
- **Category:** security
- **Location:** intelligence.js:432-443; called ~90 times across intelligence.js and features.js
- **Finding:** `rows.forEach(r => r.forEach(cell => html += '<td>' + cell + '</td>'))` — zero escaping. Safety depends on every caller remembering to pre-escape every cell.
- **Impact:** One forgotten `escapeHtml` on LLM-/server-supplied field → stored XSS in dashboard origin.
- **Fix:** Make helper escape by default; accept `{html: "..."}` for pre-formatted cells.

## Finding 2 — Inline onclick handlers use escapeHtml inside JS string literals
- **Severity:** HIGH
- **Category:** security
- **Location:** dashboard-core.js:559, 946, 953, 971; dashboard-ui.js:22, 354, 386; features.js multiple (intelligence.js:364 is the only caller that correctly escapes `'`)
- **Finding:** Pattern `onclick="fn('"+escapeHtml(x)+"')"`. `escapeHtml` does not escape `'` — an id containing a single quote breaks out of the JS string argument.
- **Impact:** Attacker-influenced ids (instance_id, task id, role, group, file path, kg node name) → arbitrary JS execution.
- **Fix:** Delegated listeners + `data-*` attributes.

## Finding 3 — Raw template-literal innerHTML without escaping
- **Severity:** MEDIUM
- **Category:** security
- **Location:** dashboard-ui.js:1034, 1031; dashboard-core.js:915
- **Finding:** Template-literal innerHTML assignments interpolate variables without escapeHtml.
- **Impact:** OK for currently constant strings; XSS once a field becomes server-sourced.
- **Fix:** escapeHtml on all interpolations; lint-ban template-literal innerHTML.

## Finding 4 — DOMPurify/marked is the only XSS defense for LLM content, no fallback or locked config
- **Severity:** MEDIUM
- **Category:** security / error-handling
- **Location:** dashboard-ui.js:97, 103, 114, 300; dashboard-core.js:2
- **Finding:** Chat tokens/bubbles/task descriptions call `DOMPurify.sanitize(marked.parse(text))`. No feature-detect, no explicit `ALLOWED_TAGS`/`FORBID_ATTR` config, no safeMarkdown() helper.
- **Impact:** Future `ALLOWED_TAGS` tweak or library-load failure silently reopens an XSS vector for all LLM output.
- **Fix:** Centralise in one helper with explicit locked config and escapeHtml fallback.

## Finding 5 — SVG / style attribute context injection (latent)
- **Severity:** MEDIUM
- **Category:** security
- **Location:** dashboard-core.js:833-888 SVG assembly; metrics.js:884-888, 977
- **Finding:** Color values concatenated into SVG attrs / inline `style=`. ROLE_COLORS constant today; when user-editable themes land this becomes attribute-context XSS.
- **Fix:** Strict color regex validation before concatenation; always quote SVG attrs.

## Finding 6 — No CSRF token / credentials specified on state-changing fetch calls
- **Severity:** LOW
- **Category:** api-contract / security
- **Location:** 139 `fetch(` sites across all 5 files
- **Finding:** POST/PUT/DELETE requests without CSRF header, without explicit `credentials`, errors swallowed into "X unavailable" innerHTML.
- **Fix:** `tbFetch(url, opts)` wrapper with CSRF, credentials, centralised error telemetry.

## Finding 7 — Standup JSON.stringify fallback can flood DOM
- **Severity:** LOW
- **Category:** edge-case
- **Location:** intelligence.js:952-955, 1171-1174
- **Finding:** `escapeHtml(JSON.stringify(obj))` when field isn't a string; arbitrary-sized blobs render inline.
- **Fix:** Truncate; normalise server schema.

## Finding 8 — Theme bootstrap assumes DOM ready
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** dashboard-ui.js:980-982
- **Finding:** Reads localStorage and sets `themeIcon.innerHTML` outside any DOMContentLoaded gate.
- **Fix:** Gate behind DOMContentLoaded.

## Systemic issues
- **323 innerHTML sites / no template layer:** safety is 100% discipline across 335KB of vanilla JS. A single forgotten `escapeHtml` = XSS. Adopt a tagged-template helper that escapes interpolations by default.
- **Inline `onclick="fn('…')"` + escapeHtml-not-JS-escape** is the most common real bug: breaks on any value containing `'`. Only one call site handles it correctly. Refactor to `data-*` + delegated listeners.
- **`v2RenderTable` is unsafe-by-default** and used ~90 times — highest-leverage single fix.
- **DOMPurify config is implicit.** Centralise safeMarkdown() with locked-down options; add SRI/feature-detect.
- **No CSP hooks** and pervasive inline onclick= means CSP hardening is blocked until handlers are refactored.
- **Fetch error handling uniformly weak** — errors silently become empty states; no CSRF; no central wrapper.

**Counts:** HIGH 2, MEDIUM 3, LOW 3 (total 8)
