/* Effect Customization — replaces the old "Manual Triggers" section.
 *
 * Set custom colours per effect in three modes — Sync All, Customize Per Light
 * (bulbs), Customize Per Zone (multizone strip / Nanoleaf) — see the effect's
 * animation read-only, and fire the effect to test it. Per-Zone is always shown
 * but greyed with a warning until a multizone device is detected, so a mixed
 * bulb + strip setup can tune both.
 *
 * Phase 1: UI + persistence to gui_settings.effect_colors (wiring-ready). Phase 2
 * threads these colours through the three controllers. Reuses createColorPicker
 * (window) + the game-def system, and the hidden legacy effect-card SVG icons.
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
    rpm_meter:      { icon: '📊', name: 'RPM Meter',      desc: 'Zones fill as revs climb',         trigger: 'RPM Meter',      slots: [], rpm: true, def: {}, anim: { style: 'Zone fill by RPM', speed: 'Live telemetry', repeat: 'Continuous' } },
  };
  const ORDER = ['start_lights', 'lights_out', 'yellow_flag', 'blue_flag', 'red_flag', 'fastest_lap', 'chequered_flag', 'white_warning', 'crash', 'neutral', 'rpm_meter'];

  // ---- state ----
  let colors = {};
  let lightCount = 5;
  let lightNames = [];      // discovered bulb labels, for the per-light view + identify
  let zones = 16;
  let hasMultizone = false;
  let selectedKey = null;
  let selectedSet = new Set([0]);   // selected zone/light indices (multi-select)
  let anchorSection = 0;            // anchor for shift-click / drag ranges
  let dragging = false;
  function selList() { return [...selectedSet].sort(function (a, b) { return a - b; }); }
  function firstSel() { const l = selList(); return l.length ? l[0] : 0; }
  function setSel(indices) { selectedSet = new Set(indices.length ? indices : [0]); }
  function rangeSel(a, b) { const lo = Math.min(a, b), hi = Math.max(a, b), r = []; for (let i = lo; i <= hi; i++) r.push(i); return r; }
  function selLabel() {
    const sel = selList(), zoned = isZone(selectedKey);
    if (sel.length === 1) return zoned ? ('Zone ' + (sel[0] + 1)) : lightName(sel[0]);
    const lo = sel[0], hi = sel[sel.length - 1];
    if (zoned && hi - lo + 1 === sel.length) return 'Zones ' + (lo + 1) + '–' + (hi + 1) + '  (' + sel.length + ')';
    return sel.length + (zoned ? ' zones' : ' lights') + ' selected';
  }
  let gameEvents = null;
  let previewRaf = 0;
  function lightName(i) { return lightNames[i] || ('Light ' + (i + 1)); }

  function toast(msg) { if (window.showToast) window.showToast(msg); }
  function gdef() { return (window._GAME_DEFS && window._currentGame) ? window._GAME_DEFS[window._currentGame] : null; }
  function nameFor(key) { const g = gdef(); return (g && g.names && g.names[key]) || EFFECT_META[key].name; }
  function descFor(key) { const g = gdef(); return (g && g.descs && g.descs[key]) || EFFECT_META[key].desc; }

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
    if (['all', 'per_light', 'per_zone'].indexOf(c.mode) < 0) c.mode = 'all';
    if (!c.colors || typeof c.colors !== 'object') c.colors = Object.assign({}, EFFECT_META[key].def);
    // per_light is a {label: hex} map; per_zone is an ordered [hex] array.
    if (!c.per_light || typeof c.per_light !== 'object' || Array.isArray(c.per_light)) c.per_light = {};
    if (!Array.isArray(c.per_zone)) c.per_zone = [];
    return c;
  }
  // Stored mode, but per_zone collapses to Sync All when no multizone device is present.
  function effMode(key) { const m = cfg(key).mode; return (m === 'per_zone' && !hasMultizone) ? 'all' : m; }
  function isZone(key) { return effMode(key) === 'per_zone'; }
  function isPer(key) { const m = effMode(key); return m === 'per_light' || m === 'per_zone'; }
  // Zones reflect the device's real addressable-zone count (LIFX strips expose one
  // per LED segment). Cap only guards against an absurd DOM; bulbs cap lower.
  function sectionCount(key) { return isZone(key) ? Math.max(1, Math.min(96, zones)) : Math.max(1, Math.min(40, lightCount)); }
  // Per-zone colours are index-keyed (zones are ordered); per-light colours are
  // keyed by light LABEL so they map to the right bulb regardless of order.
  function secGet(key, i) { const c = cfg(key); return isZone(key) ? c.per_zone[i] : c.per_light[lightName(i)]; }
  function secSet(key, i, hex) { const c = cfg(key); if (isZone(key)) c.per_zone[i] = hex; else c.per_light[lightName(i)] = hex; }

  function primaryHex(key) {
    const meta = EFFECT_META[key];
    if (!meta.slots.length) return iconFor(key).color || '#f6c66a';
    return cfg(key).colors[meta.slots[0].key] || meta.def[meta.slots[0].key] || '#8b7cf6';
  }
  function iconColor(key) {
    if (EFFECT_META[key].rpm) return '#38bdf8';
    return EFFECT_META[key].slots.length ? primaryHex(key) : (iconFor(key).color || '#f6c66a');
  }
  function persist() { callApi('save_gui_settings', { effect_colors: colors }); }

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
    parkRpm();
    const meta = EFFECT_META[selectedKey];
    const c = cfg(selectedKey);
    const ic = iconFor(selectedKey);
    const eff = effMode(selectedKey);
    ed.innerHTML =
      '<div class="ec-ed-head">'
      + '<span class="ec-ed-icon" style="color:' + esc(iconColor(selectedKey)) + ';background:' + hexA(iconColor(selectedKey), 0.14) + '">' + ic.html + '</span>'
      + '<div class="ec-ed-title"><div class="ec-ed-name">' + esc(nameFor(selectedKey)) + '</div>'
      + '<div class="ec-ed-desc">' + esc(descFor(selectedKey)) + '</div></div>'
      + '<div class="ec-ed-actions">'
      + '<button class="btn btn-ghost btn-sm" id="ecTest"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" style="vertical-align:-2px;margin-right:3px;"><path d="M8 5v14l11-7z"/></svg>Test on lights</button>'
      + (meta.idleLink ? '' : '<button class="btn btn-ghost btn-sm" id="ecReset">Reset to Default</button>')
      + '</div></div>'
      + ((meta.idleLink || meta.rpm)
        ? (meta.idleLink ? '<div class="ec-idle-note"><b>Neutral</b> uses your <b>Idle Color</b> from Settings. <button class="ec-link" id="ecGotoIdle">Open Idle Color →</button></div>' : '')
        : ('<div class="ec-mode">'
          + '<button class="ec-seg' + (eff === 'all' ? ' active' : '') + '" data-mode="all">Sync All</button>'
          + '<button class="ec-seg' + (eff === 'per_light' ? ' active' : '') + '" data-mode="per_light">Customize Per Light</button>'
          + '<button class="ec-seg' + (eff === 'per_zone' ? ' active' : '') + (hasMultizone ? '' : ' ec-seg-dim') + '" data-mode="per_zone"'
          + (hasMultizone ? '' : ' title="No multizone device detected"') + '>Customize Per Zone</button>'
          + '</div>'
          + (hasMultizone ? '' : '<div class="ec-mode-warn"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;"><path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg> Connect a multizone device (LIFX strip / Nanoleaf) to customise per-zone.</div>')))
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
    if (test) test.addEventListener('click', () => {
      if (meta.rpm) { callApi('test_multizone'); toast('Testing RPM zones'); }
      else { callApi('trigger_effect', meta.trigger); toast('Triggered ' + nameFor(selectedKey)); }
    });
    const reset = document.getElementById('ecReset');
    if (reset) reset.addEventListener('click', () => {
      const cc = cfg(selectedKey); cc.colors = Object.assign({}, meta.def); cc.per_light = []; cc.per_zone = [];
      persist(); refreshListIcon(selectedKey); renderColors();
    });
    const goIdle = document.getElementById('ecGotoIdle');
    if (goIdle) goIdle.addEventListener('click', () => { const b = document.querySelector('.nav-btn[data-page="settings"]'); if (b) b.click(); });
    ed.querySelectorAll('.ec-seg[data-mode]').forEach(seg => seg.addEventListener('click', () => {
      if (seg.dataset.mode === 'per_zone' && !hasMultizone) { toast('Connect a multizone device to customise per-zone'); return; }
      cfg(selectedKey).mode = seg.dataset.mode; selectedSet = new Set([0]); anchorSection = 0; persist();
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
    parkRpm();                 // rescue the RPM block before we clear the container
    box.innerHTML = '';
    if (meta.rpm) {            // RPM Meter: show the relocated gradient editor
      if (rpmBlock) { box.appendChild(rpmBlock); } else box.insertAdjacentHTML('beforeend', '<p class="ec-empty">RPM gradient editor unavailable.</p>');
      startPreview(); return;
    }
    if (meta.idleLink) { box.innerHTML = '<p class="ec-empty">No colour to set — Neutral follows your Idle Color.</p>'; startPreview(); return; }

    if (effMode(selectedKey) === 'all') {
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
      const zoned = isZone(selectedKey), word = zoned ? 'Zone' : 'Light';
      box.insertAdjacentHTML('beforeend',
        '<div class="ec-colors-title">Per ' + word + ' Colours <span class="ec-sub">Click, shift-click or drag to select · then pick a colour</span></div>'
        + (zoned ? '<div class="ec-zonestrip" id="ecSections"></div>' : '<div class="ec-dots" id="ecSections"></div>')
        + '<div class="ec-perpick"><span class="ec-slot-label" id="ecPerLabel"></span><div id="ecPerMount"></div>'
        + '<button class="ec-link" id="ecSelAll">Select all</button>'
        + (zoned ? '' : '<button class="ec-link" id="ecOrder">Setup Light Order →</button>') + '</div>');
      // Drop any selection past the (possibly changed) count.
      const _n = sectionCount(selectedKey);
      setSel(selList().filter(function (i) { return i < _n; }));
      if (anchorSection >= _n) anchorSection = 0;
      renderSections();
      mountPerPicker();
      const selAll = document.getElementById('ecSelAll');
      if (selAll) selAll.addEventListener('click', function () { setSel(rangeSel(0, sectionCount(selectedKey) - 1)); anchorSection = 0; renderSections(); mountPerPicker(); });
      const order = document.getElementById('ecOrder');
      if (order) order.addEventListener('click', function () { toast('Light order lives in Light Assignment (Lights page)'); });

      const grid = document.getElementById('ecSections');
      const idxAt = e => { const s = e.target.closest('[data-i]'); return s ? +s.dataset.i : null; };
      grid.addEventListener('pointerdown', e => {
        const i = idxAt(e); if (i == null) return;
        e.preventDefault();
        if (e.shiftKey) { setSel(rangeSel(anchorSection, i)); }
        else if (e.ctrlKey || e.metaKey) { if (selectedSet.has(i) && selectedSet.size > 1) selectedSet.delete(i); else selectedSet.add(i); anchorSection = i; }
        else {
          setSel([i]); anchorSection = i; dragging = true;
          if (!zoned) { callApi('identify_light', lightName(i)); toast('Identifying ' + lightName(i)); }
        }
        renderSections(); mountPerPicker();
      });
      grid.addEventListener('pointerover', e => {
        if (!dragging) return;
        const i = idxAt(e); if (i == null) return;
        setSel(rangeSel(anchorSection, i)); renderSections(); mountPerPicker();
      });
    }
    startPreview();
  }

  function renderSections() {
    const el = document.getElementById('ecSections'); if (!el) return;
    const n = sectionCount(selectedKey), zoned = isZone(selectedKey);
    let html = '';
    for (let i = 0; i < n; i++) {
      const col = secGet(selectedKey, i) || primaryHex(selectedKey);
      const sel = selectedSet.has(i) ? ' sel' : '';
      if (zoned) html += '<button class="ec-zone' + sel + '" data-i="' + i + '" style="--c:' + esc(col) + '"><span class="ec-zone-n">' + (i + 1) + '</span></button>';
      else html += '<button class="ec-dot' + sel + '" data-i="' + i + '" style="--c:' + esc(col) + '" title="' + esc(lightName(i)) + '"></button>';
    }
    el.innerHTML = html;
  }
  function mountPerPicker() {
    const m = document.getElementById('ecPerMount'); if (!m) return; m.innerHTML = '';
    const lbl = document.getElementById('ecPerLabel'); if (lbl) lbl.textContent = selLabel();
    const sel = selList();
    window.createColorPicker({
      mount: m, align: 'left', value: secGet(selectedKey, firstSel()) || primaryHex(selectedKey),
      onInput: hex => { sel.forEach(i => { secSet(selectedKey, i, hex); const d = document.querySelector('#ecSections [data-i="' + i + '"]'); if (d) d.style.setProperty('--c', hex); }); },
      onChange: hex => { sel.forEach(i => secSet(selectedKey, i, hex)); persist(); },
    });
  }

  // ---- animated preview (segment strip) ----
  function startPreview() {
    cancelAnimationFrame(previewRaf);
    const stage = document.getElementById('ecPrevStrip'); if (!stage) return;
    const meta = EFFECT_META[selectedKey], c = cfg(selectedKey);
    const n = meta.idleLink ? 6 : Math.min(16, sectionCount(selectedKey));
    stage.innerHTML = '';
    const cells = [];
    for (let i = 0; i < n; i++) { const d = document.createElement('div'); d.className = 'ec-cell'; stage.appendChild(d); cells.push(d); }
    const OFF = '#151b2b';
    function colAt(i) {
      if (meta.idleLink || meta.rpm || !meta.slots.length) return primaryHex(selectedKey);
      if (isPer(selectedKey)) return secGet(selectedKey, i) || primaryHex(selectedKey);
      const s = meta.slots[0];
      return c.colors[s.key] || meta.def[s.key] || primaryHex(selectedKey);
    }
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
      } else {
        cells.forEach((cell, i) => { const col = colAt(i); cell.style.background = col; cell.style.boxShadow = '0 0 8px ' + col; cell.style.opacity = '0.92'; });
      }
      previewRaf = requestAnimationFrame(frame);
    }
    previewRaf = requestAnimationFrame(frame);
  }

  function selectEffect(key) {
    selectedKey = key; selectedSet = new Set([0]); anchorSection = 0; dragging = false;
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
  document.addEventListener('pointerup', () => { dragging = false; });

  // Relocate the RPM-meter gradient editor here (out of Settings). Parked in a
  // hidden host so re-rendering the editor never destroys its wired DOM; moved
  // into view only while "RPM Meter" is selected.
  const rpmBlock = document.querySelector('.rpm-grad-row');
  let rpmHost = null;
  if (rpmBlock) { rpmHost = document.createElement('div'); rpmHost.style.display = 'none'; root.appendChild(rpmHost); rpmHost.appendChild(rpmBlock); }
  function parkRpm() { if (rpmBlock && rpmHost && rpmBlock.parentNode !== rpmHost) rpmHost.appendChild(rpmBlock); }

  // Re-fetch capabilities (device detection is live) whenever the Effects page opens,
  // so a strip discovered after mount enables Per-Zone without a restart.
  function refreshCaps() {
    return Promise.resolve(callApi('get_workshop_capabilities')).then(caps => {
      if (caps) {
        hasMultizone = !!caps.has_multizone;
        if (caps.zones) zones = Math.max(1, Math.min(96, caps.zones));
        if (caps.light_count) lightCount = Math.max(1, Math.min(20, caps.light_count));
      }
    }).catch(() => {}).then(() => {
      // Per-light should list only the SELECTED lights (Light Assignment), not all
      // discovered ones. Fall back to full discovery only if the selection is empty.
      const sel = (typeof window._getSelectedLightLabels === 'function') ? (window._getSelectedLightLabels() || []) : [];
      if (sel.length) { lightNames = sel; lightCount = Math.max(1, Math.min(20, sel.length)); return; }
      return Promise.resolve(callApi('get_discovered_lights')).then(list => {
        if (Array.isArray(list) && list.length) {
          lightNames = list.map(d => (d && d.label) || '');
          lightCount = Math.max(1, Math.min(20, list.length));
        }
      }).catch(() => {});
    }).catch(() => {});
  }
  const effNav = document.querySelector('.nav-btn[data-page="effects"]');
  if (effNav) effNav.addEventListener('click', () => { refreshCaps().then(() => { if (selectedKey) renderEditor(); }); });

  window._effectCustomRefresh = function (game) {
    const g = window._GAME_DEFS && window._GAME_DEFS[game];
    gameEvents = g && Array.isArray(g.events) ? new Set(g.events.filter(k => EFFECT_META[k])) : null;
    render();
  };

  Promise.resolve(callApi('get_gui_settings')).then(s => {
    if (s && s.effect_colors && typeof s.effect_colors === 'object') colors = s.effect_colors;
    // Persisted hint keeps Per-Zone available once a strip has ever been seen.
    if (s && s.multizone_seen) { hasMultizone = true; if (s.multizone_zones) zones = Math.max(1, Math.min(96, s.multizone_zones)); }
  }).catch(() => {}).then(refreshCaps).then(render).catch(render);
})();
