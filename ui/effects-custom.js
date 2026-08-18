/* Effect Customization — replaces the old "Manual Triggers" section.
 *
 * Set custom colours per effect (Sync All or Customize Per Light/Zone), see the
 * effect's animation read-only, and fire the effect to test it on real lights.
 *
 * Phase 1: UI + persistence to gui_settings.effect_colors (wiring-ready). Phase 2
 * threads these colours through the three controllers so triggers actually paint
 * with them. Reuses createColorPicker (window) + the game-def system, and the
 * hidden legacy effect-card SVGs for per-effect icons.
 */
(function () {
  'use strict';
  const root = document.getElementById('effectCustomRoot');
  if (!root) return;

  function hasBridge() { return !!(window.pywebview && window.pywebview.api); }
  function callApi(fn) {
    const a = [].slice.call(arguments, 1);
    if (hasBridge() && window.pywebview.api[fn]) return window.pywebview.api[fn].apply(null, a);
    return Promise.resolve(null);
  }
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function hexA(hex, a) {
    hex = String(hex || '').replace('#', '');
    if (hex.length === 3) hex = hex.split('').map(x => x + x).join('');
    const n = parseInt(hex, 16);
    if (isNaN(n) || hex.length !== 6) return 'rgba(139,124,246,' + a + ')';
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  // Per-effect metadata. `def` colours match today's hardcoded look (Reset restores
  // it). `anim` is shown read-only. Icons come from the legacy effect cards at runtime.
  const EFFECT_META = {
    start_lights:   { icon: '🔴', name: 'Start Lights',   desc: 'Red formation lights ramp up',    trigger: 'Start Lights',   slots: [{ key: 'main', label: 'Formation lights' }], def: { main: '#ff2828' }, anim: { style: 'Sequential fill', speed: '1 → 5 by count', repeat: 'Once' } },
    lights_out:     { icon: '🟢', name: 'Lights Out',     desc: 'Green flash — race start',         trigger: 'Lights Out',     slots: [{ key: 'main', label: 'Go colour' }],       def: { main: '#22e04a' }, anim: { style: 'Double flash → settle', speed: '~0.7s', repeat: 'Once' } },
    yellow_flag:    { icon: '🟡', name: 'Yellow Flag',    desc: 'Continuous yellow flashing',       trigger: 'Yellow Flag',    slots: [{ key: 'main', label: 'Flag colour' }],     def: { main: '#ffcc00' }, anim: { style: 'Flash', speed: '0.45s', repeat: 'Continuous' } },
    blue_flag:      { icon: '🔵', name: 'Blue Flag',      desc: 'Solid blue — let faster car past', trigger: 'Blue Flag',      slots: [{ key: 'main', label: 'Flag colour' }],     def: { main: '#2f6bff' }, anim: { style: 'Pulse', speed: '0.6s', repeat: 'Continuous' } },
    red_flag:       { icon: '🚩', name: 'Red Flag',       desc: 'Session stopped',                  trigger: 'Red Flag',       slots: [{ key: 'main', label: 'Flag colour' }],     def: { main: '#ff2020' }, anim: { style: 'Pulse', speed: '0.6s', repeat: 'Continuous' } },
    fastest_lap:    { icon: '💜', name: 'Fastest Lap',    desc: 'Purple flash celebration',         trigger: 'Fastest Lap',    slots: [{ key: 'main', label: 'Flash colour' }],    def: { main: '#b14bff' }, anim: { style: 'Flash', speed: '0.2s', repeat: '3×' } },
    chequered_flag: { icon: '🏁', name: 'Chequered Flag', desc: 'Race over — alternating flash',    trigger: 'Chequered Flag', slots: [{ key: 'a', label: 'Colour A' }, { key: 'b', label: 'Colour B' }], def: { a: '#ffffff', b: '#22e04a' }, anim: { style: 'Alternate flash', speed: '0.3s', repeat: '5×' } },
    white_warning:  { icon: '⚪', name: 'White Warning',  desc: 'Penalty warning flash',            trigger: 'White Warning',  slots: [{ key: 'main', label: 'Flash colour' }],    def: { main: '#ffffff' }, anim: { style: 'Flash', speed: '0.25s', repeat: '3×' } },
    crash:          { icon: '⚡', name: 'Crash',          desc: 'Sharp impact flash',               trigger: 'Crash',          slots: [{ key: 'main', label: 'Impact colour' }],   def: { main: '#ffffff' }, anim: { style: 'Single flash', speed: '0.12s', repeat: 'Once' } },
    neutral:        { icon: '💡', name: 'Neutral',        desc: 'Reset to idle',                    trigger: 'Neutral',        slots: [], idleLink: true, def: {}, anim: { style: 'Settle', speed: '0.8s', repeat: '—' } },
  };
  const ORDER = ['start_lights', 'lights_out', 'yellow_flag', 'blue_flag', 'red_flag', 'fastest_lap', 'chequered_flag', 'white_warning', 'crash', 'neutral'];

  // ---- state ----
  let colors = {};          // effect_colors: { key: { mode, colors:{slot:hex}, per_light:[hex...] } }
  let lightCount = 5;       // individual bulbs
  let zones = 16;           // colorable sections on a multizone device
  let hasMultizone = false; // a strip / Nanoleaf is present
  let selectedKey = null;
  let selectedSection = 0;  // selected light/zone in Customize mode
  let gameEvents = null;    // Set of effect keys for the current game (null = all)
  let previewRaf = 0;

  function toast(msg) { if (window.showToast) window.showToast(msg); }
  function gdef() { return (window._GAME_DEFS && window._currentGame) ? window._GAME_DEFS[window._currentGame] : null; }
  function nameFor(key) { const g = gdef(); return (g && g.names && g.names[key]) || EFFECT_META[key].name; }
  function descFor(key) { const g = gdef(); return (g && g.descs && g.descs[key]) || EFFECT_META[key].desc; }

  // per-effect icon (SVG) pulled from the hidden legacy manual-trigger cards.
  const _iconCache = {};
  function iconFor(key) {
    if (_iconCache[key]) return _iconCache[key];
    const el = document.querySelector('.effect-card[data-effect-key="' + key + '"] .icon');
    const r = el ? { html: el.innerHTML, color: el.style.color || '' } : { html: '<span>' + EFFECT_META[key].icon + '</span>', color: '' };
    _iconCache[key] = r; return r;
  }

  function cfg(key) {
    if (!colors[key]) colors[key] = {};
    const c = colors[key];
    if (c.mode !== 'per_light') c.mode = 'all';
    if (!c.colors || typeof c.colors !== 'object') c.colors = Object.assign({}, EFFECT_META[key].def);
    if (!Array.isArray(c.per_light)) c.per_light = [];
    return c;
  }
  function primaryHex(key) {
    const meta = EFFECT_META[key];
    if (!meta.slots.length) return meta.icon && iconFor(key).color || '#f6c66a';
    return cfg(key).colors[meta.slots[0].key] || meta.def[meta.slots[0].key] || '#8b7cf6';
  }
  // Icon glyph colour reflects the customized colour; neutral uses its canonical tint.
  function iconColor(key) { return EFFECT_META[key].slots.length ? primaryHex(key) : (iconFor(key).color || '#f6c66a'); }
  function persist() { callApi('save_gui_settings', { effect_colors: colors }); }

  // With a strip present, "your lights" are its zones; otherwise they're bulbs.
  function useZones() { return hasMultizone; }
  function sectionCount() { return useZones() ? Math.max(1, Math.min(30, zones)) : Math.max(1, Math.min(20, lightCount)); }
  function sectionWord() { return useZones() ? 'Zone' : 'Light'; }

  // ---- list (left) ----
  function keysForGame() { return ORDER.filter(k => !gameEvents || gameEvents.has(k)); }
  function renderList() {
    const list = document.getElementById('ecList');
    if (!list) return;
    list.innerHTML = keysForGame().map(k => {
      const ic = iconFor(k), col = iconColor(k);
      return '<div class="ec-item' + (k === selectedKey ? ' sel' : '') + '" data-key="' + k + '">'
        + '<span class="ec-item-ic" style="color:' + esc(col) + ';background:' + hexA(col, 0.14) + ';border-color:' + hexA(col, 0.35) + '">' + ic.html + '</span>'
        + '<div class="ec-item-info"><div class="ec-item-name">' + esc(nameFor(k)) + '</div>'
        + '<div class="ec-item-desc">' + esc(descFor(k)) + '</div></div>'
        + '<span class="ec-item-more">⋯</span></div>';
    }).join('');
  }
  function refreshListIcon(key) {
    const ic = document.querySelector('.ec-item[data-key="' + key + '"] .ec-item-ic');
    if (!ic) return;
    const col = iconColor(key);
    ic.style.color = col; ic.style.background = hexA(col, 0.14); ic.style.borderColor = hexA(col, 0.35);
  }

  // ---- editor (right) ----
  function renderEditor() {
    const ed = document.getElementById('ecEditor');
    if (!ed || !selectedKey) return;
    const meta = EFFECT_META[selectedKey];
    const c = cfg(selectedKey);
    const ic = iconFor(selectedKey);
    ed.innerHTML =
      '<div class="ec-ed-head">'
      + '<span class="ec-ed-icon" style="color:' + esc(iconColor(selectedKey)) + ';background:' + hexA(iconColor(selectedKey), 0.14) + '">' + ic.html + '</span>'
      + '<div class="ec-ed-title"><div class="ec-ed-name">' + esc(nameFor(selectedKey)) + '</div>'
      + '<div class="ec-ed-desc">' + esc(descFor(selectedKey)) + '</div></div>'
      + '<div class="ec-ed-actions">'
      + '<button class="btn btn-ghost btn-sm" id="ecTest"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" style="vertical-align:-2px;margin-right:3px;"><path d="M8 5v14l11-7z"/></svg>Test on lights</button>'
      + (meta.idleLink ? '' : '<button class="btn btn-ghost btn-sm" id="ecReset">Reset to Default</button>')
      + '</div></div>'
      + (meta.idleLink
        ? '<div class="ec-idle-note"><b>Neutral</b> uses your <b>Idle Color</b> from Settings. <button class="ec-link" id="ecGotoIdle">Open Idle Color →</button></div>'
        : '<div class="ec-mode"><button class="ec-seg' + (c.mode === 'all' ? ' active' : '') + '" data-mode="all">Sync All</button>'
          + '<button class="ec-seg' + (c.mode === 'per_light' ? ' active' : '') + '" data-mode="per_light">Customize Per ' + sectionWord() + '</button></div>')
      + '<div class="ec-grid">'
      + '<div class="ec-colors" id="ecColors"></div>'
      + '<div class="ec-side">'
      + '<div class="ec-anim"><div class="ec-anim-head">Animation <span class="ec-ro">read-only</span></div>'
      + '<div class="ec-anim-row"><span>Style</span><b>' + esc(meta.anim.style) + '</b></div>'
      + '<div class="ec-anim-row"><span>Speed</span><b>' + esc(meta.anim.speed) + '</b></div>'
      + '<div class="ec-anim-row"><span>Repeat</span><b>' + esc(meta.anim.repeat) + '</b></div>'
      + '<p class="ec-anim-note">Colours are customizable; the animation follows GridGlow\'s built-in pattern.</p></div>'
      + '<div class="ec-preview"><div class="ec-prev-label">Preview</div><div class="ec-prev-stage" id="ecPrevStrip"></div></div>'
      + '</div></div>';

    const test = document.getElementById('ecTest');
    if (test) test.addEventListener('click', () => { callApi('trigger_effect', meta.trigger); toast('Triggered ' + nameFor(selectedKey)); });
    const reset = document.getElementById('ecReset');
    if (reset) reset.addEventListener('click', () => {
      const cc = cfg(selectedKey); cc.colors = Object.assign({}, meta.def); cc.per_light = [];
      persist(); refreshListIcon(selectedKey); renderColors();
    });
    const goIdle = document.getElementById('ecGotoIdle');
    if (goIdle) goIdle.addEventListener('click', () => { const b = document.querySelector('.nav-btn[data-page="settings"]'); if (b) b.click(); });
    ed.querySelectorAll('.ec-seg[data-mode]').forEach(seg => seg.addEventListener('click', () => {
      const c2 = cfg(selectedKey); c2.mode = seg.dataset.mode; persist();
      ed.querySelectorAll('.ec-seg').forEach(s => s.classList.toggle('active', s === seg));
      renderColors();
    }));

    renderColors();
    startPreview();
  }

  function renderColors() {
    const box = document.getElementById('ecColors');
    if (!box) return;
    const meta = EFFECT_META[selectedKey];
    const c = cfg(selectedKey);
    box.innerHTML = '';
    if (meta.idleLink) { box.innerHTML = '<p class="ec-empty">No colour to set — Neutral follows your Idle Color.</p>'; startPreview(); return; }

    if (c.mode === 'all') {
      box.insertAdjacentHTML('beforeend', '<div class="ec-colors-title">' + (meta.slots.length > 1 ? 'Effect Colours' : 'Effect Colour') + '</div>');
      meta.slots.forEach(slot => {
        const row = document.createElement('div'); row.className = 'ec-slot';
        row.innerHTML = '<span class="ec-slot-label">' + esc(slot.label) + '</span>';
        const mount = document.createElement('div'); row.appendChild(mount); box.appendChild(row);
        window.createColorPicker({
          mount: mount, align: 'left', value: c.colors[slot.key] || meta.def[slot.key] || '#ffffff',
          onInput: hex => { c.colors[slot.key] = hex; if (slot.key === meta.slots[0].key) refreshListIcon(selectedKey); },
          onChange: hex => { c.colors[slot.key] = hex; persist(); },
        });
      });
    } else {
      const word = sectionWord();
      box.insertAdjacentHTML('beforeend',
        '<div class="ec-colors-title">Per ' + word + ' Colours <span class="ec-sub">Click a ' + word.toLowerCase() + ', then pick its colour</span></div>'
        + (useZones()
          ? '<div class="ec-zonestrip" id="ecSections"></div>'
          : '<div class="ec-dots" id="ecSections"></div>')
        + '<div class="ec-perpick"><span class="ec-slot-label" id="ecPerLabel"></span><div id="ecPerMount"></div>'
        + '<button class="ec-link" id="ecOrder">Setup Light Order →</button></div>');
      selectedSection = Math.max(0, Math.min(sectionCount() - 1, selectedSection));
      renderSections();
      mountPerPicker();
      const order = document.getElementById('ecOrder');
      if (order) order.addEventListener('click', () => { toast('Light order lives in Light Assignment (Lights page)'); });
      document.getElementById('ecSections').addEventListener('click', e => {
        const s = e.target.closest('[data-i]'); if (!s) return;
        selectedSection = +s.dataset.i; renderSections(); mountPerPicker();
      });
    }
    startPreview();
  }

  function renderSections() {
    const el = document.getElementById('ecSections'); if (!el) return;
    const c = cfg(selectedKey), n = sectionCount(), zoned = useZones();
    let html = '';
    for (let i = 0; i < n; i++) {
      const col = c.per_light[i] || primaryHex(selectedKey);
      if (zoned) html += '<button class="ec-zone' + (i === selectedSection ? ' sel' : '') + '" data-i="' + i + '" style="--c:' + esc(col) + '"></button>';
      else html += '<button class="ec-dot' + (i === selectedSection ? ' sel' : '') + '" data-i="' + i + '" style="--c:' + esc(col) + '"><span class="ec-dot-n">' + (i + 1) + '</span></button>';
    }
    el.innerHTML = html;
  }
  function mountPerPicker() {
    const m = document.getElementById('ecPerMount'); if (!m) return; m.innerHTML = '';
    const lbl = document.getElementById('ecPerLabel'); if (lbl) lbl.textContent = sectionWord() + ' ' + (selectedSection + 1);
    const c = cfg(selectedKey);
    window.createColorPicker({
      mount: m, align: 'left', value: c.per_light[selectedSection] || primaryHex(selectedKey),
      onInput: hex => { c.per_light[selectedSection] = hex; const d = document.querySelector('#ecSections [data-i="' + selectedSection + '"]'); if (d) d.style.setProperty('--c', hex); },
      onChange: hex => { c.per_light[selectedSection] = hex; persist(); },
    });
  }

  // ---- animated preview (segment strip) ----
  function startPreview() {
    cancelAnimationFrame(previewRaf);
    const stage = document.getElementById('ecPrevStrip'); if (!stage) return;
    const meta = EFFECT_META[selectedKey], c = cfg(selectedKey);
    const n = meta.idleLink ? 6 : Math.min(16, sectionCount());
    stage.innerHTML = '';
    const cells = [];
    for (let i = 0; i < n; i++) { const d = document.createElement('div'); d.className = 'ec-cell'; stage.appendChild(d); cells.push(d); }
    const OFF = '#151b2b';
    function colAt(i) {
      if (meta.idleLink) return primaryHex(selectedKey);
      if (c.mode === 'per_light') return c.per_light[i] || primaryHex(selectedKey);
      return c.colors[meta.slots[0].key] || meta.def[meta.slots[0].key] || primaryHex(selectedKey);
    }
    // Paint a sensible static state immediately, so the preview is never blank
    // (and is correct even where requestAnimationFrame is paused/throttled).
    cells.forEach((cell, i) => { const col = colAt(i); cell.style.background = col; cell.style.boxShadow = '0 0 8px ' + col; });
    if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const style = meta.anim.style.toLowerCase();
    const t0 = performance.now();
    function paint(cell, on, col) { cell.style.background = on ? col : OFF; cell.style.boxShadow = on ? '0 0 9px ' + col : 'none'; cell.style.opacity = '1'; }
    function frame(now) {
      const t = now - t0;
      if (style.indexOf('sequential') >= 0 || style.indexOf('fill') >= 0) {
        const period = 1700, p = (t % period) / period, lit = Math.min(n, Math.floor(p * (n + 2)));
        cells.forEach((cell, i) => paint(cell, i < lit, colAt(i)));
      } else if (style.indexOf('alternate') >= 0) {
        const flip = (Math.floor(t / 300) % 2) === 0, A = c.colors.a || meta.def.a, B = c.colors.b || meta.def.b;
        cells.forEach((cell, i) => { const col = ((i % 2 === 0) === flip) ? A : B; paint(cell, true, col); });
      } else if (style.indexOf('single') >= 0) {
        const on = (t % 1400) < 160; cells.forEach((cell, i) => paint(cell, on, colAt(i)));
      } else if (style.indexOf('flash') >= 0) {
        const on = (Math.floor(t / 300) % 2) === 0; cells.forEach((cell, i) => paint(cell, on, colAt(i)));
      } else if (style.indexOf('pulse') >= 0) {
        const k = Math.sin(t / 480) * 0.5 + 0.5; cells.forEach((cell, i) => { const col = colAt(i); cell.style.background = col; cell.style.boxShadow = '0 0 11px ' + col; cell.style.opacity = (0.28 + 0.72 * k).toFixed(2); });
      } else { // settle / double flash → hold on colour
        cells.forEach((cell, i) => { const col = colAt(i); cell.style.background = col; cell.style.boxShadow = '0 0 8px ' + col; cell.style.opacity = '0.92'; });
      }
      previewRaf = requestAnimationFrame(frame);
    }
    previewRaf = requestAnimationFrame(frame);
  }

  function selectEffect(key) {
    selectedKey = key; selectedSection = 0;
    document.querySelectorAll('.ec-item').forEach(it => it.classList.toggle('sel', it.dataset.key === key));
    renderEditor();
  }

  function ensureSelection() {
    const keys = keysForGame();
    if (!keys.length) { selectedKey = null; const ed = document.getElementById('ecEditor'); if (ed) ed.innerHTML = ''; return; }
    if (!selectedKey || keys.indexOf(selectedKey) < 0) selectedKey = keys[0];
  }
  function render() { renderList(); ensureSelection(); if (selectedKey) selectEffect(selectedKey); }

  root.innerHTML = '<div class="ec-wrap"><div class="ec-list" id="ecList"></div><div class="ec-editor" id="ecEditor"></div></div>';
  document.getElementById('ecList').addEventListener('click', e => { const it = e.target.closest('.ec-item'); if (it) selectEffect(it.dataset.key); });

  window._effectCustomRefresh = function (game) {
    const g = window._GAME_DEFS && window._GAME_DEFS[game];
    gameEvents = g && Array.isArray(g.events) ? new Set(g.events.filter(k => EFFECT_META[k])) : null;
    render();
  };

  Promise.resolve(callApi('get_gui_settings')).then(s => {
    if (s && s.effect_colors && typeof s.effect_colors === 'object') colors = s.effect_colors;
  }).catch(() => {}).then(() => callApi('get_workshop_capabilities')).then(caps => {
    if (caps) {
      hasMultizone = !!caps.has_multizone;
      if (caps.zones) zones = Math.max(1, Math.min(30, caps.zones));
      if (caps.light_count) lightCount = Math.max(1, Math.min(20, caps.light_count));
    }
    render();
  }).catch(() => render());
})();
