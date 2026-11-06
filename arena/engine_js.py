"""
JavaScript that arena/engine.py runs inside the hidden arena.ai page, and the
page action ids it needs. Kept apart from the engine's Python logic so
engine.py stays readable; engine.py imports every name below.
"""

# Runs inside arena.ai. Each push carries a sequence number because pywebview
# dispatches every js_api call on its own thread, so arrival order is not guaranteed.
_FETCH_JS = r"""
(function (rid, path, method, body, v3key, action, extraHeaders, wantCx, cxModuleId) {
  var seq = 0;
  function push(kind, data) { window.pywebview.api.push(rid, seq++, kind, data); }
  function wait(ms) { return new Promise(function (ok) { setTimeout(ok, ms); }); }
  function installCx() {
    var T = globalThis.TURBOPACK;
    if (!T || Array.isArray(T) || typeof T.push !== 'function') return;
    var PID = 987654321;
    try {
      T.push(['arena-personal-probe', PID, function (ctx) {
        try {
          window.__arenaCxCtx = ctx;
          window.__arenaCx = function () {
            var imp = typeof ctx.i === 'function' ? ctx.i : ctx.r;
            var m = imp.call(ctx, cxModuleId);
            return m.a(m.k());
          };
        } catch (e) { window.__arenaCxErr = String(e); }
      }]);
      T.push(['arena-personal-probe', {otherChunks: [], runtimeModuleIds: [PID]}]);
    } catch (e) {}
  }
  async function acquireCx() {
    if (typeof window.__arenaCx !== 'function') {
      installCx();
      for (var i = 0; i < 25 && typeof window.__arenaCx !== 'function'; i++) await wait(200);
    }
    if (typeof window.__arenaCx !== 'function') return null;
    try {
      var v = window.__arenaCx();
      return typeof v === 'string' && v.length > 0 ? v : null;
    } catch (e) { return null; }
  }
  (async function () {
    for (var i = 0; i < 50 && !(window.pywebview && window.pywebview.api && window.pywebview.api.push); i++) await wait(100);
    try {
      if (body && wantCx) {
        try {
          var cxv = await Promise.race([acquireCx(), wait(6000).then(function () { return null; })]);
          if (cxv) body.cx = cxv;
        } catch (e) {}
      }
      if (body && 'recaptchaV3Token' in body && !body.recaptchaV2Token) {
        var tag = document.querySelector('script[src*="recaptcha"][src*="render="]');
        var key = (tag && new URL(tag.src).searchParams.get('render')) || v3key;
        if (!window.grecaptcha && key) {
          await Promise.race([wait(8000), new Promise(function (ok) {
            var s = document.createElement('script');
            s.src = 'https://www.google.com/recaptcha/enterprise.js?render=' + key;
            s.onload = ok; s.onerror = ok; document.head.appendChild(s);
          })]);
        }
        var g = window.grecaptcha && (window.grecaptcha.enterprise || window.grecaptcha);
        if (g && g.execute) {
          await Promise.race([wait(8000), new Promise(function (ok) { g.ready ? g.ready(ok) : ok(); })]);
          try { body.recaptchaV3Token = await Promise.race([g.execute(key, {action: action}), wait(15000).then(function () { return null; })]); } catch (e) {}
        }
      }
      var res = await fetch(path, {method: method, credentials: 'include',
        headers: Object.assign({}, body ? {'Content-Type': 'application/json'} : {}, extraHeaders || {}),
        body: body ? JSON.stringify(body) : undefined});
      var hdrs = {};
      try {
        ['ratelimit-modality', 'ratelimit-reset', 'retry-after', 'ratelimit-remaining'].forEach(function (k) {
          var v = res.headers.get(k);
          if (v != null) hdrs[k] = v;
        });
      } catch (e) {}
      push('status', res.status + '|' + JSON.stringify(hdrs));
      if (res.body) {
        var rd = res.body.getReader(), dec = new TextDecoder();
        for (;;) {
          var r = await rd.read();
          if (r.done) break;
          push('chunk', dec.decode(r.value, {stream: true}));
        }
      }
      push('end', null);
    } catch (e) { push('error', String(e)); }
  })();
  return 1;
})(%s);
"""

