/* Project scoping for detail pages (home redesign).
 *
 * When a detail page is opened as ?project=<id>, transparently tag every
 * same-origin /api/ request with the X-Taskbrew-Project header so the dashboard
 * resolves THIS project (running, or a read-only idle view of its DB when it is
 * stopped) instead of the focused one. This scopes the whole page without
 * editing each fetch() call. Scope persists per-tab via sessionStorage because
 * some pages strip ?project from the URL via history.replaceState, which would
 * otherwise lose scope on reload.
 *
 * Must load synchronously in <head> BEFORE the page's own scripts, so the fetch
 * override is installed before any data fetch runs.
 *
 * NOTE: WebSocket live updates are scoped separately (the WS URL carries the
 * project). Header scoping covers REST; data is correct on load and on poll.
 */
(function () {
  var pid = null;
  try { pid = new URLSearchParams(window.location.search).get('project'); } catch (e) {}
  try {
    if (pid) sessionStorage.setItem('tb_scope_project', pid);
    else pid = sessionStorage.getItem('tb_scope_project');
  } catch (e) {}
  if (!pid) return;
  window.__TB_PROJECT__ = pid;
  var _fetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    try {
      var raw = (typeof input === 'string') ? input : (input && input.url) || '';
      var u = new URL(raw, window.location.origin);
      if (u.origin === window.location.origin && u.pathname.lastIndexOf('/api/', 0) === 0) {
        init = init || {};
        var h = new Headers(init.headers || (typeof input !== 'string' && input.headers) || {});
        if (!h.has('X-Taskbrew-Project')) h.set('X-Taskbrew-Project', pid);
        init.headers = h;
      }
    } catch (e) {}
    return _fetch(input, init);
  };
  /* Scope the live-events WebSocket too: append ?project so the server routes
     only this project's events to the page (the chat WS at /ws/chat/* is left
     untouched). */
  if (window.WebSocket) {
    var _WS = window.WebSocket;
    var Patched = function (url, protocols) {
      try {
        var u = new URL(url, window.location.origin);
        // Compare host, not origin: WS urls use the ws:// scheme so their
        // origin never equals the page's http(s):// origin.
        if (u.host === window.location.host && u.pathname === '/ws' && !u.searchParams.has('project')) {
          u.searchParams.set('project', pid);
          url = u.toString();
        }
      } catch (e) {}
      return (protocols !== undefined) ? new _WS(url, protocols) : new _WS(url);
    };
    Patched.prototype = _WS.prototype;
    Patched.CONNECTING = _WS.CONNECTING; Patched.OPEN = _WS.OPEN;
    Patched.CLOSING = _WS.CLOSING; Patched.CLOSED = _WS.CLOSED;
    window.WebSocket = Patched;
  }

  /* Preserve scope across internal navigation between detail pages. */
  document.addEventListener('DOMContentLoaded', function () {
    try {
      var as = document.querySelectorAll('a[href]');
      for (var i = 0; i < as.length; i++) {
        var a = as[i], href = a.getAttribute('href') || '';
        if (/^\/(advanced|metrics|settings|costs|questions|trace)(\b|\/|\?|$)/.test(href) && href.indexOf('project=') === -1) {
          a.setAttribute('href', href + (href.indexOf('?') === -1 ? '?' : '&') + 'project=' + encodeURIComponent(pid));
        }
      }
    } catch (e) {}
  });
})();
