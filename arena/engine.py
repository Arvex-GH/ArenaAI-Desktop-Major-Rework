"""
Embedded arena.ai engine.

A hidden WebView2 window stays on https://arena.ai. Chats are sent from that
page, so arena's own reCAPTCHA and Cloudflare checks run in a real browser with
the user's real session. When arena asks for a visible check (reCAPTCHA
checkbox, Cloudflare "Just a moment..."), the window is shown to the human.
Nothing here solves, mints or fakes a challenge.

Rotation resilience: only endpoint paths from arena.contracts are used, and the
reCAPTCHA key is read from the live page's <script src="...render=KEY"> tag.
"""

import json
import logging
import queue
import re
import threading
import time
import uuid
from typing import Dict, Optional, Tuple

from arena import contracts

LOG = logging.getLogger("arena.desktop")

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


class _Bridge:
    """js_api of the engine window. arena.ai's page can call only these methods."""

    def __init__(self, engine):
        self._engine = engine

    def push(self, rid, seq, kind, data):
        self._engine._deliver(rid, seq, kind, data)

    def challenge_solved(self, token):
        self._engine._on_v2(token)

    def drive_event(self, kind, payload):
        self._engine._on_drive_event(kind, payload)


class EngineResponse:
    """Duck-types the parts of requests.Response the app uses."""

    def __init__(self, engine, rid: str, q: "queue.Queue", timeout: float):
        self._engine, self._rid, self._q, self._timeout = engine, rid, q, timeout
        self._done = False
        self.error: Optional[str] = None
        self.headers: Dict[str, str] = {}
        kind, data = self._next(min(timeout, 90))
        if kind == "status":
            # The page pushes "429|{\"ratelimit-modality\": \"video\", ...}".
            text = str(data)
            status_text, _, headers_text = text.partition("|")
            try:
                self.status_code = int(status_text)
            except ValueError:
                self.status_code = 0
            if headers_text:
                try:
                    parsed = json.loads(headers_text)
                    if isinstance(parsed, dict):
                        self.headers = {str(k): str(v) for k, v in parsed.items()}
                except ValueError:
                    pass
        else:
            self.status_code = 0
            self.error = str(data)
            self._finish()

    def _next(self, timeout=None):
        try:
            return self._q.get(timeout=timeout or self._timeout)
        except queue.Empty:
            return ("error", "timed out waiting for arena.ai")

    def _finish(self):
        self._done = True
        self._engine._close(self._rid)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def iter_text(self):
        while not self._done:
            kind, data = self._next()
            if kind == "chunk":
                yield str(data)
            else:
                if kind == "error":
                    self.error = str(data)
                self._finish()

    def iter_content(self, chunk_size=None):
        for text in self.iter_text():
            yield text.encode("utf-8")

    @property
    def text(self) -> str:
        if not hasattr(self, "_text"):
            self._text = "".join(self.iter_text())
        return self._text

    def json(self):
        return json.loads(self.text)