# Shows arena's own reCAPTCHA v2 checkbox over the arena.ai page for the human to tick.
# The panel stays below Google's picture popup (z-index 2000000000) and is never removed while a check
# runs: removing the checkbox orphans the popup, and Verify then does nothing.
_CHECK_JS = r"""
(function (v2key, action) {
  if (document.getElementById('arena-desktop-check')) return 1;
  var box = document.createElement('div');
  box.id = 'arena-desktop-check';
  box.style.cssText = 'position:fixed;inset:0;z-index:1999999999;background:rgba(20,20,19,.88);' +
    'display:flex;align-items:center;justify-content:center;font:14px system-ui,sans-serif;color:#F4F0EB';
  box.innerHTML = '<div style="background:#2C2B28;padding:24px 28px;border-radius:12px;max-width:440px;text-align:center">' +
    '<h3 style="margin:0 0 8px;font-size:18px">Quick human check</h3>' +
    '<p style="margin:0 0 16px;opacity:.8">arena.ai asked for verification before sending your message. ' +
    'Tick the box. Arena Desktop resends your message when you are done.</p>' +
    '<div id="arena-desktop-v2" style="display:inline-block;min-height:78px"></div>' +
    '<p style="margin:14px 0 0;opacity:.6;font-size:12px">If pictures appear, pick them and press Verify. ' +
    'This pane closes by itself once arena.ai accepts the check.</p></div>';
  document.body.appendChild(box);
  if (!window.grecaptcha) {
    var s = document.createElement('script');
    s.src = 'https://www.google.com/recaptcha/enterprise.js?render=explicit';
    document.head.appendChild(s);
  }
  var tries = 0;
  (function render() {
    var g = window.grecaptcha && (window.grecaptcha.enterprise || window.grecaptcha);
    if (!g || !g.render) { if (++tries < 60) setTimeout(render, 250); return; }
    g.ready(function () {
      try {
        g.render('arena-desktop-v2', {sitekey: v2key, action: action, theme: 'dark',
          callback: function (t) { window.pywebview.api.challenge_solved(t); box.remove(); }});
      } catch (e) { document.getElementById('arena-desktop-v2').textContent = 'Checkbox unavailable: ' + e; }
    });
  })();
  return 1;
})(%s);
"""

_KEY_JS = ("(function(){var t=document.querySelector('script[src*=\"recaptcha\"][src*=\"render=\"]');"
           "return t?new URL(t.src).searchParams.get('render'):null})()")

# --- native Agent preflight ---------------------------------------------------
# Arena validates agent session creation with an encoded client-interaction
# blob (Field "cx": versioned bit-packed behavioral telemetry with integrity
# bits) that only the website's own client code can honestly produce.  The
# preflight probe asks the page's own client-interaction module (imported
# through the page's Turbopack chunk runtime, the same loader the site's
# chunks use) to compute a genuine blob for this submission — the exact
# `a(k())` pair the site's evaluation submit runs, which truthfully labels
# the submit kind "programmatic".  We never synthesize, patch or replay the
# blob; the page computes it from its own current state.
_CX_MODULE_ID = 385144  # client-interaction module in arena.ai's bundle

# Battle pairwise voting on arena.ai is a React Server Action
# (createPairwiseFeedback) guarded by a reCAPTCHA v3 token for the
# "pairwise_feedback" action.  The vote probe below recreates the site's
# own server reference through the page's React flight module and calls
# it with the vote arguments — the exact call the website's vote bar
# makes, token minted in-page — and reports the decoded result
# ({success, data:{modelAId, modelBId, revealedModels}}).
_VOTE_MODULE_ID = 29948  # React flight client module (createServerReference)
_VOTE_ACTION_ID = "40afa19157c4ee2a95868929c0392d3083ffb127df"  # fallback; probe rediscovers it
_VOTE_RECAPTCHA_ACTION = "pairwise_feedback"
# Archiving a chat in Arena's own sidebar calls the archiveEvaluationSession
# Server Action with the session id (no reCAPTCHA); agentic sessions use
# POST /api/chat/{id}/archive instead (see contracts.PATHS["chat_archive"]).
_ARCHIVE_ACTION_ID = "60c49d7488128c7ec8bcbf1d90f8c7d124cfb82e91"  # fallback; probe rediscovers it
_ARCHIVE_ACTION_NAME = "archiveEvaluationSession"

