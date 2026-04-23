# Audit Findings — HTML Templates
**Files reviewed:**
- src/taskbrew/dashboard/templates/index.html (621.8 KB)
- src/taskbrew/dashboard/templates/settings.html (191.7 KB)
- src/taskbrew/dashboard/templates/metrics.html (109.1 KB)
- src/taskbrew/dashboard/templates/costs.html (1.7 KB)

**Reviewer:** audit-agent-14

## Template risk inventory
- Total `|safe` occurrences: **0** (index=0, settings=0, metrics=0, costs=0)
- Total `{% autoescape false %}` blocks: **0**
- Total `{{ ... }}` Jinja expressions anywhere: **0** (templates are fully static shells — all dynamic data is fetched by the JS bundle at runtime and rendered client-side)
- `{% include %}` / `{% import %}` / `{% extends %}`: **0** — no template composition, no user-controlled include target risk
- CSP meta tag present: **NO** (no `<meta http-equiv="Content-Security-Policy">` in any template; CSP would have to be set via HTTP header in the FastAPI layer — see slice 10 for dashboard.py `add_security_headers`)
- Inline `<script>` tag count: **3** (one per file in index/settings/metrics; costs.html has none — uses external `/static/js/costs.js`)
- External `<script src>` tags: **4** total (index=2, settings=1, metrics=1, costs=0)
- Jinja expressions inside `<script>`: **0** (cannot occur — no `{{ }}` anywhere)
- Event-handler attributes containing Jinja (`onclick="{{...}}"`, etc.): **0**
- `<form method="POST">` tags: **0** — the entire dashboard is API-driven via `fetch()`; no classic HTML form submissions, so CSRF tokens are not a template-layer concern (CSRF must instead be enforced at the FastAPI/fetch header layer, see slice 10)
- `javascript:` / `style="expression(...)"`: **0**
- `target="_blank"` (rel=noopener risk): **0**

### External CDN domains loaded (unique)
1. `https://fonts.googleapis.com` — Google Fonts CSS (all 4 files, via `<link rel="preconnect">` and stylesheet)
2. `https://fonts.gstatic.com` — Google Fonts font-file origin (all 4 files, via `<link rel="preconnect" crossorigin>`)
3. `https://cdn.jsdelivr.net` — JS libraries: `marked@15.0.12`, `dompurify@3.2.4`, `chart.js@4.5.1` (index, settings, metrics)

### SRI (Subresource Integrity) presence on `<script src>`
- index.html: 2/2 have `integrity=` + `crossorigin=` (marked.min.js, purify.min.js)
- settings.html: 1/1 has SRI (purify.min.js)
- metrics.html: 1/1 has SRI (chart.umd.min.js)
- costs.html: 0 external scripts (same-origin only)
- **All external scripts carry SRI** — good.
- `<link rel="stylesheet" href="https://fonts.googleapis.com/...">` has **no `integrity=`** in any of the 4 files. SRI on Google-Fonts CSS is often omitted because the CSS is dynamically generated per browser, so SRI would break legitimate requests — this is an accepted trade-off but still worth noting.