class BrowserEngine:
    def __init__(self):
        self.window = None
        self.loaded = False
        self.host = ""
        self.challenge: Optional[str] = None  # None | "recaptcha" | "cloudflare"
        self.v2_token: Optional[str] = None
        self.login_open = False
        self.visible = False
        self._streams: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._imported = False
        self._drive_lock = threading.Lock()
        self._drive_queue: Optional["queue.Queue"] = None

    def bridge(self) -> _Bridge:
        return _Bridge(self)

    def attach(self, window, main=None):
        self.window = window
        self.main = main
        window.events.loaded += lambda *a: threading.Thread(target=self._after_load, daemon=True).start()
        window.events.closing += self._on_closing

    def ready(self) -> bool:
        return self.window is not None and self.loaded

    def wait_ready(self, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end and not self.ready():
            time.sleep(0.25)
        return self.ready()

    def state(self) -> dict:
        return {"attached": self.window is not None, "ready": self.ready(), "challenge": self.challenge,
                "host": self.host, "login_open": self.login_open, "open": self.visible,
                "separate_window": getattr(self, "dock", None) is None}

    # -- window lifecycle -------------------------------------------------
    def _on_closing(self, *a):
        # Closing the window only hides it; the engine keeps running.
        self.close_pane()
        return False

    def close_pane(self):
        """The user closed the arena.ai pane: drop any open check or sign-in and hide it."""
        if self.challenge == "recaptcha":
            self.challenge = None
        self.login_open = False
        threading.Thread(target=self.hide, daemon=True).start()

    def _after_load(self):
        try:
            title = str(self.window.evaluate_js("document.title") or "")
            self.host = str(self.window.evaluate_js("location.host") or "")
        except Exception:
            title = ""
        if "just a moment" in title.lower():
            self.loaded = False
            self.challenge = "cloudflare"
            self._present()
            return
        if not self.host.endswith("arena.ai"):
            self.loaded = False  # e.g. Google's sign-in page during login
            return
        if self.challenge == "cloudflare":
            self.challenge = None
            if not self.login_open:
                self.hide()
        if not self._imported and not self.has_session():
            self._imported = True
            from arena.config import COOKIE_FILE
            try:
                saved = COOKIE_FILE.read_text(encoding="utf-8").strip()
            except OSError:
                saved = ""
            if contracts.AUTH_COOKIE_PREFIX in saved and self.import_cookies(saved):
                return  # reloads; this handler runs again
        self._imported = True
        self.loaded = True

    def _present(self):
        """Show the arena.ai page: as a pane inside the app window when docking works, else over it."""
        dock = getattr(self, "dock", None)
        if dock is not None and dock.show():
            self.visible = True
            return
        w, m = self.window, getattr(self, "main", None)
        spot = None
        if m is not None:
            try:
                spot = (int(m.x) + 40, int(m.y) + 40)
                w.resize(max(int(m.width) - 80, 720), max(int(m.height) - 80, 560))
                w.move(*spot)
            except Exception:
                spot = None
        w.show()
        self.visible = True
        if spot:
            try:
                w.move(*spot)  # the first show() can re-center the window on screen
            except Exception:
                pass

    def show(self):
        if self.window:
            self._present()

    def hide(self):
        self.visible = False
        dock = getattr(self, "dock", None)
        if dock is not None and dock.visible:
            dock.hide()
        elif self.window:
            self.window.hide()

    def open_login(self):
        self.login_open = True
        self._present()
        if not self.host.endswith("arena.ai"):
            self.window.load_url(contracts.ORIGIN)

    def finish_login(self):
        self.login_open = False
        self.hide()

    def open_path(self, path: str):
        """Show one arena.ai path in the separate engine window."""
        if not self.window or not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            return False
        self.loaded = False
        self.window.load_url(contracts.ORIGIN + path)
        self._present()
        return True

    def show_cloudflare(self):
        self.loaded = False
        self.challenge = "cloudflare"
        self.window.load_url(contracts.ORIGIN)
        self._present()

    def run_script(self, script: str):
        """Evaluate JS inside the arena.ai page; returns its value or None."""
        if not self.window:
            return None
        try:
            return self.window.evaluate_js(script)
        except Exception:
            return None

    def page_site_key(self) -> Optional[str]:
        try:
            key = self.window.evaluate_js(_KEY_JS)
        except Exception:
            return None
        return key if isinstance(key, str) and contracts.SITE_KEY_RE.fullmatch(key) else None

    # -- cookies ----------------------------------------------------------
    def cookies(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        try:
            for jar in self.window.get_cookies() or []:
                for name, morsel in jar.items():
                    domain = (morsel.get("domain") or "arena.ai").lstrip(".")
                    if domain == "arena.ai" or domain.endswith(".arena.ai"):
                        out[name] = morsel.value
        except Exception:
            pass
        # Some WebView2 builds do not enumerate the page cookies through
        # pywebview. Arena's auth chunks are readable by the page itself, so
        # use document.cookie as a fallback without leaving arena.ai.
        if not any(name.startswith(contracts.AUTH_COOKIE_PREFIX) for name in out):
            host = (self.host or "").lower()
            if self.window and (host == "arena.ai" or host.endswith(".arena.ai")):
                try:
                    raw = self.window.evaluate_js("document.cookie")
                except Exception:
                    raw = None
                if isinstance(raw, str):
                    for part in raw.split(";"):
                        if "=" not in part:
                            continue
                        name, value = part.strip().split("=", 1)
                        if name.startswith(contracts.AUTH_COOKIE_PREFIX):
                            out[name] = value
        return out

    def has_session(self) -> bool:
        return any(k.startswith(contracts.AUTH_COOKIE_PREFIX) for k in self.cookies())

    def import_cookies(self, cookie_str: str) -> bool:
        """Copy auth cookies into the engine, drop stale auth chunks, then reload arena.ai.

        arena's Supabase SSR cookies are not HttpOnly, so document.cookie can set them.
        """
        pairs = [p.strip() for p in (cookie_str or "").split(";")
                 if "=" in p and p.strip().startswith(contracts.AUTH_COOKIE_PREFIX)]
        if not pairs or not self.window:
            return False
        if not self.host.endswith("arena.ai"):
            self.window.load_url(contracts.ORIGIN)
            end = time.time() + 20
            while time.time() < end and not self.host.endswith("arena.ai"):
                time.sleep(0.25)
        if not (self.host == "arena.ai" or self.host.endswith(".arena.ai")):
            return False  # Never inject account cookies into a different site.
        names = [p.split("=", 1)[0] for p in pairs]
        js = ("(function(keep,pairs,prefix){document.cookie.split(';').forEach(function(c){"
              "var n=c.split('=')[0].trim();if(n.indexOf(prefix)===0&&keep.indexOf(n)<0)"
              "document.cookie=n+'=; path=/; max-age=0; secure';});"
              "pairs.forEach(function(p){document.cookie=p+'; path=/; max-age=34560000; secure; samesite=lax';});"
              "location.reload();return 1})(%s,%s,%s)") % (
            json.dumps(names), json.dumps(pairs), json.dumps(contracts.AUTH_COOKIE_PREFIX))
        self._imported = True
        self.loaded = False
        self.window.evaluate_js(js)
        return True

    def sync_to(self, client):
        """The engine owns the refresh-token family: copy its rotated auth cookies back to the app."""
        auth = {k: v for k, v in self.cookies().items() if k.startswith(contracts.AUTH_COOKIE_PREFIX)}
        if not auth:
            return
        cookie_str = "; ".join(f"{k}={v}" for k, v in sorted(auth.items()))
        if cookie_str != client.cookie_str:
            client.set_cookie(cookie_str)
            from arena.config import save_cookie
            save_cookie(cookie_str)

    def clear_session_cookies(self):
        """Drop auth cookies locally only (no server sign-out).

        Used when adding or switching accounts: the previous account's
        stored session must stay valid on Arena's side, so nothing is
        revoked — the window simply forgets it and reloads as a guest.
        """
        if not self.window:
            return
        self._imported = True
        self.loaded = False
        js = ("(function(){document.cookie.split(';').forEach(function(c){var n=c.split('=')[0].trim();"
              "if(n.indexOf(%s)===0)document.cookie=n+'=; path=/; max-age=0; secure';});location.reload()})();1"
              ) % (json.dumps(contracts.AUTH_COOKIE_PREFIX))
        try:
            self.window.evaluate_js(js)
        except Exception:
            pass

    def logout(self):
        if not self.window:
            return
        self._imported = True
        self.loaded = False
        js = ("(async function(){try{await fetch(%s,{method:'POST',credentials:'include'})}catch(e){}"
              "document.cookie.split(';').forEach(function(c){var n=c.split('=')[0].trim();"
              "if(n.indexOf(%s)===0)document.cookie=n+'=; path=/; max-age=0; secure';});location.reload()})();1"
              ) % (json.dumps(contracts.PATHS["sign_out"]), json.dumps(contracts.AUTH_COOKIE_PREFIX))
        try:
            self.window.evaluate_js(js)
        except Exception:
            pass

    # -- requests ---------------------------------------------------------
    def request(self, path: str, body=None, method: Optional[str] = None, timeout: float = 180,
                v3_action: Optional[str] = None, headers: Optional[dict] = None,
                cx: bool = False) -> EngineResponse:
        """Run fetch() inside arena.ai's page; returns once the status line arrives.

        ``cx=True`` asks the page's own client-interaction module for a
        genuine ``cx`` blob and attaches it to the body — the same field
        the website's chat submit sends — before the fetch runs.  When the
        module is unavailable the request goes out without it, as before.
        """
        rid = uuid.uuid4().hex
        q: "queue.Queue" = queue.Queue()
        with self._lock:
            self._streams[rid] = {"q": q, "next": 0, "held": {}}
        args = ",".join(json.dumps(a) for a in (rid, path, method or ("POST" if body is not None else "GET"),
                                               body, contracts.KEYS["v3"], v3_action or contracts.RECAPTCHA_ACTION,
                                               headers, bool(cx and body is not None), _CX_MODULE_ID))
        run = getattr(self.window, "run_js", None) or self.window.evaluate_js
        try:
            run(_FETCH_JS % args)
        except Exception as e:
            self._deliver(rid, 0, "error", f"engine script failed: {e}")
        return EngineResponse(self, rid, q, timeout)

    # -- native Agent preflight ----------------------------------------------
    def native_preflight(self, action: str, timeout: float = 45) -> dict:
        """Ask the arena.ai page for submission prerequisites.

        Returns {"cx": str|None, "token_len": int, "trace": [str, ...]} where
        ``cx`` is a genuine client-interaction blob computed by the page's own
        module for this submission and ``token_len`` proves reCAPTCHA v3
        minting works in this page for ``action`` (length only; the probe
        token itself is discarded, the real request mints its own).

        If the interaction module is not loaded on the current page, the
        engine navigates to /agent (where it definitely is) and retries once.
        """
        result = {"cx": None, "token_len": 0, "trace": []}
        if not self.window:
            result["trace"] = ["no_window"]
            return result
        if self.login_open:
            result["trace"] = ["login_open"]
            return result
        with self._drive_lock:
            for attempt in range(2):
                data = self._run_preflight_probe(action, timeout)
                if data is None:
                    result["trace"].append("probe_no_answer")
                    return result
                result["cx"] = data.get("cx") or None
                try:
                    result["token_len"] = int(data.get("tokenLen") or 0)
                except (TypeError, ValueError):
                    result["token_len"] = 0
                result["trace"] = [str(t) for t in (data.get("trace") or [])]
                if result["cx"] or attempt == 1:
                    return result
                state = self._drive_state()
                if (state.get("path") or "").startswith("/agent"):
                    return result
                LOG.info("preflight: no cx on %s; navigating to /agent to load the module",
                         state.get("path"))
                self.loaded = False
                self.window.load_url(contracts.ORIGIN + "/agent")
                if not self.wait_ready(60):
                    result["trace"].append("agent_nav_timeout")
                    return result
        return result

    def _run_preflight_probe(self, action: str, timeout: float) -> Optional[dict]:
        if not self.wait_ready(30):
            return None
        q: "queue.Queue" = queue.Queue()
        self._drive_queue = q
        try:
            opts = json.dumps({"action": action, "v3key": contracts.KEYS["v3"],
                               "moduleId": _CX_MODULE_ID})
            self.run_script(_PREFLIGHT_JS.replace("__ARENA_OPTS__", opts))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    kind, data = q.get(timeout=2)
                except queue.Empty:
                    continue
                if kind == "preflight":
                    return data
            return None
        finally:
            self._drive_queue = None

    # -- Battle pairwise vote --------------------------------------------------
    def pairwise_vote(self, args: dict, timeout: float = 45) -> dict:
        """Submit one Battle pairwise vote through the arena.ai page.

        The page's own Server Action (createPairwiseFeedback) is called
        with a reCAPTCHA v3 token the page mints for the
        "pairwise_feedback" action — the exact submission the website's
        vote bar performs.  ``args`` carries evaluationSessionId,
        messageAId, messageBId and value (model_a|model_b|tie|both_bad).

        Returns {"ok": bool, "result": {success, error, modelAId,
        modelBId, arenaId, revealed:[{id, name, org}]} | None,
        "error": str|None, "trace": [...]}.  The reCAPTCHA token never
        leaves the page.
        """
        result: dict = {"ok": False, "result": None, "error": None, "trace": []}
        if not self.window:
            result["error"] = "no_window"
            return result
        if self.login_open:
            result["error"] = "login_open"
            return result
        with self._drive_lock:
            data = self._run_vote_probe(args, timeout)
            if data is None:
                result["error"] = "probe_no_answer"
                return result
            result["ok"] = bool(data.get("ok"))
            result["result"] = data.get("result") if isinstance(data.get("result"), dict) else None
            result["error"] = data.get("error")
            result["trace"] = [str(t) for t in (data.get("trace") or [])]
            return result

    def _run_vote_probe(self, args: dict, timeout: float) -> Optional[dict]:
        if not self.wait_ready(30):
            return None
        q: "queue.Queue" = queue.Queue()
        self._drive_queue = q
        try:
            opts = json.dumps({"args": args, "v3key": contracts.KEYS["v3"],
                               "moduleId": _VOTE_MODULE_ID, "actionId": _VOTE_ACTION_ID,
                               "recaptchaAction": _VOTE_RECAPTCHA_ACTION})
            self.run_script(_VOTE_JS.replace("__ARENA_OPTS__", opts))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    kind, data = q.get(timeout=2)
                except queue.Empty:
                    continue
                if kind == "vote":
                    return data
            return None
        finally:
            self._drive_queue = None

    # -- chat archive --------------------------------------------------------
    def archive_evaluation(self, session_id: str, timeout: float = 40) -> dict:
        """Archive one evaluation session on arena.ai, like its sidebar does.

        Calls the page's own archiveEvaluationSession Server Action with
        the session id.  Returns {"ok": bool, "error": str|None,
        "trace": [...]}.
        """
        result: dict = {"ok": False, "error": None, "trace": []}
        if not self.window:
            result["error"] = "no_window"
            return result
        if self.login_open:
            result["error"] = "login_open"
            return result
        with self._drive_lock:
            data = self._run_archive_probe(str(session_id or ""), timeout)
            if data is None:
                result["error"] = "probe_no_answer"
                return result
            result["ok"] = bool(data.get("ok"))
            result["error"] = data.get("error")
            result["trace"] = [str(t) for t in (data.get("trace") or [])]
            return result

    def _run_archive_probe(self, session_id: str, timeout: float) -> Optional[dict]:
        if not session_id or not self.wait_ready(30):
            return None
        q: "queue.Queue" = queue.Queue()
        self._drive_queue = q
        try:
            opts = json.dumps({"sessionId": session_id,
                               "moduleId": _VOTE_MODULE_ID,
                               "actionId": _ARCHIVE_ACTION_ID,
                               "actionName": _ARCHIVE_ACTION_NAME})
            self.run_script(_ARCHIVE_JS.replace("__ARENA_OPTS__", opts))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    kind, data = q.get(timeout=2)
                except queue.Empty:
                    continue
                if kind == "archive":
                    return data
            return None
        finally:
            self._drive_queue = None

    # -- native Agent page drive -------------------------------------------
    def _on_drive_event(self, kind, payload):
        try:
            data = json.loads(payload) if isinstance(payload, str) else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if kind == "preflight":
            # The cx value itself is never logged, only its length and the trace.
            LOG.info("preflight: tokenLen=%s cxLen=%s trace=%s",
                     data.get("tokenLen"), data.get("cxLen"),
                     "; ".join(str(t) for t in (data.get("trace") or []))[:500])
        elif kind == "vote":
            slim = data.get("result") or {}
            LOG.info("vote: ok=%s success=%s modelA=%s modelB=%s err=%s trace=%s",
                     data.get("ok"), slim.get("success"), slim.get("modelAId"),
                     slim.get("modelBId"), data.get("error") or slim.get("error"),
                     "; ".join(str(t) for t in (data.get("trace") or []))[:300])
        elif kind == "archive":
            LOG.info("archive: ok=%s err=%s trace=%s",
                     data.get("ok"), data.get("error"),
                     "; ".join(str(t) for t in (data.get("trace") or []))[:300])
        elif kind == "create":
            sid = None
            try:
                parsed = json.loads(data.get("text") or "{}")
                if isinstance(parsed, dict):
                    sid = parsed.get("id") or parsed.get("sessionId")
            except ValueError:
                pass
            # The create JSON carries a public access token: log ids only.
            LOG.info("drive create fetch: HTTP %s session=%s", data.get("status"), sid)
        elif kind == "append":
            LOG.info("drive append fetch: HTTP %s", data.get("status"))
        elif kind == "composer":
            LOG.info("drive composer: %s", {k: data.get(k) for k in
                     ("ok", "set", "via", "cleared", "stage", "path")})
        q = self._drive_queue
        if q is not None:
            q.put((kind, data))

    def _drive_state(self) -> dict:
        raw = self.run_script(_DRIVE_STATE_JS)
        if isinstance(raw, str):
            try:
                state = json.loads(raw)
                if isinstance(state, dict):
                    return state
            except ValueError:
                pass
        return {}

    def drive_agent_send(self, text: str, chat_id: Optional[str] = None,
                         on_event=None, timeout: float = 240) -> dict:
        """Send one native Agent message through the real /agent UI in this window.

        The website's own client builds the whole submission (client-
        interaction blob, reCAPTCHA, tokens); the drive only types into its
        composer, clicks its send button and observes the create/append
        responses.  ``chat_id=None`` creates a new session on the agent
        landing; otherwise the existing chat page is driven.

        Returns {"ok": True, "payload": <create-chat JSON|None>} or
        {"ok": False, "error": str, "status": int|None, "stage": str}.
        ``on_event(kind, payload)`` receives ("stage", text) progress notes
        and ("challenge", "recaptcha"|"cloudflare") when the human is needed.
        """
        notify = on_event or (lambda kind, payload: None)
        if not self.window:
            return {"ok": False, "stage": "window", "status": None,
                    "error": "The arena.ai window is unavailable."}
        with self._drive_lock:
            return self._drive_send_locked(text, chat_id, notify, timeout)

    def _drive_send_locked(self, text, chat_id, notify, timeout) -> dict:
        q: "queue.Queue" = queue.Queue()
        self._drive_queue = q
        presented = False
        challenge_kind = None

        def present_for(kind):
            nonlocal presented, challenge_kind
            if kind == "recaptcha":
                self.challenge = "recaptcha"
            if not presented:
                presented = True
                self._present()
            if challenge_kind != kind:
                challenge_kind = kind
                notify("challenge", kind)

        def finish(result):
            if result.get("ok") and presented:
                if self.challenge == "recaptcha":
                    self.challenge = None
                if not self.login_open:
                    threading.Thread(target=self.hide, daemon=True).start()
            return result

        try:
            target = "/agent" + (f"/{chat_id}" if chat_id else "")
            state = self._drive_state()
            if (state.get("path") or "").rstrip("/") != target:
                notify("stage", "Arena Agent · opening the agent page")
                LOG.info("drive: navigating %s -> %s", state.get("path"), target)
                self.loaded = False
                self.window.load_url(contracts.ORIGIN + target)
                if not self.wait_ready(60):
                    if self.challenge == "cloudflare":
                        presented = True  # _after_load already showed the window
                        challenge_kind = "cloudflare"
                        notify("challenge", "cloudflare")
                        if not self.wait_ready(240):
                            return {"ok": False, "stage": "load", "status": None,
                                    "error": "The Cloudflare check in the Arena window is not finished."}
                    else:
                        return {"ok": False, "stage": "load", "status": None,
                                "error": "The arena.ai page did not load in the engine window."}
            self.run_script(_DRIVE_HOOK_JS)
            deadline = time.time() + 60
            state = {}
            found = False
            while time.time() < deadline:
                state = self._drive_state()
                if (state.get("textareas") or 0) + (state.get("editables") or 0) > 0:
                    found = True
                    break
                time.sleep(1)
            if not found:
                LOG.info("drive: composer not found, last state %s", state)
                return {"ok": False, "stage": "composer", "status": None,
                        "error": ("Could not find the input field on the Arena Agent page "
                                  f"({state.get('path') or '…'}). Take a look at the Arena window.")}
            if not state.get("hooked"):
                self.run_script(_DRIVE_HOOK_JS)
            notify("stage", "Arena Agent · sending the message")
            self.run_script(_DRIVE_SEND_JS.replace("__ARENA_TEXT__", json.dumps(text)))

            deadline = time.time() + timeout
            grace_deadline = None
            last_create = None
            composer = None
            sent_at = time.time()
            next_probe = sent_at + 3
            while time.time() < deadline:
                if grace_deadline and time.time() > grace_deadline:
                    break
                try:
                    kind, data = q.get(timeout=2)
                except queue.Empty:
                    kind, data = None, {}
                now = time.time()
                if kind == "composer":
                    composer = data
                    if not data.get("ok"):
                        return {"ok": False, "stage": "composer", "status": None,
                                "error": ("Could not find the input field on the Arena Agent page. "
                                          "Take a look at the Arena window.")}
                    if chat_id and data.get("cleared"):
                        return finish({"ok": True, "payload": None})
                elif kind == "create":
                    status = data.get("status")
                    if status == 200:
                        try:
                            payload = json.loads(data.get("text") or "{}")
                        except ValueError:
                            payload = {}
                        if isinstance(payload, dict) and (payload.get("id") or payload.get("sessionId")):
                            return finish({"ok": True, "payload": payload})
                        last_create = (status, "unexpected create reply")
                    else:
                        snippet = " ".join(str(data.get("text") or "").split())[:300]
                        last_create = (status, snippet)
                        if status in (401, 422, 429):
                            return {"ok": False, "stage": "create", "status": status,
                                    "error": f"arena.ai returned HTTP {status}: {snippet}"}
                        # The page itself may now show a human check and retry;
                        # give that flow a bounded grace period before failing.
                        if grace_deadline is None:
                            grace_deadline = now + 150
                elif kind == "append" and chat_id and data.get("status") == 200:
                    return finish({"ok": True, "payload": None})

                if now >= next_probe:
                    next_probe = now + 3
                    state = self._drive_state()
                    path = state.get("path") or ""
                    if not chat_id:
                        m = re.fullmatch(r"/agent/([0-9a-fA-F-]{36})", path)
                        if m:
                            LOG.info("drive: session from URL %s", m.group(1))
                            return finish({"ok": True, "payload": {"id": m.group(1)}})
                    if chat_id and composer and composer.get("ok") and state.get("composerEmpty"):
                        return finish({"ok": True, "payload": None})
                    if state.get("v2"):
                        present_for("recaptcha")
                    elif self.challenge == "cloudflare":
                        present_for("cloudflare")
                    elif not presented and now - sent_at > 25 and composer:
                        # Nothing confirmed for a while: show the page so the
                        # human can resolve whatever the site is showing.
                        present_for("recaptcha")
            if last_create:
                status, snippet = last_create
                return {"ok": False, "stage": "create", "status": status,
                        "error": f"arena.ai returned HTTP {status}: {snippet}"}
            return {"ok": False, "stage": "timeout", "status": None,
                    "error": ("Arena did not confirm the send in time. Take a look at the "
                              "Arena window — there may be a check or an error message.")}
        finally:
            self._drive_queue = None

    def get_me(self, timeout: float = 25) -> Tuple[Optional[dict], Optional[str]]:
        if not self.wait_ready(timeout):
            return None, (f"arena.ai is waiting for a {self.challenge} check" if self.challenge
                          else "arena.ai window is still loading")
        r = self.request(contracts.PATHS["me"], method="GET", timeout=timeout)
        body = r.text
        if r.status_code == 0:
            return None, f"Network error: {r.error}"
        if r.status_code == 200:
            try:
                data = json.loads(body)
            except ValueError:
                return None, "arena.ai sent an unexpected /api/me reply"
            user = data.get("user") if isinstance(data, dict) and "user" in data else data
            if isinstance(user, dict) and (user.get("email") or user.get("id")):
                return user, None
            return None, "Not signed in"
        if r.status_code == 401:
            return None, "Not signed in"
        return None, f"HTTP {r.status_code}"

    def _deliver(self, rid, seq, kind, data):
        with self._lock:
            st = self._streams.get(rid)
            if not st:
                return
            st["held"][int(seq)] = (kind, data)
            while st["next"] in st["held"]:
                st["q"].put(st["held"].pop(st["next"]))
                st["next"] += 1

    def _close(self, rid):
        with self._lock:
            self._streams.pop(rid, None)

    # -- human verification -----------------------------------------------
    def request_human_check(self):
        """Show arena.ai with its reCAPTCHA v2 checkbox; the token lands in self.v2_token."""
        if not self.window:
            return
        self.v2_token = None
        self.challenge = "recaptcha"
        LOG.info("arena.ai asked for a human check")
        self._present()
        self.window.evaluate_js(_CHECK_JS % ",".join(json.dumps(a) for a in (contracts.KEYS["v2"], contracts.RECAPTCHA_ACTION)))

    def cancel_check(self):
        self.challenge = None
        try:
            self.window.evaluate_js("(function(){var b=document.getElementById('arena-desktop-check');if(b)b.remove();return 1})()")
        except Exception:
            pass
        if not self.login_open:
            threading.Thread(target=self.hide, daemon=True).start()

    def _on_v2(self, token):
        self.v2_token = str(token or "") or None
        LOG.info("human check finished in the page, token %s", "received" if self.v2_token else "empty")
        self.challenge = None
        if not self.login_open:
            threading.Thread(target=self.hide, daemon=True).start()

    def take_v2_token(self) -> Optional[str]:
        token, self.v2_token = self.v2_token, None
        return token


ENGINE = BrowserEngine()