_PREFLIGHT_JS = r"""
(function (opts) {
  var trace = [];
  function push(obj) {
    try { window.pywebview.api.drive_event('preflight', JSON.stringify(obj)); } catch (e) {}
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function describeCtx(ctx) {
    try {
      var names = [];
      for (var k in ctx) names.push(k);
      return names.slice(0, 12).join(',');
    } catch (e) { return '?'; }
  }
  async function tokenProbe() {
    try {
      var tag = document.querySelector('script[src*="recaptcha"][src*="render="]');
      var key = (tag && new URL(tag.src).searchParams.get('render')) || opts.v3key;
      trace.push('v3key=' + (key ? 'page' : 'none'));
      if (!window.grecaptcha && key) {
        await Promise.race([sleep(8000), new Promise(function (ok) {
          var s = document.createElement('script');
          s.src = 'https://www.google.com/recaptcha/enterprise.js?render=' + key;
          s.onload = ok; s.onerror = ok; document.head.appendChild(s);
        })]);
      }
      var g = window.grecaptcha && (window.grecaptcha.enterprise || window.grecaptcha);
      trace.push('grecaptcha=' + (!!g));
      if (g && g.execute && key) {
        await Promise.race([sleep(8000), new Promise(function (ok) { g.ready ? g.ready(ok) : ok(); })]);
        var t = await Promise.race([g.execute(key, {action: opts.action}),
                                     sleep(15000).then(function () { return null; })]);
        return t ? t.length : 0;
      }
    } catch (e) { trace.push('token_err=' + String(e).slice(0, 80)); }
    return 0;
  }
  function installCx() {
    var T = globalThis.TURBOPACK;
    if (!T) { trace.push('turbopack=absent'); return; }
    if (Array.isArray(T)) { trace.push('turbopack=array_pending'); return; }
    if (typeof T.push !== 'function') { trace.push('turbopack=no_push'); return; }
    var PID = 987654321;
    try {
      T.push(['arena-personal-probe', PID, function (ctx) {
        try {
          window.__arenaCxCtx = ctx;
          window.__arenaCx = function () {
            var imp = typeof ctx.i === 'function' ? ctx.i : ctx.r;
            var m = imp.call(ctx, opts.moduleId);
            return m.a(m.k());
          };
        } catch (e) { window.__arenaCxErr = String(e); }
      }]);
      trace.push('probe_registered');
    } catch (e) { trace.push('reg_err=' + String(e).slice(0, 80)); return; }
    try {
      T.push(['arena-personal-probe', {otherChunks: [], runtimeModuleIds: [PID]}]);
      trace.push('probe_exec_pushed');
    } catch (e) { trace.push('exec_err=' + String(e).slice(0, 80)); }
  }
  async function acquireCx() {
    if (typeof window.__arenaCx !== 'function') {
      installCx();
      for (var i = 0; i < 25 && typeof window.__arenaCx !== 'function'; i++) await sleep(200);
    }
    if (typeof window.__arenaCx !== 'function') {
      trace.push('cx_fn=missing' + (window.__arenaCxErr ? ':' + String(window.__arenaCxErr).slice(0, 80) : ''));
      return null;
    }
    trace.push('ctx_keys=' + describeCtx(window.__arenaCxCtx || {}));
    try {
      var v = window.__arenaCx();
      trace.push('cx_type=' + typeof v);
      return typeof v === 'string' && v.length > 0 ? v : null;
    } catch (e) {
      trace.push('cx_call_err=' + String(e).slice(0, 120));
      return null;
    }
  }
  (async function () {
    var tokenLen = 0, cx = null;
    try { tokenLen = await tokenProbe(); } catch (e) { trace.push('token_outer=' + String(e).slice(0, 60)); }
    try { cx = await acquireCx(); } catch (e) { trace.push('cx_outer=' + String(e).slice(0, 60)); }
    push({cx: cx, cxLen: cx ? cx.length : 0, tokenLen: tokenLen, trace: trace});
  })();
  return 1;
})(__ARENA_OPTS__)
"""