## Finding 1 — No Content-Security-Policy meta tag anywhere
- **Severity:** M
- **Category:** security
- **Location:** all 4 templates (`<head>` of each)
- **Finding:** None of the 4 templates declare `<meta http-equiv="Content-Security-Policy" ...>`. If the FastAPI response headers also omit CSP (slice 10 should confirm), the dashboard runs with no script/style/connect/frame restrictions, so any residual XSS in the large `innerHTML =` JS paths (361 occurrences across the three big templates' inline scripts — see slice 13) has full access to `fetch`, cookies, and third-party origins.
- **Impact:** Absent CSP, a single unescaped user/LLM field rendered into the DOM becomes full account compromise (token exfiltration, arbitrary XHR to `/api/*`), with no second line of defense.
- **Fix:** Add a `<meta http-equiv="Content-Security-Policy">` (or HTTP header) with at minimum `default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'sha256-<hash-of-each-inline-block>'; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; connect-src 'self'; frame-ancestors 'none'; object-src 'none'` — note explicitly avoiding `'unsafe-inline'` for `script-src`.

## Finding 2 — Inline `<script>` blocks will force `'unsafe-inline'` or hash pinning when CSP is added
- **Severity:** M
- **Category:** security
- **Location:** index.html (1 inline block), settings.html (1), metrics.html (1)
- **Finding:** Each of the three large templates contains exactly one inline `<script>` block (the app bootstrap/UI code — tens of thousands of lines concatenated). When Finding 1 is fixed, CSP must either allow `'unsafe-inline'` for scripts (weak) or pin each inline block's SHA-256 hash (fragile — any edit to the block changes the hash and breaks the page until CSP is updated).
- **Impact:** Makes a strict CSP impractical; couples deploys of JS to deploys of CSP; encourages use of `'unsafe-inline'` which negates most of CSP's XSS value.
- **Fix:** Extract the inline blocks to `/static/js/dashboard.js`, `/static/js/settings.js`, `/static/js/metrics.js` (costs.html already does this correctly) so `script-src 'self'` alone suffices.

## Finding 3 — Google Fonts loaded without SRI on the stylesheet
- **Severity:** L
- **Category:** security
- **Location:** all 4 templates, `<link href="https://fonts.googleapis.com/css2?...">`
- **Finding:** The Google Fonts CSS response is a dynamic 3rd-party stylesheet, loaded with no SRI and no CSP `style-src` pin. A compromise of or MITM on `fonts.googleapis.com` can inject CSS capable of keylogging-via-attribute-selector and data exfiltration.
- **Impact:** Low individual likelihood, but amplifies Finding 1: without CSP and without SRI, the dashboard's security boundary is as strong as Google Fonts' CDN.
- **Fix:** Self-host the required font files under `/static/fonts/` and drop the cross-origin `<link>`; or accept the risk explicitly and document it.

## Finding 4 — Template files are enormous and ship inline JS/CSS that belong in `/static/`
- **Severity:** L
- **Category:** perf
- **Location:** index.html (621.8 KB), settings.html (191.7 KB), metrics.html (109.1 KB)
- **Finding:** Because all CSS and JS is inlined, every full page load re-downloads the entire asset bundle (~920 KB just for the three HTML files) with no HTTP caching benefit beyond the HTML itself; also forces the inline-script CSP problem in Finding 2.
- **Impact:** Slower cold loads, no incremental caching across dashboard/settings/metrics navigation, and CSP lockdown is blocked.
- **Fix:** Move inline `<script>` and `<style>` blocks to cacheable static files (mirroring the pattern already used by costs.html + `/static/js/costs.js` + `/static/css/costs.css`).

## Finding 5 — No CSRF token scaffolding because no POST forms exist — but the dashboard mutates state via `fetch` POST/DELETE
- **Severity:** L (at the template layer — true risk lives in slice 10 API + slice 13 JS)
- **Category:** security
- **Location:** N/A in templates (0 `<form method="POST">`)
- **Finding:** The absence of POST forms is not a clean bill of health: all state-changing operations are issued by client-side `fetch()` calls (see slice 13). Those must still defend against CSRF via `SameSite=Strict/Lax` cookies, a CSRF header token read from a meta tag, or Origin/Referer checks on the server. No CSRF meta tag (e.g., `<meta name="csrf-token">`) exists in any template — so if the dashboard relies on a browser cookie for auth, CSRF defense is currently exclusively at the server layer.
- **Impact:** If the FastAPI layer uses cookie auth without SameSite and without Origin checks, any third-party site can POST to `/api/...` with the user's credentials.
- **Fix:** Either (a) confirm auth is token-based via `Authorization` header (no cookie — CSRF not applicable), or (b) add `<meta name="csrf-token" content="{{ csrf_token }}">` to the head of each template and require clients to echo it in a header checked server-side.

## Systemic issues
- **Zero Jinja autoescape risk in templates**: there is literally no `{{ ... }}` Jinja expression, no `|safe`, no `{% autoescape false %}`, no `{% include %}`. All server-side-rendered XSS classes are *structurally impossible* from the template files alone — a very rare and positive finding. The entire XSS surface area has been pushed to client-side JS rendering (slice 13), which must be audited with `innerHTML` and `DOMPurify` correctness as the key axes.
- **No CSP anywhere** is the single largest template-layer gap. Combined with ~361 `innerHTML`/`.html()` call sites in the inline JS, the dashboard has no defense-in-depth against a client-side XSS escape. Adding CSP is a one-file change but is blocked on extracting inline scripts (Finding 2).
- **All three CDN scripts have SRI** (marked, dompurify, chart.js) — good hygiene. But SRI is absent on the Google Fonts CSS link, and pinned versions (`@15.0.12`, `@3.2.4`, `@4.5.1`) must be bumped manually when CVEs land — no automated supply-chain monitoring is evidenced.
- **Templates are essentially static HTML shells**, not Jinja-driven views. The Jinja dependency is unused — rendering them as plain static HTML via `StaticFiles` would simplify the FastAPI stack and eliminate a template-injection class entirely.
- **Inline everything (CSS + JS)** hurts both CSP adoption and caching. costs.html demonstrates the correct pattern (external CSS + external JS with `defer`); extending it to the other three templates is a mechanical refactor.
