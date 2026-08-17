/* Community Workshop — app module (ported from docs/mockups/workshop-community.html).
 *
 * Data layer: in the desktop app the webview loads via file:// and CANNOT fetch the
 * API directly, so reads/writes go through the pywebview bridge (callApi → workshop_*).
 * In a plain browser (dev preview) there's no bridge, so we fall back to a direct fetch
 * — handy for verifying markup/styles/reads against the live API.
 *
 * This slice ports the DISCOVER reads (list + detail + arcs + filters/sort/search).
 * Write actions, the preview loop, personal tabs and the auth UI land in the next pass.
 */
(function () {
  'use strict';
  const root = document.getElementById('workshopRoot');
  if (!root) return;

  const API_BASE = 'https://gridglow.titanstowers.net';
  const BRIDGE = !!(window.pywebview && window.pywebview.api);
  function callApi(fn) {
    const args = Array.prototype.slice.call(arguments, 1);
    if (BRIDGE && window.pywebview.api[fn]) return window.pywebview.api[fn].apply(null, args);
    return Promise.resolve(null);
  }
  // reads — bridge in the app, direct fetch in browser preview
  async function wsList() {
    if (BRIDGE) return callApi('workshop_list');
    try { return await fetch(API_BASE + '/api/workshop/presets').then(r => r.json()); }
    catch (e) { return { error: 'request_failed' }; }
  }
  async function wsGet(id) {
    if (BRIDGE) return callApi('workshop_get', id);
    try { return await fetch(API_BASE + '/api/workshop/presets/' + id).then(r => r.json()); }
    catch (e) { return { error: 'request_failed' }; }
  }

  // ---- small helpers (from the mockup) ----
  const DEV_META = { hue:{label:'Philips Hue',cls:'dev-hue'}, nanoleaf:{label:'Nanoleaf',cls:'dev-nano'}, lifx:{label:'LIFX',cls:'dev-lifx'} };
  const USER_DEVICES = ['hue'];                                  // TODO: from the app's real device set
  const MZ_EFFECTS = ['rpm_meter','start_lights','sector_status'];
  const fmt = n => { n = +n || 0; return n >= 1000 ? (n/1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/,'') + 'k' : String(n); };
  const daysSince = ms => Math.max(0, Math.floor((Date.now() - ms) / 86400000));
  const shortDate = ms => { try { return new Date(ms).toLocaleDateString(undefined,{month:'short',day:'numeric'}); } catch(e){ return '—'; } };
  const logoFor = slug => 'logos/' + slug + '.png';
  const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const hex2rgb = h => { h = h.replace('#',''); return [0,2,4].map(k => parseInt(h.substr(k,2),16)); };

  // rev-gauge arc from a preset's swatch/rpm_gradient
  let _arc = 0;
  function renderArc(el, cols) {
    if (!el || !cols || !cols.length) return;
    const gid = 'ws_grad_' + (_arc++);
    const stops = cols.map((c,i) => '<stop offset="' + (i/(cols.length-1)*100).toFixed(1) + '%" stop-color="' + c + '"/>').join('');
    const m = hex2rgb(cols[Math.floor(cols.length/2)]);
    el.innerHTML =
      '<svg viewBox="0 0 164 36" preserveAspectRatio="none" style="filter:drop-shadow(0 1px 6px rgba('+m[0]+','+m[1]+','+m[2]+',.5))">'
      + '<defs><linearGradient id="'+gid+'" gradientUnits="userSpaceOnUse" x1="10" y1="0" x2="154" y2="0">'+stops+'</linearGradient></defs>'
      + '<path d="M10 27 Q82 5 154 27" fill="none" stroke="url(#'+gid+')" stroke-width="11" stroke-linecap="round" stroke-dasharray="1.3 4.6"/>'
      + '</svg>';
  }

  // ---- markup (Discover) injected into #page-workshop ----
  const MARKUP =
    '<div class="main">'
    + '<div class="hd"><div><h1>Community Workshop</h1>'
    +   '<p class="sub">Discover, share, and download community lighting presets for your favourite games.</p></div>'
    +   '<span class="grow"></span>'
    +   '<div class="search"><i class="ti ti-search"></i>'
    +     '<input id="wsSearch" class="search-input" type="text" placeholder="Search presets, games, creators…" autocomplete="off" spellcheck="false">'
    +     '<button id="wsSearchClear" class="search-clear" type="button" title="Clear"><i class="ti ti-x"></i></button></div>'
    + '</div>'
    + '<div class="tabs">'
    +   '<div class="tab active" data-tab="discover">Discover</div>'
    +   '<div class="tab" data-tab="uploads">My Uploads</div>'
    +   '<div class="tab" data-tab="downloads">My Downloads</div>'
    +   '<div class="tab" data-tab="liked">Liked</div>'
    + '</div>'
    + '<div class="tab-panel active" data-panel="discover">'
    +   '<div class="filters">'
    +     '<div class="fdrop" data-key="game"><i class="ti ti-device-gamepad-2"></i> <span class="fdrop-lbl">All Games</span> <i class="ti ti-chevron-down chev"></i></div>'
    +     '<div class="fdrop" data-key="device"><i class="ti ti-bulb"></i> <span class="fdrop-lbl">All Devices</span> <i class="ti ti-chevron-down chev"></i></div>'
    +     '<div class="fdrop hot" data-key="sort"><i class="ti ti-flame"></i> <span class="fdrop-lbl">Popular</span> <i class="ti ti-chevron-down chev"></i></div>'
    +     '<span class="grow"></span>'
    +     '<div class="fdrop" id="wsReset"><i class="ti ti-adjustments-horizontal"></i> Reset</div>'
    +   '</div>'
    +   '<div class="split">'
    +     '<div class="list" id="wsList">'
    +       '<div class="list-state" id="wsLoading">Loading presets…</div>'
    +       '<div class="list-state" id="wsError" style="display:none;">Can\'t reach the Workshop API.</div>'
    +       '<div class="list-empty" id="wsEmpty">No presets yet — be the first to upload one.</div>'
    +     '</div>'
    +     '<div class="detail"><div class="d-scroll">'
    +       '<div class="d-hero" id="wsHero"><img class="d-badge" id="wsBadge" src="" alt="" style="height:26px;width:auto;filter:drop-shadow(0 2px 6px rgba(0,0,0,.6));"></div>'
    +       '<div class="d-tit" id="wsTit"></div><div class="d-by" id="wsBy"></div>'
    +       '<div class="d-statgrid">'
    +         '<div class="d-stat"><div class="k">Downloads</div><div class="v" id="wsDl">–</div></div>'
    +         '<div class="d-stat"><div class="k">Likes</div><div class="v" id="wsLikes">–</div></div>'
    +         '<div class="d-stat"><div class="k">Rating</div><div class="v star" id="wsRating">–</div></div>'
    +         '<div class="d-stat"><div class="k">Updated</div><div class="v" id="wsUpdated">–</div></div>'
    +       '</div>'
    +       '<div class="d-lbl">Compatible Games</div><div class="chips" id="wsGames"></div>'
    +       '<div class="d-lbl">Recommended Devices</div><div class="chips" id="wsDevices"></div>'
    +       '<div class="d-lbl">Description</div><div class="d-desc" id="wsDesc"></div>'
    +       '<div class="d-lbl">Tags</div><div class="chips d-tags" id="wsTags"></div>'
    +       '<div class="d-lbl">Share Preset</div><div class="d-share"><div class="lnk" id="wsShareLink">gridglow.app/w/…</div>'
    +         '<div class="d-copy"><i class="ti ti-copy" style="font-size:15px;"></i> Copy</div></div>'
    +     '</div></div>'
    +   '</div>'
    + '</div>'
    + '<div class="tab-panel" data-panel="uploads"><div class="list-state" style="margin-top:40px;">Sign in to see your uploads. (Write path — next slice.)</div></div>'
    + '<div class="tab-panel" data-panel="downloads"><div class="list-state" style="margin-top:40px;">Sign in to see your downloads. (Write path — next slice.)</div></div>'
    + '<div class="tab-panel" data-panel="liked"><div class="list-state" style="margin-top:40px;">Sign in to see liked presets. (Write path — next slice.)</div></div>'
    + '</div>';

  // ---- render ----
  let workshopLoaded = false;
  const fstate = { sort: 'pop', game: 'all', device: 'all', q: '' };
  let listEl, emptyEl, loadingEl, errorEl;

  function renderCard(p) {
    const card = document.createElement('div');
    card.className = 'pc';
    card.dataset.id = p.id;
    card.dataset.name = p.title || '';
    card.dataset.game = p.game_name || p.game || '';
    card.dataset.downloads = p.downloads || 0;
    card.dataset.likes = p.likes || 0;
    card.dataset.rating = (p.rating == null ? 0 : p.rating);
    card.dataset.days = daysSince(p.created_at || Date.now());
    card.dataset.devices = (p.devices || []).join(',');
    card.dataset.tags = (p.tags || []).join(',');
    const ratingTxt = p.rating == null ? '—' : (+p.rating).toFixed(1);
    card.innerHTML =
      '<div class="thumb"><img class="thumb-logo" src="' + logoFor(p.game) + '" alt="' + esc(p.game_name||'') + '"></div>'
      + '<div class="pc-info"><div class="pc-titrow"><span class="pc-tit">' + esc(p.title) + '</span></div>'
      + '<p class="pc-game">' + esc(p.game_name||p.game) + ' · <span class="pc-by">by ' + esc(p.author_name||'unknown') + '</span></p>'
      + '<p class="pc-desc">' + (p.tags||[]).slice(0,4).map(t => '#'+esc(t)).join(' ') + '</p></div>'
      + '<div class="zones"></div>'
      + '<div class="pc-stats"><div class="s"><i class="ti ti-download"></i> ' + fmt(p.downloads) + '</div>'
      + '<div class="s"><i class="ti ti-heart"></i> ' + fmt(p.likes) + '</div>'
      + '<div class="s"><i class="ti ti-star-filled star"></i> ' + ratingTxt + '</div></div>'
      + '<div class="pc-act"><button class="dl"><i class="ti ti-download"></i> Download</button>'
      + '<button class="heart" style="margin:0 auto;"><i class="ti ti-heart hx"></i></button></div>';
    renderArc(card.querySelector('.zones'), (p.swatch && p.swatch.length) ? p.swatch : ['#8b7cf6']);
    return card;
  }

  function applyFilters() {
    const cards = [].slice.call(listEl.querySelectorAll('.pc'));
    const q = fstate.q.trim().toLowerCase();
    cards.forEach(c => {
      const okGame = fstate.game === 'all' || c.dataset.game === fstate.game;
      const okDev = fstate.device === 'all' || (c.dataset.devices||'').split(',').indexOf(fstate.device) >= 0;
      const hay = (c.dataset.name + ' ' + c.dataset.game + ' ' + (c.dataset.tags||'') + ' ' + (c.querySelector('.pc-by') ? c.querySelector('.pc-by').textContent : '')).toLowerCase();
      c.style.display = (okGame && okDev && (!q || hay.indexOf(q) >= 0)) ? '' : 'none';
    });
    const cmp = {
      pop:   (a,b) => b.dataset.downloads - a.dataset.downloads,
      likes: (a,b) => b.dataset.likes - a.dataset.likes,
      rating:(a,b) => b.dataset.rating - a.dataset.rating,
      new:   (a,b) => a.dataset.days - b.dataset.days,
      az:    (a,b) => a.dataset.name.localeCompare(b.dataset.name),
    }[fstate.sort];
    const visible = cards.filter(c => c.style.display !== 'none').sort(cmp);
    visible.forEach(c => listEl.insertBefore(c, emptyEl));
    emptyEl.classList.toggle('show', workshopLoaded && visible.length === 0);
  }

  function compatFor(theme) {
    const evs = (theme && theme.enabled_events) || [];
    const hasStrip = USER_DEVICES.some(d => d === 'lifx' || d === 'nanoleaf');
    const mz = evs.filter(e => MZ_EFFECTS.indexOf(e) >= 0).length;
    return (mz === 0 || hasStrip) ? 'Fully compatible with your setup'
      : 'Partially compatible — ' + mz + ' effect' + (mz>1?'s':'') + ' need a multizone strip you don\'t have.';
  }

  function renderDetail(p) {
    const theme = p.theme || {};
    const grad = (theme.rpm_gradient && theme.rpm_gradient.length) ? theme.rpm_gradient : (p.swatch || ['#7a0f1a']);
    document.getElementById('wsHero').style.background =
      'radial-gradient(120% 140% at 70% 10%, ' + (grad[grad.length-1]||'#7a0f1a') + ' 0%, rgba(10,7,16,.55) 55%, #0a0710 100%)';
    const badge = document.getElementById('wsBadge'); badge.src = logoFor(p.game); badge.alt = p.game_name || '';
    document.getElementById('wsTit').textContent = p.title || '';
    document.getElementById('wsBy').textContent = 'by ' + (p.author_name || 'unknown');
    document.getElementById('wsDl').textContent = fmt(p.downloads);
    document.getElementById('wsLikes').textContent = fmt(p.likes);
    document.getElementById('wsRating').textContent = p.rating == null ? '—'
      : (+p.rating).toFixed(1) + (p.rating_count ? ' (' + p.rating_count + ')' : '');
    document.getElementById('wsUpdated').textContent = shortDate(p.updated_at || p.created_at);
    document.getElementById('wsGames').innerHTML = '<span class="chip">' + esc(p.game_name||p.game) + '</span>';
    document.getElementById('wsDevices').innerHTML = (p.devices||[]).map(d => {
      const m = DEV_META[d]; if (!m) return '';
      const owned = USER_DEVICES.indexOf(d) >= 0;
      return '<span class="chip ' + m.cls + (owned ? '' : ' missing') + '"><span class="d"></span> ' + m.label + (owned ? ' <i class="ti ti-check own"></i>' : '') + '</span>';
    }).join('');
    document.getElementById('wsDesc').textContent = (p.description || '') + '  —  ' + compatFor(theme);
    document.getElementById('wsTags').innerHTML = (p.tags||[]).map(t => '<button class="d-tag" type="button">' + esc(t) + '</button>').join('');
    document.getElementById('wsShareLink').textContent = 'gridglow.app/w/' + p.id;
  }

  async function selectPreset(id, cardEl) {
    listEl.querySelectorAll('.pc.sel').forEach(c => c.classList.remove('sel'));
    if (cardEl) cardEl.classList.add('sel');
    const res = await wsGet(id);
    if (res && !res.error) renderDetail(res);
  }

  function setState(s) {
    loadingEl.style.display = s === 'loading' ? '' : 'none';
    errorEl.style.display = s === 'error' ? '' : 'none';
  }

  async function loadWorkshop() {
    setState('loading');
    emptyEl.classList.remove('show');
    const res = await wsList();
    if (!res || res.error) { workshopLoaded = false; listEl.querySelectorAll('.pc').forEach(c => c.remove()); setState('error'); return; }
    listEl.querySelectorAll('.pc').forEach(c => c.remove());
    (res.presets || []).forEach(p => listEl.insertBefore(renderCard(p), emptyEl));
    workshopLoaded = true; setState('ready');
    applyFilters();
    const first = listEl.querySelector('.pc');
    if (first) selectPreset(first.dataset.id, first);
  }

  // ---- wiring (runs once, after markup is injected) ----
  function wire() {
    listEl = document.getElementById('wsList');
    emptyEl = document.getElementById('wsEmpty');
    loadingEl = document.getElementById('wsLoading');
    errorEl = document.getElementById('wsError');

    // search
    const si = document.getElementById('wsSearch');
    const box = si.closest('.search');
    function onSearch() { fstate.q = si.value; box.classList.toggle('has-q', si.value.trim().length > 0); applyFilters(); }
    si.addEventListener('input', onSearch);
    document.getElementById('wsSearchClear').addEventListener('click', () => { si.value = ''; onSearch(); si.focus(); });

    // sort/game/device dropdowns
    const OPTS = {
      sort: [['pop','Popular'],['likes','Most liked'],['rating','Highest rated'],['new','Newest'],['az','Name A–Z']],
      game: [['all','All Games'],['F1 25 – F1 21','F1 25 – F1 21'],['Assetto Corsa Competizione','ACC'],['DiRT Rally 2.0','DiRT Rally 2.0'],['EA SPORTS WRC','EA SPORTS WRC']],
      device: [['all','All Devices'],['hue','Philips Hue'],['nanoleaf','Nanoleaf'],['lifx','LIFX']],
    };
    root.querySelectorAll('.fdrop[data-key]').forEach(drop => {
      const key = drop.dataset.key, lbl = drop.querySelector('.fdrop-lbl');
      const menu = document.createElement('div'); menu.className = 'fmenu';
      menu.innerHTML = OPTS[key].map(o => '<div class="fmenu-item" data-v="' + esc(o[0]) + '">' + esc(o[1]) + '<i class="ti ti-check ck"></i></div>').join('');
      drop.appendChild(menu);
      menu.addEventListener('click', e => {
        const it = e.target.closest('.fmenu-item'); if (!it) return; e.stopPropagation();
        fstate[key] = it.dataset.v; lbl.textContent = it.textContent.trim();
        menu.querySelectorAll('.fmenu-item').forEach(x => x.classList.remove('active')); it.classList.add('active');
        drop.classList.remove('open'); applyFilters();
      });
      drop.addEventListener('click', e => {
        if (e.target.closest('.fmenu')) return;
        const open = drop.classList.contains('open');
        root.querySelectorAll('.fdrop.open').forEach(d => d.classList.remove('open'));
        if (!open) drop.classList.add('open');
      });
    });
    document.addEventListener('click', e => { if (!e.target.closest('.fdrop')) root.querySelectorAll('.fdrop.open').forEach(d => d.classList.remove('open')); });
    document.getElementById('wsReset').addEventListener('click', () => {
      Object.assign(fstate, { sort:'pop', game:'all', device:'all', q:'' });
      si.value = ''; box.classList.remove('has-q');
      root.querySelectorAll('.fdrop[data-key]').forEach(d => { d.querySelector('.fdrop-lbl').textContent = OPTS[d.dataset.key][0][1]; });
      applyFilters();
    });

    // tab switching (personal tabs are placeholders this slice)
    root.querySelectorAll('.tab[data-tab]').forEach(tab => tab.addEventListener('click', () => {
      root.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      root.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === tab.dataset.tab));
    }));

    // card select / detail-tag search (delegation)
    listEl.addEventListener('click', e => {
      const card = e.target.closest('.pc');
      if (card) selectPreset(card.dataset.id, card);
    });
    document.getElementById('wsTags').addEventListener('click', e => {
      const t = e.target.closest('.d-tag'); if (!t) return;
      si.value = t.textContent.trim(); onSearch();
    });
  }

  // ---- mount lazily on first tab open ----
  let mounted = false;
  function mount() {
    if (mounted) return; mounted = true;
    root.innerHTML = MARKUP;
    wire();
    loadWorkshop();
  }
  const navBtn = document.querySelector('.nav-btn[data-page="workshop"]');
  if (navBtn) navBtn.addEventListener('click', mount);
})();