_VOTE_JS = r"""
(function (opts) {
  var trace = [];
  function push(obj) {
    try { window.pywebview.api.drive_event('vote', JSON.stringify(obj)); } catch (e) {}
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  async function mintToken() {
    var tag = document.querySelector('script[src*="recaptcha"][src*="render="');
    var key = (tag && new URL(tag.src).searchParams.get('render')) || opts.v3key;
    if (!key) { trace.push('v3key=none'); return null; }
    if (!window.grecaptcha) {
      await Promise.race([sleep(8000), new Promise(function (ok) {
        var s = document.createElement('script');
        s.src = 'https://www.google.com/recaptcha/enterprise.js?render=' + key;
        s.onload = ok; s.onerror = ok; document.head.appendChild(s);
      })]);
    }
    var g = window.grecaptcha && (window.grecaptcha.enterprise || window.grecaptcha);
    if (!g || !g.execute) { trace.push('grecaptcha=missing'); return null; }
    await Promise.race([sleep(8000), new Promise(function (ok) { g.ready ? g.ready(ok) : ok(); })]);
    try {
      var t = await Promise.race([g.execute(key, {action: opts.recaptchaAction}),
                                  sleep(15000).then(function () { return null; })]);
      trace.push('token=' + (t ? t.length : 0));
      return t || null;
    } catch (e) { trace.push('token_err=' + String(e).slice(0, 60)); return null; }
  }
  function actionIdFromChunk(text) {
    // Turbopack wraps the binding: (0,s.createServerReference)("40ab…",
    // s.callServer,void 0,s.findSourceMapURL,"createPairwiseFeedback").
    // Older bundles called createServerReference("40ab…", …) directly.
    if (text.indexOf('createPairwiseFeedback') === -1) return null;
    var m = /createServerReference\)\("([0-9a-f]{32,64})"[^)]*?"createPairwiseFeedback"/.exec(text)
      || /createServerReference\("([0-9a-f]{32,64})"[^)]*?"createPairwiseFeedback"/.exec(text);
    return m ? m[1] : null;
  }
  async function chunkUrls() {
    var seen = {}, urls = [];
    function add(u) {
      if (u && u.indexOf('/chunks/') !== -1 && u.slice(-3) === '.js' && !seen[u]) { seen[u] = 1; urls.push(u); }
    }
    try {
      performance.getEntriesByType('resource').forEach(function (e) { add(e.name); });
    } catch (e) {}
    try {
      Array.prototype.forEach.call(document.scripts, function (s) { add(s.src); });
    } catch (e) {}
    // The vote chunk may not be loaded on the current page yet; the home
    // document lists every chunk the current deployment can serve.
    try {
      var html = await (await fetch('/', {credentials: 'include'})).text();
      var re = /\/_next\/static\/[A-Za-z0-9_./-]*?\.js/g, hit;
      while ((hit = re.exec(html))) add(location.origin + hit[0]);
    } catch (e) { trace.push('html_chunks_err=' + String(e).slice(0, 40)); }
    return urls;
  }
  async function discoverActionId(force) {
    if (!force && window.__arenaVoteActionId) return window.__arenaVoteActionId;
    try {
      var urls = await chunkUrls();
      for (var i = 0; i < urls.length; i++) {
        try {
          var text = await (await fetch(urls[i])).text();
          var id = actionIdFromChunk(text);
          if (id) {
            window.__arenaVoteActionId = id;
            trace.push('action=discovered');
            return id;
          }
        } catch (e) {}
      }
    } catch (e) { trace.push('discover_err=' + String(e).slice(0, 60)); }
    trace.push('action=fallback');
    return opts.actionId;
  }
  function installVote(actionId) {
    var T = globalThis.TURBOPACK;
    if (!T || Array.isArray(T) || typeof T.push !== 'function') { trace.push('turbopack=missing'); return; }
    var PID = 987654322;
    try {
      T.push(['arena-personal-vote-probe', PID, function (ctx) {
        try {
          var imp = typeof ctx.i === 'function' ? ctx.i : ctx.r;
          var m = imp.call(ctx, opts.moduleId);
          var ref = m.createServerReference(actionId, m.callServer, undefined,
                                            m.findSourceMapURL, 'createPairwiseFeedback');
          window.__arenaVote = function (args) { return ref(args); };
        } catch (e) { window.__arenaVoteErr = String(e); }
      }]);
      T.push(['arena-personal-vote-probe', {otherChunks: [], runtimeModuleIds: [PID]}]);
      trace.push('vote_probe_pushed');
    } catch (e) { trace.push('install_err=' + String(e).slice(0, 80)); }
  }
  function isStaleAction(text) {
    return /UnrecognizedAction|was not found on the server|failed-to-find-server-action/.test(String(text || ''));
  }
  async function attempt(forceRediscover) {
    var actionId = await discoverActionId(forceRediscover);
    if (typeof window.__arenaVote !== 'function' || window.__arenaVoteIdFor !== actionId) {
      installVote(actionId);
      window.__arenaVoteIdFor = actionId;
      for (var i = 0; i < 25 && typeof window.__arenaVote !== 'function'; i++) await sleep(200);
    }
    if (typeof window.__arenaVote !== 'function') {
      return {error: 'vote_fn_missing' + (window.__arenaVoteErr ? ':' + String(window.__arenaVoteErr).slice(0, 100) : '')};
    }
    var token = null;
    try { token = await mintToken(); } catch (e) { trace.push('mint_outer=' + String(e).slice(0, 60)); }
    var args = {};
    for (var k in opts.args) args[k] = opts.args[k];
    args.recaptchaV3Token = token || null;
    try {
      var res = await window.__arenaVote(args);
      return {res: res};
    } catch (e) {
      return {error: String(e).slice(0, 300)};
    }
  }
  (async function () {
    try {
      var out = await attempt(false);
      if (isStaleAction(out.error) || (out.res && isStaleAction(out.res.error))) {
        // Arena redeployed and the action id rotated: drop every cached
        // reference, rediscover from the fresh deployment, retry once.
        trace.push('action=stale_retry');
        window.__arenaVoteActionId = null;
        window.__arenaVote = undefined;
        window.__arenaVoteIdFor = null;
        out = await attempt(true);
      }
      if (out.error) {
        push({ok: false, error: out.error, trace: trace});
        return;
      }
      var res = out.res;
      var slim = {success: false, error: null, modelAId: null, modelBId: null, arenaId: null, revealed: []};
      try {
        slim.success = !!(res && res.success);
        slim.error = res && res.error ? String(res.error).slice(0, 200) : null;
        if (res && res.data) {
          slim.modelAId = res.data.modelAId || null;
          slim.modelBId = res.data.modelBId || null;
          slim.arenaId = res.data.arenaId || null;
          if (Array.isArray(res.data.revealedModels)) {
            slim.revealed = res.data.revealedModels.slice(0, 8).map(function (mm) {
              return {id: mm && (mm.id || mm.model_id) || null,
                      name: mm && mm.name || null,
                      org: mm && (mm.organization || mm.org_display || mm.org) || null};
            });
          }
        }
      } catch (e) { slim.error = 'shape:' + String(e).slice(0, 100); }
      trace.push('success=' + slim.success);
      push({ok: true, result: slim, trace: trace});
    } catch (e) {
      push({ok: false, error: String(e).slice(0, 200), trace: trace});
    }
  })();
  return 1;
})(__ARENA_OPTS__)
"""

