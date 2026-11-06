  const DEMO = new URLSearchParams(window.location.search).get('demo') === '1';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  /* ---------------------------------------------------------------------- */
  /* Icons: static, local SVG only. Never derived from server/model text.   */
  /* ---------------------------------------------------------------------- */
  const ICONS = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    'panel-left': '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/>',
    'panel-right': '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M15 4v16"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
    search: '<circle cx="11" cy="11" r="6.8"/><path d="m16 16 4.2 4.2"/>',
    clock: '<circle cx="12" cy="12" r="8.7"/><path d="M12 7.6v4.9l3.2 1.8"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.1 6.5"/><path d="M20 4v7h-7"/>',
    folder: '<path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-10Z"/>',
    settings: '<circle cx="12" cy="12" r="3.2"/><path d="M19.2 13.5a7.7 7.7 0 0 0 0-3l2-1.6-2-3.4-2.4 1a8.4 8.4 0 0 0-2.6-1.5L14 2.5h-4L9.8 5a8.4 8.4 0 0 0-2.6 1.5l-2.4-1-2 3.4 2 1.6a7.7 7.7 0 0 0 0 3l-2 1.6 2 3.4 2.4-1A8.4 8.4 0 0 0 9.8 19l.2 2.5h4l.2-2.5a8.4 8.4 0 0 0 2.6-1.5l2.4 1 2-3.4-2-1.6Z"/>',
    help: '<circle cx="12" cy="12" r="8.7"/><path d="M9.6 9.2a2.5 2.5 0 1 1 3.5 2.3c-.7.35-1.1.9-1.1 1.7v.4"/><path d="M12 17.2h.01"/>',
    globe: '<circle cx="12" cy="12" r="8.7"/><path d="M3.6 12h16.8M12 3.3c2.2 2.3 3.4 5.3 3.4 8.7s-1.2 6.4-3.4 8.7c-2.2-2.3-3.4-5.3-3.4-8.7S9.8 5.6 12 3.3Z"/>',
    x: '<path d="m6 6 12 12M18 6 6 18"/>',
    'chevron-down': '<path d="m6.5 9 5.5 5.5L17.5 9"/>',
    'chevron-right': '<path d="m9 6.5 5.5 5.5L9 17.5"/>',
    'arrow-up': '<path d="M12 19V5M6.5 10.5 12 5l5.5 5.5"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.6" fill="currentColor" stroke="none"/>',
    shield: '<path d="M12 3.2 19 6v5.1c0 4.4-2.9 7.8-7 9.6-4.1-1.8-7-5.2-7-9.6V6l7-2.8Z"/><path d="m9.2 12 1.9 1.9 3.8-4"/>',
    'shield-alert': '<path d="M12 3.2 19 6v5.1c0 4.4-2.9 7.8-7 9.6-4.1-1.8-7-5.2-7-9.6V6l7-2.8Z"/><path d="M12 8v4.5"/><path d="M12 16h.01"/>',
    'wifi-off': '<path d="m3 3 18 18M8.6 15.3a5.2 5.2 0 0 1 6.8 0M5.5 12.2a10.2 10.2 0 0 1 4-2M14.2 9.9a10.2 10.2 0 0 1 4.3 2.3M2.5 8.8A15.8 15.8 0 0 1 7 6.4M12 5c2.1 0 4.1.5 6 1.4M12 20h.01"/>',
    flask: '<path d="M9 3h6M10 3v6.1l-5 8.2A2 2 0 0 0 6.7 20h10.6a2 2 0 0 0 1.7-2.7l-5-8.2V3"/><path d="M7.3 14h9.4"/>',
    trophy: '<path d="M7.5 4h9v5a4.5 4.5 0 0 1-9 0V4Z"/><path d="M7.5 5.5H4.7a2.8 2.8 0 0 0 2.9 4M16.5 5.5h2.8a2.8 2.8 0 0 1-2.9 4M12 13.6v3M8.8 20h6.4M10 20l.6-3.4M14 20l-.6-3.4"/>',
    'thumbs-up': '<path d="M6.8 10.2v9.3H4.3a.8.8 0 0 1-.8-.8v-7.7a.8.8 0 0 1 .8-.8h2.5ZM6.8 12.4l3.9-7.3c1.3-.2 2.4.8 2.3 2l-.6 3.1h5.9c1 0 1.7 1 1.4 1.9l-1.4 5.4a2 2 0 0 1-2 1.5H8.5c-.9 0-1.7-.7-1.7-1.7v-4.9Z"/>',
    'thumbs-down': '<path d="M17.2 13.8V4.5h2.5a.8.8 0 0 1 .8.8v7.7a.8.8 0 0 1-.8.8h-2.5ZM17.2 11.6l-3.9 7.3c-1.3.2-2.4-.8-2.3-2l.6-3.1H5.7c-1 0-1.7-1-1.4-1.9l1.4-5.4a2 2 0 0 1 2-1.5h7.8c.9 0 1.7.7 1.7 1.7v4.9Z"/>',
    scale: '<path d="M12 4v16M7.5 20h9M12 4 6 6.4M12 4l6 2.4M6 6.4 3.4 12a3.4 3.4 0 0 0 5.2 0L6 6.4ZM18 6.4 15.4 12a3.4 3.4 0 0 0 5.2 0L18 6.4Z"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    trash: '<path d="M4.8 7h14.4M9.2 7V4.6h5.6V7M7.2 7l.9 13h7.8l.9-13M10 10.5v6M14 10.5v6"/>',
    layout: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9h17M9 9v10.5"/>',
    chart: '<path d="M4 20V4M4 20h16"/><path d="M8 16v-4M12.5 16V7.5M17 16v-6"/>',
    gamepad: '<path d="M8.5 8.2h7a4.5 4.5 0 0 1 4.3 3.2l1 3.4a2.4 2.4 0 0 1-4.2 2.1l-1.2-1.5H8.6l-1.2 1.5a2.4 2.4 0 0 1-4.2-2.1l1-3.4a4.5 4.5 0 0 1 4.3-3.2Z"/><path d="M8 11v3M6.5 12.5h3M16 11.5h.01M18 13.5h.01"/>',
    bug: '<rect x="7" y="7.5" width="10" height="12" rx="5"/><path d="M9.5 7.5a2.5 2.5 0 0 1 5 0M4 11h3M17 11h3M4 16h3M17 16h3M8.5 19l-2 2M15.5 19l2 2"/>',
    'git-branch': '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10M8 7c4 0 4.5-1 8-1"/>',
    'file-text': '<path d="M7 3.5h7l4 4V20H7z"/><path d="M14 3.5V8h4M9.5 12h5M9.5 15.5h5"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="9.5" r="1.5"/><path d="m4 17 4.6-4.5 3.3 3 2.4-2.1 5.7 5"/>',
    video: '<rect x="3.5" y="5.5" width="12.5" height="13" rx="2"/><path d="m16 10 4.5-2.5v9L16 14"/>',
    code: '<path d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4M13.5 5l-3 14"/>',
    'search-plus': '<circle cx="10.5" cy="10.5" r="6.4"/><path d="m15.3 15.3 4.2 4.2M10.5 7.8v5.4M7.8 10.5h5.4"/>',
    copy: '<rect x="8.2" y="8.2" width="11.3" height="11.3" rx="2"/><path d="M15.8 8.2V6a1.8 1.8 0 0 0-1.8-1.8H6A1.8 1.8 0 0 0 4.2 6v8a1.8 1.8 0 0 0 1.8 1.8h2.2"/>',
    check: '<path d="m5 12.4 4.4 4.4L19 7"/>',
    package: '<path d="m12 3 8 4.3v9.4L12 21l-8-4.3V7.3L12 3Z"/><path d="m4.3 7.5 7.7 4 7.7-4M12 11.5V21"/>',
    sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    download: '<path d="M12 3.5v11M7.5 10.5 12 15l4.5-4.5M4 19.5h16"/>',
    external: '<path d="M13 4h7v7M20 4l-8 8"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
    terminal: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="m7.2 9 2.8 2.7L7.2 14.5M12.4 15h4.2"/>',
    plug: '<path d="M9 3v5M15 3v5M6.8 8h10.4v3.4a5.2 5.2 0 0 1-10.4 0V8Z"/><path d="M12 16.6V21"/>',
    cloud: '<path d="M17.5 18.2a4.2 4.2 0 0 0 .6-8.36A6.2 6.2 0 0 0 6.5 7.7 5.2 5.2 0 0 0 6.8 18z"/>',
    braces: '<path d="M8 4H7a3 3 0 0 0-3 3v2.5A2.5 2.5 0 0 1 1.8 12 2.5 2.5 0 0 1 4 14.5V17a3 3 0 0 0 3 3h1M16 4h1a3 3 0 0 1 3 3v2.5a2.5 2.5 0 0 0 2.2 2.5A2.5 2.5 0 0 0 20 14.5V17a3 3 0 0 1-3 3h-1"/>',
    info: '<circle cx="12" cy="12" r="8.7"/><path d="M12 10.5v5.2M12 7.8h.01"/>',
    alert: '<path d="M10.3 4.6a2 2 0 0 1 3.4 0l7.4 12.7a2 2 0 0 1-1.7 3H4.6a2 2 0 0 1-1.7-3L10.3 4.6Z"/><path d="M12 9v4M12 16.4h.01"/>',
    lock: '<rect x="4.8" y="10" width="14.4" height="10.5" rx="2"/><path d="M8.2 10V7.5a3.8 3.8 0 0 1 7.6 0V10M12 14v2.4"/>',
    user: '<circle cx="12" cy="8.5" r="3.7"/><path d="M4.8 20a7.2 7.2 0 0 1 14.4 0"/>',
    login: '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M9 8l4 4-4 4M13 12H3.5"/>',
    logout: '<path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M15 8l4 4-4 4M19 12H9"/>',
    mail: '<rect x="3.5" y="5.2" width="17" height="14" rx="2"/><path d="m4.5 7 7.5 6 7.5-6"/>',
    file: '<path d="M7 3.5h7l4 4V20H7z"/><path d="M14 3.5V8h4"/>',
    archive: '<rect x="3.5" y="4.5" width="17" height="5" rx="1.2"/><path d="M5 9.5V20h14V9.5M9.5 13h5"/>',
    spark: '<path d="M12 2.8c.55 5.35 3.85 8.65 9.2 9.2-5.35.55-8.65 3.85-9.2 9.2-.55-5.35-3.85-8.65-9.2-9.2 5.35-.55 8.65-3.85 9.2-9.2Z"/>',
    swords: '<path d="M4.5 4.5 15 15M19.5 4.5 9 15M4.5 4.5v3.2M4.5 4.5h3.2M19.5 4.5v3.2M19.5 4.5h-3.2M13.8 13.8 19 19M10.2 13.8 5 19"/>',
    columns: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M12 4.5v15"/>',
    message: '<path d="M20.5 12a8.3 8.3 0 0 1-12 7.2L4 20.5l1.4-4.2A8.3 8.3 0 1 1 20.5 12Z"/>',
    type: '<path d="M5 5h14M12 5v14M9.5 19h5"/>',
    play: '<circle cx="12" cy="12" r="8.7"/><path d="m10 8.5 5 3.5-5 3.5v-7Z"/>',
    'arrow-down': '<path d="M12 5v14M6.5 13.5 12 19l5.5-5.5"/>',
    compress: '<path d="M9.5 3.5 4 9M4 4.8V9h4.2M14.5 3.5 20 9M20 4.8V9h-4.2M9.5 20.5 4 15M4 19.2V15h4.2M14.5 20.5 20 15M20 19.2V15h-4.2"/>',
    'file-code': '<path d="M7 3.5h7l4 4V20H7z"/><path d="M14 3.5V8h4"/><path d="m10 12.5-2 1.5 2 1.5M14 12.5l2 1.5-2 1.5"/>'
  };

  function icon(name, className = '') {
    const body = ICONS[name] || ICONS.info;
    return `<svg${className ? ` class="${className}"` : ''} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
  }

  function iconEl(name, className = '') {
    const span = document.createElement('span');
    span.className = `icon${className ? ` ${className}` : ''}`;
    span.setAttribute('aria-hidden', 'true');
    span.innerHTML = icon(name);
    return span;
  }

  function hydrateIcons(root = document) {
    $$('[data-icon]', root).forEach((node) => {
      if (node.dataset.iconReady === '1' || node.querySelector('svg')) return;
      node.innerHTML = icon(node.dataset.icon);
      node.dataset.iconReady = '1';
      node.setAttribute('aria-hidden', 'true');
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Typewriter loading phrases                                            */
  /* ---------------------------------------------------------------------- */
  const _loadingPhrases = [
    "asking Terry", "argmaxxing...", "thinking deeply", "sampling candidates",
    "routing tokens", "consulting the oracle", "parsing intent",
    "reasoning through it", "exploring solutions", "warming up neurons",
    "cross-referencing", "beam searching", "calibrating confidence",
    "attention is all you need", "gradient descending", "softmax engaged",
    "embedding your query", "considering alternatives", "drafting a response",
    "deliberating", "asking the council", "crunching logits",
    "traversing the latent space", "pondering deeply", "assembling thoughts",
    "running inference", "token by token", "weighing probabilities",
    "searching the manifold", "spinning up context"
  ];

  function createThinkingTypewriter() {
    const wrap = el('span', { class: 'loading-block thinking-typewriter' });
    const dot = el('span', { class: 'status-dot is-online' });
    const text = el('span', { class: 'typewriter-text' });
    const cursor = el('span', { class: 'typewriter-cursor' });
    wrap.append(dot, text, cursor);

    let alive = true;
    let used = new Set();
    const pick = () => {
      if (used.size >= _loadingPhrases.length) used.clear();
      let phrase;
      do { phrase = _loadingPhrases[Math.floor(Math.random() * _loadingPhrases.length)]; } while (used.has(phrase));
      used.add(phrase);
      return phrase;
    };

    // The element is created before it is attached, and chat re-renders move
    // it between containers, so a short detach must not end the animation.
    // Only a long absence (the lane finished, chat switched) stops it.
    let detachedSince = 0;
    const attached = async () => {
      while (alive && !wrap.isConnected) {
        if (!detachedSince) detachedSince = Date.now();
        if (Date.now() - detachedSince > 3000) { alive = false; wrap._dead = true; return false; }
        await new Promise(r => setTimeout(r, 60));
      }
      detachedSince = 0;
      return alive;
    };
    const run = async () => {
      while (alive && await attached()) {
        const phrase = pick();
        for (let i = 0; i <= phrase.length && alive; i++) {
          text.textContent = phrase.slice(0, i);
          await new Promise(r => setTimeout(r, 30 + Math.random() * 25));
        }
        await new Promise(r => setTimeout(r, 1200 + Math.random() * 800));
        for (let i = phrase.length; i >= 0 && alive; i--) {
          text.textContent = phrase.slice(0, i);
          await new Promise(r => setTimeout(r, 15));
        }
        await new Promise(r => setTimeout(r, 200));
      }
    };

    run();
    wrap._destroy = () => { alive = false; wrap._dead = true; };
    return wrap;
  }

  /* ---------------------------------------------------------------------- */
  /* Small DOM and formatting helpers                                      */
  /* ---------------------------------------------------------------------- */
  function append(parent, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) {
      child.forEach((item) => append(parent, item));
      return;
    }
    if (child instanceof Node) {
      parent.appendChild(child);
      return;
    }
    parent.appendChild(document.createTextNode(String(child)));
  }

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class' || key === 'className') node.className = String(value);
      else if (key === 'text') node.textContent = String(value);
      else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
      else if (key === 'dataset' && typeof value === 'object') Object.assign(node.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (value === true) node.setAttribute(key, '');
      else node.setAttribute(key, String(value));
    }
    children.forEach((child) => append(node, child));
    return node;
  }

  function clear(node) {
    if (node) node.replaceChildren();
    return node;
  }

  function announce(text) {
    const region = $('#liveRegion');
    if (!region) return;
    region.textContent = '';
    window.setTimeout(() => { region.textContent = text; }, 20);
  }

  function textValue(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) return value.map((item) => textValue(typeof item === 'object' ? (item?.text ?? item?.content ?? '') : item)).filter(Boolean).join('\n');
    if (typeof value === 'object') return textValue(value.text ?? value.content ?? value.message ?? '');
    return '';
  }

  function safeUrl(raw, { image = false } = {}) {
    if (typeof raw !== 'string') return null;
    const value = raw.trim();
    if (!value) return null;
    if (image && value.startsWith('data:image/')) {
      if (/^data:image\/(png|jpe?g|webp|gif);/i.test(value)) return value;
      return null;
    }
    if (image && /^blob:/i.test(value)) return value;
    if (value.startsWith('/') && !value.startsWith('//')) return value;
    try {
      const parsed = new URL(value, window.location.href);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href;
    } catch { /* invalid URL */ }
    return null;
  }

  function hostOf(raw) {
    const safe = safeUrl(raw);
    if (!safe) return '';
    try { return new URL(safe, window.location.href).host.replace(/^www\./, ''); } catch { return ''; }
  }

  function safeColor(raw) {
    if (typeof raw !== 'string') return '';
    const value = raw.trim();
    if (/^#[0-9a-f]{3,8}$/i.test(value)) return value;
    if (/^rgba?\(\s*[\d.%\s,/]+\)$/i.test(value)) return value;
    if (/^hsla?\(\s*[\d.%\s,/deg]+\)$/i.test(value)) return value;
    return '';
  }

  function initialLetters(name, max = 2) {
    const clean = String(name || 'Arena Desktop').replace(/[^\p{L}\p{N}\s._-]/gu, ' ').trim();
    const words = clean.split(/[\s._-]+/).filter(Boolean);
    if (!words.length) return 'AD';
    return words.slice(0, max).map((word) => word[0]).join('').toUpperCase();
  }

  // Arena's model catalog carries no per-model icon: the website draws the
  // organization's logo. Keep the same local brand marks (static SVG only);
  // an icon_url from the catalog, when Arena provides one, wins.
  const ORG_LOGOS = {
    google: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A10.96 10.96 0 0 0 1 12c0 1.77.42 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>',
    openai: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M22.28 9.37a6.17 6.17 0 0 0-.53-5.07 6.24 6.24 0 0 0-6.73-2.98A6.2 6.2 0 0 0 10.36 0a6.24 6.24 0 0 0-5.95 4.33A6.17 6.17 0 0 0 .27 8.06a6.24 6.24 0 0 0 .77 7.32 6.17 6.17 0 0 0 .53 5.07 6.24 6.24 0 0 0 6.73 2.98A6.2 6.2 0 0 0 12.96 24a6.24 6.24 0 0 0 5.95-4.33 6.17 6.17 0 0 0 4.14-3.73 6.24 6.24 0 0 0-.77-6.57z" fill="#10B981"/></svg>',
    anthropic: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M13.83 2H16.7l6.3 20h-2.87l-1.46-4.83H12.6L14.04 14h4.5L15.27 4.37 10.15 22H7.28L13.83 2zM1 22L7.2 2h2.87l-1.85 5.97L4.69 22H1z" fill="#F97316"/></svg>',
    xai: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M3 3l7.5 9L3 21h2.2l6.3-7.56L17.8 21H21l-7.8-9.36L20.4 3h-2.2l-5.7 6.84L6.2 3H3z" fill="#D946EF"/></svg>',
    meta: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M6.91 4.24C5.21 4.24 3.76 5.6 2.73 7.74 1.46 10.38.87 13.59.87 15.46c0 2.74 1.16 4.3 3.24 4.3 1.62 0 3.15-1.34 4.88-4.28.73-1.23 1.44-2.67 2.15-4.28l.98-2.27c.89-2.04 1.92-3.87 3.12-5.14A5.87 5.87 0 0 1 19.35 2c2.39 0 3.78 1.74 3.78 4.45 0 3.09-1.4 6.63-3.52 8.92a8.97 8.97 0 0 1-2.8 2.22" fill="none" stroke="#06B6D4" stroke-width="2"/></svg>',
    mistral: '<svg viewBox="0 0 24 24" width="18" height="18"><rect x="2" y="2" width="5" height="5" fill="#F7D046"/><rect x="9.5" y="2" width="5" height="5" fill="#F7D046"/><rect x="17" y="2" width="5" height="5" fill="#EF4444"/><rect x="2" y="9.5" width="5" height="5" fill="#F7D046"/><rect x="9.5" y="9.5" width="5" height="5" fill="#EF4444"/><rect x="17" y="9.5" width="5" height="5" fill="#F7D046"/><rect x="2" y="17" width="5" height="5" fill="#EF4444"/><rect x="9.5" y="17" width="5" height="5" fill="#F7D046"/><rect x="17" y="17" width="5" height="5" fill="#F7D046"/></svg>',
    deepseek: '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#3B82F6"/><text x="12" y="16" text-anchor="middle" fill="white" font-size="12" font-weight="700" font-family="sans-serif">D</text></svg>',
    alibaba: '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#F59E0B"/><text x="12" y="16" text-anchor="middle" fill="white" font-size="11" font-weight="700" font-family="sans-serif">Q</text></svg>',
    nvidia: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M9.2 9.48v-.72c.04 0 .08 0 .14-.01 2.1-.14 3.66 1.71 3.66 1.71s-1.74 2.04-3.52 2.04c-.11 0-.2-.01-.28-.03V9.48zm0-2.97v1.68l.06-.01c2.68-.2 4.63 2.2 4.63 2.2s-2.35 2.72-4.47 2.72a2.5 2.5 0 0 1-.22-.01v1.1c.06 0 .13.01.2.01 2.54 0 4.42-1.3 6.22-2.82a17.4 17.4 0 0 1 1.37 1.12c-2.06 1.66-6.86 3.22-7.77 3.22h-.02v1.26C11.5 16.96 18 13.18 18 13.18a21.88 21.88 0 0 0-4.06-3.84c1.3-1.14 2.82-1.85 2.82-1.85A16.67 16.67 0 0 0 9.2 6.51z" fill="#76B900"/></svg>'
  };

  function orgLogoSvg(org, color) {
    const key = String(org || '').trim().toLowerCase();
    if (ORG_LOGOS[key]) return ORG_LOGOS[key];
    const initial = (key || '?')[0].toUpperCase();
    const colors = { minimax: '#14B8A6', zai: '#8B5CF6', stepfun: '#EC4899', bytedance: '#00D1FF', tencent: '#2B82EE', amazon: '#FF9900', microsoft: '#00BCF2', ibm: '#054ADA', cohere: '#F43F5E' };
    const fill = safeColor(color) || colors[key] || '#C7C2BC';
    return `<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="${fill}"/><text x="12" y="16" text-anchor="middle" fill="white" font-size="11" font-weight="700" font-family="sans-serif">${initial}</text></svg>`;
  }

  function inferOrgFromModelName(name) {
    const text = String(name || '').toLowerCase();
    if (/gpt|openai|chatgpt|o[0-9]\b/.test(text)) return 'openai';
    if (/claude|anthropic/.test(text)) return 'anthropic';
    if (/gemini|gemma|google/.test(text)) return 'google';
    if (/grok|xai/.test(text)) return 'xai';
    if (/llama|meta/.test(text)) return 'meta';
    if (/mistral|mixtral|codestral/.test(text)) return 'mistral';
    if (/deepseek/.test(text)) return 'deepseek';
    if (/qwen|alibaba|tongyi/.test(text)) return 'alibaba';
    if (/nvidia|nemotron/.test(text)) return 'nvidia';
    if (/command|cohere/.test(text)) return 'cohere';
    if (/minimax/.test(text)) return 'minimax';
    return '';
  }

  function modelIconNode(model) {
    const iconUrl = safeUrl(model?.icon_url || model?.iconUrl || model?.logo_url || '', { image: true });
    const org = model?.org || model?.org_display || inferOrgFromModelName(model?.name || model?.modelName || '');
    const wrap = el('span', { class: 'model-org-logo', 'aria-hidden': 'true' });
    if (iconUrl) {
      const img = el('img', { src: iconUrl, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' });
      img.addEventListener('error', () => { wrap.innerHTML = orgLogoSvg(org, model?.color); }, { once: true });
      wrap.appendChild(img);
    } else if (org) {
      wrap.innerHTML = orgLogoSvg(org, model?.color);
    } else {
      return null;
    }
    return wrap;
  }

  const CONNECTOR_DOMAINS = {
    github: 'github.com', googledrive: 'drive.google.com', drive: 'drive.google.com',
    googledocs: 'docs.google.com', docs: 'docs.google.com', googlesheets: 'docs.google.com',
    googleslides: 'docs.google.com', gmail: 'mail.google.com', googlecalendar: 'calendar.google.com',
    calendar: 'calendar.google.com', notion: 'notion.so', linear: 'linear.app',
    dropbox: 'dropbox.com', vercel: 'vercel.com', supabase: 'supabase.com', slack: 'slack.com',
    figma: 'figma.com', airtable: 'airtable.com', hubspot: 'hubspot.com',
    salesforce: 'salesforce.com', stripe: 'stripe.com', discord: 'discord.com',
    spotify: 'spotify.com', youtube: 'youtube.com', asana: 'asana.com', jira: 'atlassian.com',
    confluence: 'atlassian.com', gitlab: 'gitlab.com', zoom: 'zoom.us', outlook: 'outlook.live.com'
  };

  function connectorFallbackLogoUrl(item) {
    const key = String(item?.slug || item?.id || item?.preset_id || item?.name || '')
      .toLowerCase().replace(/[^a-z0-9]/g, '');
    const domain = CONNECTOR_DOMAINS[key];
    return domain ? `https://www.google.com/s2/favicons?domain=${domain}&sz=64` : '';
  }

  function connectorLogoNode(item) {
    const fallback = () => el('span', {
      class: 'arena-connector-logo is-fallback', 'aria-hidden': 'true',
      text: initialLetters(item?.name || item?.slug || '?')
    });
    const logoUrl = safeUrl(item?.logo_url || item?.logoUrl || '', { image: true }) || connectorFallbackLogoUrl(item);
    if (!logoUrl) return fallback();
    const img = el('img', { class: 'arena-connector-logo', src: logoUrl, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' });
    img.addEventListener('error', () => { img.replaceWith(fallback()); }, { once: true });
    return img;
  }

  function accountAvatarNode(extraClass = '') {
    const node = el('span', {
      class: `avatar${extraClass ? ` ${extraClass}` : ''}${state.demo ? ' is-demo' : state.authenticated ? '' : ' is-anonymous'}`
    });
    fillAvatarNode(node);
    return node;
  }

  function fillAvatarNode(node) {
    if (!node) return;
    const displayName = state.fullName || state.email || 'Arena';
    const url = state.authenticated && !state.demo ? safeUrl(state.avatarUrl || '', { image: true }) : '';
    if (url) {
      if (node.dataset.avatarUrl === url && node.querySelector('img')) return;
      node.dataset.avatarUrl = url;
      node.textContent = '';
      const img = el('img', { src: url, alt: '', referrerpolicy: 'no-referrer' });
      img.addEventListener('error', () => {
        delete node.dataset.avatarUrl;
        node.textContent = initialLetters(displayName, 2);
      }, { once: true });
      node.appendChild(img);
      return;
    }
    delete node.dataset.avatarUrl;
    node.textContent = state.authenticated ? initialLetters(displayName, 2) : '—';
  }

  function relativeTime(raw) {
    if (!raw) return '';
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return String(raw);
    const diff = Date.now() - date.getTime();
    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;
    if (diff < 55 * 1000 && diff > -60 * 1000) return "just now";
    if (diff < hour) return `${Math.max(1, Math.round(diff / minute))} minutes ago`;
    if (diff < day) return `${Math.round(diff / hour)} hours ago`;
    if (diff < 2 * day) return "yesterday";
    if (diff < 7 * day) return `${Math.round(diff / day)} days ago`;
    try {
      return new Intl.DateTimeFormat('en-US', { day: 'numeric', month: 'short' }).format(date);
    } catch { return date.toLocaleDateString(); }
  }

  function formatClock(raw) {
    const date = raw ? new Date(raw) : new Date();
    if (Number.isNaN(date.getTime())) return '';
    try {
      return new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit' }).format(date);
    } catch { return ''; }
  }

  function truncate(value, length = 54) {
    const text = textValue(value).replace(/\s+/g, ' ').trim();
    return text.length > length ? `${text.slice(0, Math.max(1, length - 1))}…` : text;
  }

  function baseName(path) {
    if (!path) return '';
    const parts = String(path).replace(/[\\/]+$/, '').split(/[\\/]/);
    return parts[parts.length - 1] || String(path);
  }

  function errorMessage(error) {
    if (!error) return "An unknown error.";
    if (error instanceof ApiError) return error.message;
    if (error.name === 'AbortError') return "Request has been stopped.";
    if (error instanceof TypeError) {
      return "The local application server is unavailable. Check that the application is running and try again.";
    }
    return String(error.message || error);
  }

  async function copyText(value) {
    const text = String(value ?? '');
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch { /* fall through to legacy method */ }
    try {
      const area = el('textarea', { class: 'sr-only', readonly: true });
      area.value = text;
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand('copy');
      area.remove();
      return ok;
    } catch { return false; }
  }

  function showButtonFeedback(button, label = "Copied and copied") {
    if (!button) return;
    const original = button.dataset.originalLabel || button.textContent.trim();
    button.dataset.originalLabel = original;
    button.textContent = label;
    window.setTimeout(() => {
      if (button.isConnected) button.textContent = original;
    }, 1500);
  }

  function focusFirst(root) {
    if (!root) return;
    const candidate = root.querySelector('[data-autofocus], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])');
    if (candidate) {
      try { candidate.focus({ preventScroll: true }); } catch { candidate.focus(); }
    }
  }

  function trapFocus(event, container) {
    if (event.key !== 'Tab' || !container) return;
    const focusable = $$('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])', container)
      .filter((node) => node.offsetParent !== null || node === document.activeElement);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const anchor = el('a', { href: url, download: filename });
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 2500);
  }
