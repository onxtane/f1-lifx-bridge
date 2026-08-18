/* Effect Customization — replaces the old "Manual Triggers" section.
 *
 * Lets the user set custom colours per effect (All-Lights or Per-Light), see the
 * effect's animation read-only, and fire the effect to test it on real lights.
 *
 * Phase 1 (this file): the UI + persistence to gui_settings.effect_colors, so the
 * whole thing is wiring-ready. Phase 2 wires the three controllers (LIFX/Hue/
 * Nanoleaf) to actually paint with these colours. Reuses the app's createColorPicker
 * (exposed on window) and the game-def system (window._GAME_DEFS / _currentGame).
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

  // Per-effect metadata. `def` colours match the current hardcoded look so
  // "Reset to Default" restores today's behaviour. `anim` is shown read-only —
  // the animation shape is fixed; only the colours are customizable.
  const EFFECT_META = {
    start_lights:   { icon: '🔴', name: 'Start Lights',   desc: 'Red formation lights ramp up',   trigger: 'Start Lights',   slots: [{ key: 'main', label: 'Formation lights' }], def: { main: '#ff2828' }, anim: { style: 'Sequential fill', speed: '1 → 5 by count', repeat: 'Once' } },
    lights_out:     { icon: '🟢', name: 'Lights Out',     desc: 'Green flash — race start',        trigger: 'Lights Out',     slots: [{ key: 'main', label: 'Go colour' }],       def: { main: '#22e04a' }, anim: { style: 'Double flash → settle', speed: '~0.7s', repeat: 'Once' } },
    yellow_flag:    { icon: '🟡', name: 'Yellow Flag',    desc: 'Continuous yellow flashing',      trigger: 'Yellow Flag',    slots: [{ key: 'main', label: 'Flag colour' }],     def: { main: '#ffcc00' }, anim: { style: 'Flash', speed: '0.45s', repeat: 'Continuous' } },
    blue_flag:      { icon: '🔵', name: 'Blue Flag',      desc: 'Solid blue — let faster car past', trigger: 'Blue Flag',     slots: [{ key: 'main', label: 'Flag colour' }],     def: { main: '#2f6bff' }, anim: { style: 'Pulse', speed: '0.6s', repeat: 'Continuous' } },
    red_flag:       { icon: '🚩', name: 'Red Flag',       desc: 'Session stopped',                 trigger: 'Red Flag',       slots: [{ key: 'main', label: 'Flag colour' }],     def: { main: '#ff2020' }, anim: { style: 'Pulse', speed: '0.6s', repeat: 'Continuous' } },
    fastest_lap:    { icon: '💜', name: 'Fastest Lap',    desc: 'Purple flash celebration',        trigger: 'Fastest Lap',    slots: [{ key: 'main', label: 'Flash colour' }],    def: { main: '#b14bff' }, anim: { style: 'Flash', speed: '0.2s', repeat: '3×' } },
    chequered_flag: { icon: '🏁', name: 'Chequered Flag', desc: 'Race over — alternating flash',   trigger: 'Chequered Flag', slots: [{ key: 'a', label: 'Colour A' }, { key: 'b', label: 'Colour B' }], def: { a: '#ffffff', b: '#22e04a' }, anim: { style: 'Alternate flash', speed: '0.3s', repeat: '5×' } },
    white_warning:  { icon: '⚪', name: 'White Warning',  desc: 'Penalty warning flash',           trigger: 'White Warning',  slots: [{ key: 'main', label: 'Flash colour' }],    def: { main: '#ffffff' }, anim: { style: 'Flash', speed: '0.25s', repeat: '3×' } },
    crash:          { icon: '⚡', name: 'Crash',          desc: 'Sharp impact flash',              trigger: 'Crash',          slots: [{ key: 'main', label: 'Impact colour' }],   def: { main: '#ffffff' }, anim: { style: 'Single flash', speed: '0.12s', repeat: 'Once' } },
    neutral:        { icon: '💡', name: 'Neutral',        desc: 'Reset to idle',                   trigger: 'Neutral',        slots: [], idleLink: true, def: {}, anim: { style: 'Settle', speed: '0.8s', repeat: '—' } },
  };
  const ORDER = ['start_lights', 'lights_out', 'yellow_flag', 'blue_flag', 'red_flag', 'fastest_lap', 'chequered_flag', 'white_warning', 'crash', 'neutral'];

  // ---- state ----
  let colors = {};        // effect_colors: { key: { mode, colors:{slot:hex}, per_light:[hex...] } }
  let lightCount = 5;     // per-light dot count (from discovered lights)
  let selectedKey = null; // effect open in the editor
  let selectedLight = 0;  // per-light: selected dot
  let gameEvents = null;  // Set of effect keys for the current game (null = all)

  function toast(msg) { if (window.showToast) window.showToast(msg); }
  function gdef() { return (window._GAME_DEFS && window._currentGame) ? window._GAME_DEFS[window._currentGame] : null; }
  function nameFor(key) { const g = gdef(); return (g && g.names && g.names[key]) || EFFECT_META[key].name; }
  function descFor(key) { const g = gdef(); return (g && g.descs && g.descs[key]) || EFFECT_META[key].desc; }

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
    if (!meta.slots.length) return '#8b7cf6';
    return cfg(key).colors[meta.slots[0].key] || meta.def[meta.slots[0].key] || '#8b7cf6';
  }
  function persist() { callApi('save_gui_settings', { effect_colors: colors }); }

  // ---- list (left) ----
  function keysForGame() { return ORDER.filter(k => !gameEvents || gameEvents.has(k)); }
  function renderList() {
    const list = document.getElementById('ecList');
    if (!list) return;
    const keys = keysForGame();
    list.innerHTML = keys.map(k =>
      '<div class="ec-item' + (k === selectedKey ? ' sel' : '') + '" data-key="' + k + '">'
      + '<span class="ec-item-dot" style="background:' + esc(primaryHex(k)) + '"></span>'
      + '<div class="ec-item-info"><div class="ec-item-name">' + esc(nameFor(k)) + '</div>'
      + '<div class="ec-item-desc">' + esc(descFor(k)) + '</div></div></div>'
    ).join('');
  }
  function updateListDot(key) {
    const el = document.querySelector('.ec-item[data-key="' + key + '"] .ec-item-dot');
    if (el) el.style.background = primaryHex(key);
  }

  // ---- editor (right) ----
  function renderEditor() {
    const ed = document.getElementById('ecEditor');
    if (!ed || !selectedKey) return;
    const meta = EFFECT_META[selectedKey];
    const c = cfg(selectedKey);
    ed.innerHTML =
      '<div class="ec-ed-head">'
      + '<span class="ec-ed-icon">' + meta.icon + '</span>'
      + '<div class="ec-ed-title"><div class="ec-ed-name">' + esc(nameFor(selectedKey)) + '</div>'
      + '<div class="ec-ed-desc">' + esc(descFor(selectedKey)) + '</div></div>'
      + '<div class="ec-ed-actions">'
      + '<button class="btn btn-ghost btn-sm" id="ecTest"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" style="vertical-align:-2px;margin-right:3px;"><path d="M8 5v14l11-7z"/></svg>Test on lights</button>'
      + (meta.idleLink ? '' : '<button class="btn btn-ghost btn-sm" id="ecReset">Reset to Default</button>')
      + '</div></div>'
      + (meta.idleLink
        ? '<div class="ec-idle-note"><b>Neutral</b> uses your <b>Idle Color</b> from Settings. <button class="ec-link" id="ecGotoIdle">Open Idle Color →</button></div>'
        : '<div class="ec-mode"><button class="ec-seg' + (c.mode === 'all' ? ' active' : '') + '" data-mode="all">All Lights</button>'
          + '<button class="ec-seg' + (c.mode === 'per_light' ? ' active' : '') + '" data-mode="per_light">Per Light</button></div>')
      + '<div class="ec-grid">'
      + '<div class="ec-colors" id="ecColors"></div>'
      + '<div class="ec-side">'
      + '<div class="ec-anim"><div class="ec-anim-head">Animation <span class="ec-ro">read-only</span></div>'
      + '<div class="ec-anim-row"><span>Style</span><b>' + esc(meta.anim.style) + '</b></div>'
      + '<div class="ec-anim-row"><span>Speed</span><b>' + esc(meta.anim.speed) + '</b></div>'
      + '<div class="ec-anim-row"><span>Repeat</span><b>' + esc(meta.anim.repeat) + '</b></div>'
      + '<p class="ec-anim-note">Colours are customizable; the animation follows GridGlow\'s built-in pattern.</p></div>'
      + '<div class="ec-preview"><div class="ec-prev-label">Preview</div><div class="ec-prev-stage"><div class="ec-prev-light" id="ecPrevLight"></div></div></div>'
      + '</div></div>';

    // wire header
    const test = document.getElementById('ecTest');
    if (test) test.addEventListener('click', () => { callApi('trigger_effect', meta.trigger); toast('Triggered ' + nameFor(selectedKey)); });
    const reset = document.getElementById('ecReset');
    if (reset) reset.addEventListener('click', () => {
      const cc = cfg(selectedKey); cc.colors = Object.assign({}, meta.def); cc.per_light = [];
      persist(); updateListDot(selectedKey); renderColors(); updatePreview();
    });
    const goIdle = document.getElementById('ecGotoIdle');
    if (goIdle) goIdle.addEventListener('click', () => {
      const btn = document.querySelector('.nav-btn[data-page="settings"]'); if (btn) btn.click();
    });
    ed.querySelectorAll('.ec-seg[data-mode]').forEach(seg => seg.addEventListener('click', () => {
      const c2 = cfg(selectedKey); c2.mode = seg.dataset.mode; persist();
      ed.querySelectorAll('.ec-seg').forEach(s => s.classList.toggle('active', s === seg));
      renderColors(); updatePreview();
    }));

    renderColors();
    updatePreview();
  }

  function renderColors() {
    const box = document.getElementById('ecColors');
    if (!box) return;
    const meta = EFFECT_META[selectedKey];
    const c = cfg(selectedKey);
    box.innerHTML = '';
    if (meta.idleLink) { box.innerHTML = '<p class="ec-empty">No colour to set — Neutral follows your Idle Color.</p>'; return; }

    if (c.mode === 'all') {
      box.insertAdjacentHTML('beforeend', '<div class="ec-colors-title">' + (meta.slots.length > 1 ? 'Effect Colours' : 'Effect Colour') + '</div>');
      meta.slots.forEach(slot => {
        const row = document.createElement('div'); row.className = 'ec-slot';
        row.innerHTML = '<span class="ec-slot-label">' + esc(slot.label) + '</span>';
        const mount = document.createElement('div'); row.appendChild(mount); box.appendChild(row);
        window.createColorPicker({
          mount: mount, align: 'left', value: c.colors[slot.key] || meta.def[slot.key] || '#ffffff',
          onInput: hex => { c.colors[slot.key] = hex; if (slot.key === meta.slots[0].key) updateListDot(selectedKey); updatePreview(); },
          onChange: hex => { c.colors[slot.key] = hex; persist(); },
        });
      });
    } else {
      // Per-light: a row of numbered dots + a picker for the selected light.
      box.insertAdjacentHTML('beforeend',
        '<div class="ec-colors-title">Per Light Colours <span class="ec-sub">Click a light, then pick its colour</span></div>'
        + '<div class="ec-dots" id="ecDots"></div>'
        + '<div class="ec-perpick"><span class="ec-slot-label" id="ecPerLabel"></span><div id="ecPerMount"></div>'
        + '<button class="ec-link" id="ecOrder">Setup Light Order →</button></div>');
      selectedLight = Math.max(0, Math.min(lightCount - 1, selectedLight));
      renderDots();
      mountPerPicker();
      const order = document.getElementById('ecOrder');
      if (order) order.addEventListener('click', () => { toast('Light order lives in Light Assignment (Lights page)'); });
      document.getElementById('ecDots').addEventListener('click', e => {
        const d = e.target.closest('.ec-dot'); if (!d) return;
        selectedLight = +d.dataset.i; renderDots(); mountPerPicker();
      });
    }
  }
  function renderDots() {
    const el = document.getElementById('ecDots'); if (!el) return;
    const c = cfg(selectedKey);
    let html = '';
    for (let i = 0; i < lightCount; i++) {
      const col = c.per_light[i] || primaryHex(selectedKey);
      html += '<button class="ec-dot' + (i === selectedLight ? ' sel' : '') + '" data-i="' + i + '" style="--c:' + esc(col) + '"><span class="ec-dot-n">' + (i + 1) + '</span></button>';
    }
    el.innerHTML = html;
  }
  function mountPerPicker() {
    const m = document.getElementById('ecPerMount'); if (!m) return; m.innerHTML = '';
    const lbl = document.getElementById('ecPerLabel'); if (lbl) lbl.textContent = 'Light ' + (selectedLight + 1);
    const c = cfg(selectedKey);
    const val = c.per_light[selectedLight] || primaryHex(selectedKey);
    window.createColorPicker({
      mount: m, align: 'left', value: val,
      onInput: hex => { c.per_light[selectedLight] = hex; const d = document.querySelector('.ec-dot[data-i="' + selectedLight + '"]'); if (d) d.style.setProperty('--c', hex); updatePreview(); },
      onChange: hex => { c.per_light[selectedLight] = hex; persist(); },
    });
  }

  function updatePreview() {
    const light = document.getElementById('ecPrevLight'); if (!light) return;
    const meta = EFFECT_META[selectedKey];
    const c = cfg(selectedKey);
    let cols;
    if (meta.idleLink) cols = ['#f6c66a'];
    else if (c.mode === 'per_light') cols = Array.from({ length: lightCount }, (_, i) => c.per_light[i] || primaryHex(selectedKey));
    else cols = meta.slots.map(s => c.colors[s.key] || meta.def[s.key]);
    light.style.setProperty('--c1', cols[0] || '#8b7cf6');
    light.style.setProperty('--c2', cols[1] || cols[0] || '#8b7cf6');
    // choose a preview animation from the effect's style
    const style = meta.anim.style.toLowerCase();
    let anim = 'ecPulse';
    if (style.indexOf('flash') >= 0) anim = 'ecFlash';
    else if (style.indexOf('fill') >= 0) anim = 'ecFill';
    else if (style.indexOf('settle') >= 0) anim = 'ecSettle';
    light.className = 'ec-prev-light';
    // force reflow so the animation restarts
    void light.offsetWidth;
    light.style.animationName = anim;
  }

  function selectEffect(key) {
    selectedKey = key; selectedLight = 0;
    document.querySelectorAll('.ec-item').forEach(it => it.classList.toggle('sel', it.dataset.key === key));
    renderEditor();
  }

  // ---- mount / refresh ----
  function ensureSelection() {
    const keys = keysForGame();
    if (!keys.length) { selectedKey = null; const ed = document.getElementById('ecEditor'); if (ed) ed.innerHTML = ''; return; }
    if (!selectedKey || keys.indexOf(selectedKey) < 0) selectedKey = keys[0];
  }
  function render() { renderList(); ensureSelection(); if (selectedKey) selectEffect(selectedKey); }

  root.innerHTML = '<div class="ec-wrap"><div class="ec-list" id="ecList"></div><div class="ec-editor" id="ecEditor"></div></div>';
  document.getElementById('ecList').addEventListener('click', e => {
    const it = e.target.closest('.ec-item'); if (it) selectEffect(it.dataset.key);
  });

  // Game-awareness: re-render the list for the active game (called from _applyGameEffects).
  window._effectCustomRefresh = function (game) {
    const g = window._GAME_DEFS && window._GAME_DEFS[game];
    gameEvents = g && Array.isArray(g.events) ? new Set(g.events.filter(k => EFFECT_META[k])) : null;
    render();
  };

  // Load saved colours + light count, then render.
  Promise.resolve(callApi('get_gui_settings')).then(s => {
    if (s && s.effect_colors && typeof s.effect_colors === 'object') colors = s.effect_colors;
  }).catch(() => {}).then(() => callApi('get_discovered_lights')).then(list => {
    if (Array.isArray(list) && list.length) lightCount = Math.max(1, Math.min(20, list.length));
    render();
  }).catch(() => render());
})();