_ARCHIVE_JS = r"""
(function (opts) {
  var trace = [];
  function push(obj) {
    try { window.pywebview.api.drive_event('archive', JSON.stringify(obj)); } catch (e) {}
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function actionIdFromChunk(text) {
    if (text.indexOf(opts.actionName) === -1) return null;
    var m = new RegExp('createServerReference\\)\\("([0-9a-f]{32,64})"[^)]*?"' + opts.actionName + '"').exec(text)
      || new RegExp('createServerReference\\("([0-9a-f]{32,64})"[^)]*?"' + opts.actionName + '"').exec(text);
    return m ? m[1] : null;
  }
  async function chunkUrls() {
    var seen = {}, urls = [];
    function add(u) {
      if (u && u.indexOf('/chunks/') !== -1 && u.slice(-3) === '.js' && !seen[u]) { seen[u] = 1; urls.push(u); }
    }
    try { performance.getEntriesByType('resource').forEach(function (e) { add(e.name); }); } catch (e) {}
    try { Array.prototype.forEach.call(document.scripts, function (s) { add(s.src); }); } catch (e) {}
    try {
      var html = await (await fetch('/', {credentials: 'include'})).text();
      var re = /\/_next\/static\/[A-Za-z0-9_./-]*?\.js/g, hit;
      while ((hit = re.exec(html))) add(location.origin + hit[0]);
    } catch (e) { trace.push('html_chunks_err=' + String(e).slice(0, 40)); }
    return urls;
  }
  async function discoverActionId(force) {
    var cacheKey = '__arenaAction_' + opts.actionName;
    if (!force && window[cacheKey]) return window[cacheKey];
    try {
      var urls = await chunkUrls();
      for (var i = 0; i < urls.length; i++) {
        try {
          var text = await (await fetch(urls[i])).text();
          var id = actionIdFromChunk(text);
          if (id) { window[cacheKey] = id; trace.push('action=discovered'); return id; }
        } catch (e) {}
      }
    } catch (e) { trace.push('discover_err=' + String(e).slice(0, 60)); }
    trace.push('action=fallback');
    return opts.actionId;
  }
  function install(actionId) {
    var T = globalThis.TURBOPACK;
    if (!T || Array.isArray(T) || typeof T.push !== 'function') { trace.push('turbopack=missing'); return; }
    var PID = 987654323;
    try {
      T.push(['arena-personal-archive-probe', PID, function (ctx) {
        try {
          var imp = typeof ctx.i === 'function' ? ctx.i : ctx.r;
          var m = imp.call(ctx, opts.moduleId);
          var ref = m.createServerReference(actionId, m.callServer, undefined,
                                            m.findSourceMapURL, opts.actionName);
          window.__arenaArchive = function (sessionId) { return ref(sessionId); };
        } catch (e) { window.__arenaArchiveErr = String(e); }
      }]);
      T.push(['arena-personal-archive-probe', {otherChunks: [], runtimeModuleIds: [PID]}]);
      trace.push('archive_probe_pushed');
    } catch (e) { trace.push('install_err=' + String(e).slice(0, 80)); }
  }
  function isStaleAction(text) {
    return /UnrecognizedAction|was not found on the server|failed-to-find-server-action/.test(String(text || ''));
  }
  async function attempt(forceRediscover) {
    var actionId = await discoverActionId(forceRediscover);
    if (typeof window.__arenaArchive !== 'function' || window.__arenaArchiveIdFor !== actionId) {
      install(actionId);
      window.__arenaArchiveIdFor = actionId;
      for (var i = 0; i < 25 && typeof window.__arenaArchive !== 'function'; i++) await sleep(200);
    }
    if (typeof window.__arenaArchive !== 'function') {
      return {error: 'archive_fn_missing' + (window.__arenaArchiveErr ? ':' + String(window.__arenaArchiveErr).slice(0, 100) : '')};
    }
    try { return {res: await window.__arenaArchive(opts.sessionId)}; }
    catch (e) { return {error: String(e).slice(0, 300)}; }
  }
  (async function () {
    try {
      var out = await attempt(false);
      if (isStaleAction(out.error) || (out.res && isStaleAction(out.res.error))) {
        trace.push('action=stale_retry');
        window['__arenaAction_' + opts.actionName] = null;
        window.__arenaArchive = undefined;
        window.__arenaArchiveIdFor = null;
        out = await attempt(true);
      }
      if (out.error) { push({ok: false, error: out.error, trace: trace}); return; }
      var res = out.res || {};
      var success = res.success === undefined ? true : !!res.success;
      push({ok: success,
            error: success ? null : String(res.error || 'archive_refused').slice(0, 200),
            trace: trace});
    } catch (e) {
      push({ok: false, error: String(e).slice(0, 200), trace: trace});
    }
  })();
  return 1;
})(__ARENA_OPTS__)
"""

# --- native Agent "page drive" (kept, currently unused by the app) -----------
# Earlier approach: type into the page's own /agent composer and click its
# send button, observing the page's fetches.  The user asked for Direct-style
# API sends instead (v4.5), so this machinery stays only as tested plumbing:
# it synthesizes and modifies nothing in the page's requests.

# Observation-only fetch wrapper: reports create/append responses to Python.
_DRIVE_HOOK_JS = r"""
(function () {
  if (window.__arenaDriveHooked) return 1;
  window.__arenaDriveHooked = true;
  var origFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    return origFetch(input, init).then(function (res) {
      try {
        var url = '';
        if (typeof input === 'string') url = input;
        else if (input && input.url) url = input.url;
        var kind = null;
        if (url.indexOf('/nextjs-api/stream/create-chat') !== -1) kind = 'create';
        else if (url.indexOf('/api/coding-agent/sessions') !== -1) kind = 'create';
        else if (url.indexOf('/in/append') !== -1) kind = 'append';
        if (kind) {
          var status = res.status;
          res.clone().text().then(function (text) {
            try {
              window.pywebview.api.drive_event(kind, JSON.stringify({
                url: url.slice(0, 200), status: status,
                text: String(text || '').slice(0, 32768)
              }));
            } catch (e) {}
          }).catch(function () {});
        }
      } catch (e) {}
      return res;
    });
  };
  return 1;
})()
"""

# One JSON snapshot of everything the drive loop polls.
_DRIVE_STATE_JS = r"""
(function () {
  function vis(el) {
    var r = el.getBoundingClientRect();
    return r.width > 30 && r.height > 10;
  }
  var tas = Array.prototype.filter.call(document.querySelectorAll('textarea'), vis);
  var ces = Array.prototype.filter.call(document.querySelectorAll('[contenteditable="true"]'), vis);
  var comp = tas.length ? tas[tas.length - 1] : (ces.length ? ces[ces.length - 1] : null);
  var val = null;
  if (comp) val = (comp.value !== undefined ? comp.value : comp.textContent) || '';
  var v2 = false;
  var frames = document.querySelectorAll('iframe[src*="recaptcha"]');
  for (var i = 0; i < frames.length; i++) {
    var r = frames[i].getBoundingClientRect();
    if (r.width > 60 && r.height > 60) { v2 = true; break; }
  }
  return JSON.stringify({
    path: location.pathname, title: document.title,
    textareas: tas.length, editables: ces.length,
    hooked: !!window.__arenaDriveHooked,
    composerEmpty: val === null ? null : val.trim() === '',
    v2: v2
  });
})()
"""

# Types the message into the page composer and submits it the page's own way.
# Layered heuristics (textarea or contenteditable; execCommand typing with a
# native-setter fallback; send button with an Enter fallback) and a diagnostic
# report through the bridge either way.
_DRIVE_SEND_JS = r"""
(function (text) {
  function push(kind, obj) {
    try { window.pywebview.api.drive_event(kind, JSON.stringify(obj)); } catch (e) {}
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function vis(el) {
    var r = el.getBoundingClientRect();
    return r.width > 30 && r.height > 10 && !el.disabled;
  }
  function findComposer() {
    var tas = Array.prototype.filter.call(document.querySelectorAll('textarea'), vis);
    if (tas.length) return tas[tas.length - 1];
    var ces = Array.prototype.filter.call(document.querySelectorAll('[contenteditable="true"]'), vis);
    return ces.length ? ces[ces.length - 1] : null;
  }
  function getVal(el) { return (el.value !== undefined ? el.value : el.textContent) || ''; }
  function head(s) { return s.slice(0, 24); }
  function setNative(el) {
    if (el.value !== undefined) {
      var proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, text);
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
    } else {
      el.focus();
      try {
        var sel = window.getSelection();
        sel.selectAllChildren(el);
        sel.collapseToEnd();
      } catch (e) {}
      el.textContent = text;
      el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: text}));
    }
  }
  async function typeInto(el) {
    el.focus();
    el.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, cancelable: true}));
    el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
    el.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, cancelable: true}));
    el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
    el.focus();
    await sleep(150);
    var i = 0;
    while (i < text.length && i < 1600) {
      var chunk = text.slice(i, i + 8);
      var ch = chunk.charAt(chunk.length - 1);
      el.dispatchEvent(new KeyboardEvent('keydown', {key: ch, bubbles: true, cancelable: true}));
      var inserted = false;
      try { inserted = document.execCommand('insertText', false, chunk); } catch (e) {}
      el.dispatchEvent(new KeyboardEvent('keyup', {key: ch, bubbles: true, cancelable: true}));
      if (!inserted) break;
      i += chunk.length;
      await sleep(12);
    }
    if (i < text.length || getVal(el).indexOf(head(text)) === -1) setNative(el);
    return getVal(el).indexOf(head(text)) !== -1;
  }
  function findSend(el) {
    var scopes = [];
    var form = el.closest ? el.closest('form') : null;
    if (form) scopes.push(form);
    var node = el;
    for (var i = 0; i < 7 && node; i++) { node = node.parentElement; if (node) scopes.push(node); }
    function label(b) {
      return ((b.getAttribute('aria-label') || '') + ' ' +
              (b.getAttribute('title') || '') + ' ' + (b.textContent || '')).toLowerCase();
    }
    function buttons(scope) {
      return Array.prototype.filter.call(scope.querySelectorAll('button'), vis);
    }
    // Pass 1: an explicitly labelled send button anywhere near the composer.
    for (var s = 0; s < scopes.length; s++) {
      var btns = buttons(scopes[s]);
      for (var j = 0; j < btns.length; j++) {
        var lb = label(btns[j]);
        if (lb.indexOf('send') !== -1) return btns[j];
      }
    }
    // Pass 2: a submit button inside the composer form.
    if (form) {
      var fbtns = buttons(form);
      for (var k = 0; k < fbtns.length; k++) if (fbtns[k].type === 'submit') return fbtns[k];
      if (fbtns.length) return fbtns[fbtns.length - 1];
    }
    // Pass 3: the last button of the nearest container that has any.
    for (var s2 = 0; s2 < scopes.length; s2++) {
      var bs = buttons(scopes[s2]);
      if (bs.length) return bs[bs.length - 1];
    }
    return null;
  }
  (async function () {
    var el = null;
    for (var i = 0; i < 30 && !el; i++) { el = findComposer(); if (!el) await sleep(400); }
    if (!el) {
      push('composer', {ok: false, stage: 'find', path: location.pathname, title: document.title});
      return;
    }
    var set = await typeInto(el);
    await sleep(800);
    var btn = findSend(el);
    var via = 'none';
    if (btn) { btn.click(); via = 'button'; }
    else {
      var types = ['keydown', 'keypress', 'keyup'];
      for (var t = 0; t < types.length; t++) {
        el.dispatchEvent(new KeyboardEvent(types[t], {
          key: 'Enter', code: 'Enter', bubbles: true, cancelable: true
        }));
      }
      via = 'enter';
    }
    await sleep(1500);
    push('composer', {
      ok: true, set: set, via: via, path: location.pathname,
      cleared: getVal(el).trim() === ''
    });
  })();
  return 1;
})(__ARENA_TEXT__)
"""
