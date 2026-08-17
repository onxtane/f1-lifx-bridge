/* Community Workshop — app module (ported from docs/mockups/workshop-community.html).
 *
 * Data layer: the desktop webview loads via file:// and can't fetch the API directly,
 * so reads/writes route through the pywebview bridge (callApi → workshop_*). In a plain
 * browser (dev preview) there's no bridge, so it falls back to a direct fetch (+ Bearer
 * from the Supabase session) — lets us verify markup/styles/reads in preview.
 *
 * Auth runs in the webview via the vendored supabase-js; on auth change the access
 * token is pushed to Python (set_workshop_token) so the bridge's write calls send it.
 */
(function () {
  'use strict';
  const root = document.getElementById('workshopRoot');
  if (!root) return;

  const API_BASE      = 'https://gridglow.titanstowers.net';
  const SUPABASE_URL  = 'https://upvbmoseimgiathprtsj.supabase.co';
  const SUPABASE_ANON = 'sb_publishable_bChP2PoDJXHNSHysW63DNA_CK7M1RKC';
  // pywebview injects window.pywebview.api ASYNCHRONOUSLY (after load), so this must be
  // checked at CALL time — not captured once, or the app wrongly falls back to fetch.
  function hasBridge() { return !!(window.pywebview && window.pywebview.api); }

  function callApi(fn) {
    const args = Array.prototype.slice.call(arguments, 1);
    if (hasBridge() && window.pywebview.api[fn]) return window.pywebview.api[fn].apply(null, args);
    return Promise.resolve(null);
  }

  // ---- auth (Supabase JS in the webview) ----
  let sb = null, session = null;
  if (window.supabase && window.supabase.createClient) {
    sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON);
  }
  function authHeader() { return session ? { Authorization: 'Bearer ' + session.access_token } : {}; }

  // ---- data layer ----
  function jok(r) { return r.json().catch(() => ({ error: 'bad_json' })); }
  async function wsList() {
    if (hasBridge()) return callApi('workshop_list');
    try { return await fetch(API_BASE + '/api/workshop/presets').then(jok); } catch (e) { return { error: 'x' }; }
  }
  async function wsGet(id) {
    if (hasBridge()) return callApi('workshop_get', id);
    try { return await fetch(API_BASE + '/api/workshop/presets/' + id).then(jok); } catch (e) { return { error: 'x' }; }
  }
  function norm(res) { if (!res) return { ok:false, status:0, data:null }; if (res.error) return { ok:false, status:res.status||0, data:res }; return { ok:true, status:200, data:res }; }
  async function wsWrite(path, method, body) {
    if (hasBridge()) {
      try {
        let m;
        if ((m = path.match(/\/presets\/([^/?]+)\/like$/)))     return norm(await callApi('workshop_like', m[1]));
        if ((m = path.match(/\/presets\/([^/?]+)\/download$/))) return norm(await callApi('workshop_download', m[1]));
        if ((m = path.match(/\/presets\/([^/?]+)\/rate$/)))     return norm(await callApi('workshop_rate', m[1], body.stars));
        if ((m = path.match(/[?&](mine|liked|downloaded)=1/)))  return norm(await callApi('workshop_personal', m[1]));
        if ((m = path.match(/\/presets\/([^/?]+)$/)) && method === 'DELETE') return norm(await callApi('workshop_delete', m[1]));
        if (path.endsWith('/presets') && method === 'POST')
          return norm(await callApi('workshop_upload', { title: body.title, description: body.description, game: body.game, visibility: body.visibility, tags: body.tags, devices: body.devices }));
        return { ok:false, status:0, data:{ error:'no_route' } };
      } catch (e) { return { ok:false, status:0, data:{ error:'bridge_error', detail:String(e) } }; }
    }
    try {
      const r = await fetch(API_BASE + path, { method, headers: Object.assign({ 'Content-Type':'application/json' }, authHeader()), body: body ? JSON.stringify(body) : undefined });
      let data = null; try { data = await r.json(); } catch (e) {}
      if (r.status === 429) toast('Rate limited — try again shortly', 'err');
      else if (r.status === 401) toast('Please sign in again', 'err');
      return { ok:r.ok, status:r.status, data };
    } catch (e) { toast('Network error', 'err'); return { ok:false, status:0, data:null }; }
  }

  // ---- helpers ----
  const DEV_META = { hue:{label:'Philips Hue',cls:'dev-hue'}, nanoleaf:{label:'Nanoleaf',cls:'dev-nano'}, lifx:{label:'LIFX',cls:'dev-lifx'} };
  const USER_DEVICES = ['hue'];
  const MZ_EFFECTS = ['rpm_meter','start_lights','sector_status'];
  const fmt = n => { n = +n || 0; return n >= 1000 ? (n/1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/,'') + 'k' : String(n); };
  const daysSince = ms => Math.max(0, Math.floor((Date.now() - ms) / 86400000));
  const shortDate = ms => { try { return new Date(ms).toLocaleDateString(undefined,{month:'short',day:'numeric'}); } catch(e){ return '—'; } };
  const logoFor = slug => 'logos/' + slug + '.png';
  const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const hex2rgb = h => { h = h.replace('#',''); return [0,2,4].map(k => parseInt(h.substr(k,2),16)); };
  const lerp = (a,b,t) => a.map((v,i) => Math.round(v + (b[i]-v)*t));
  function sampleGradient(stops, t) {
    if (!stops || !stops.length) return '#8b7cf6';
    if (stops.length === 1) return stops[0];
    const s = stops.map(hex2rgb), seg = Math.max(0,Math.min(1,t))*(s.length-1), k = Math.min(s.length-2, Math.floor(seg)), c = lerp(s[k], s[k+1], seg-k);
    return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
  }
  let _arc = 0;
  function renderArc(el, cols) {
    if (!el || !cols || !cols.length) return;
    const gid = 'ws_grad_' + (_arc++), m = hex2rgb(cols[Math.floor(cols.length/2)]);
    const stops = cols.map((c,i) => '<stop offset="' + (i/(cols.length-1)*100).toFixed(1) + '%" stop-color="' + c + '"/>').join('');
    el.innerHTML = '<svg viewBox="0 0 164 36" preserveAspectRatio="none" style="filter:drop-shadow(0 1px 6px rgba('+m[0]+','+m[1]+','+m[2]+',.5))">'
      + '<defs><linearGradient id="'+gid+'" gradientUnits="userSpaceOnUse" x1="10" y1="0" x2="154" y2="0">'+stops+'</linearGradient></defs>'
      + '<path d="M10 27 Q82 5 154 27" fill="none" stroke="url(#'+gid+')" stroke-width="11" stroke-linecap="round" stroke-dasharray="1.3 4.6"/></svg>';
  }
  let _tt = null;
  function toast(msg, kind) { const t = document.getElementById('wsToast'); if (!t) return; t.textContent = msg; t.className = 'ws-toast show' + (kind ? ' '+kind : ''); clearTimeout(_tt); _tt = setTimeout(() => t.classList.remove('show'), 2800); }
  function requireLogin() { if (!session) { toast('Sign in to do that'); openLogin(); return false; } return true; }

  // ---- markup ----
  const MARKUP =
    '<div class="main">'
    + '<div class="hd"><div><h1>Community Workshop</h1>'
    +   '<p class="sub">Discover, share, and download community lighting presets for your favourite games.</p></div>'
    +   '<span class="grow"></span>'
    +   '<div class="search"><i class="ti ti-search"></i>'
    +     '<input id="wsSearch" class="search-input" type="text" placeholder="Search presets, games, creators…" autocomplete="off" spellcheck="false">'
    +     '<button id="wsSearchClear" class="search-clear" type="button" title="Clear"><i class="ti ti-x"></i></button></div>'
    +   '<button class="btn btn-primary" id="wsUploadBtn" style="margin-top:2px;"><i class="ti ti-upload"></i> Upload Preset</button>'
    +   '<div class="auth-area" id="wsAuth" style="margin-top:2px;"></div>'
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
    +     '<span class="grow"></span><div class="fdrop" id="wsReset"><i class="ti ti-adjustments-horizontal"></i> Reset</div>'
    +   '</div>'
    +   '<div class="split">'
    +     '<div class="list" id="wsList"><div class="list-state" id="wsLoading">Loading presets…</div>'
    +       '<div class="list-state" id="wsErr" style="display:none;">Can\'t reach the Workshop API.</div>'
    +       '<div class="list-empty" id="wsEmpty">No presets yet — be the first to upload one.</div></div>'
    +     '<div class="detail"><div class="d-scroll">'
    +       '<div class="d-hero" id="wsHero"><img class="d-badge" id="wsBadge" src="" alt="" style="height:26px;width:auto;filter:drop-shadow(0 2px 6px rgba(0,0,0,.6));"></div>'
    +       '<div class="d-tit" id="wsTit"></div><div class="d-by" id="wsBy"></div>'
    +       '<div class="d-actions"><button class="btn btn-primary" id="wsDownload"><i class="ti ti-download"></i> Download</button>'
    +         '<div class="d-icons"><div class="d-iconbtn like" id="wsLike"><svg class="hx" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="'+HEART_PATH()+'"/></svg> <span class="lc" id="wsLikeCount">0</span></div>'
    +         '<div class="d-iconbtn" id="wsShare"><i class="ti ti-share-3"></i> Share</div></div></div>'
    +       '<div class="d-compat warn" id="wsCompat"><span class="d-compat-dot"></span><div id="wsCompatText"></div></div>'
    +       '<div class="d-statgrid"><div class="d-stat"><div class="k">Downloads</div><div class="v" id="wsDl">–</div></div>'
    +         '<div class="d-stat"><div class="k">Likes</div><div class="v" id="wsLikes">–</div></div>'
    +         '<div class="d-stat"><div class="k">Rating</div><div class="v star" id="wsRating">–</div></div>'
    +         '<div class="d-stat"><div class="k">Updated</div><div class="v" id="wsUpdated">–</div></div></div>'
    +       '<div class="d-lbl">Compatible Games</div><div class="chips" id="wsGames"></div>'
    +       '<div class="d-lbl">Recommended Devices</div><div class="chips" id="wsDevices"></div>'
    +       '<div class="d-lbl">Description</div><div class="d-desc" id="wsDesc"></div>'
    +       '<div class="d-lbl">Your rating</div><div class="d-rate" id="wsRate"></div>'
    +       '<div class="d-lbl" style="display:flex;align-items:center;justify-content:space-between;"><span>Preview</span>'
    +         '<button class="lp-btn" id="wsPlay" type="button"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg><span id="wsPlayLbl">Play</span></button></div>'
    +       '<div class="d-preview"><div class="lp-flag" id="wsFlag"></div><div class="lp-strip" id="wsStrip"></div><div class="lp-caption" id="wsCap">Idle · press play</div></div>'
    +       '<div class="d-lbl">Tags</div><div class="chips d-tags" id="wsTags"></div>'
    +       '<div class="d-lbl">Share Preset</div><div class="d-share"><div class="lnk" id="wsShareLink">gridglow.app/w/…</div>'
    +         '<div class="d-copy"><i class="ti ti-copy" style="font-size:15px;"></i> Copy</div></div>'
    +     '</div></div>'
    +   '</div>'
    + '</div>'
    + '<div class="tab-panel" data-panel="uploads"><div class="panel-head"><div class="ph-title">Your published presets</div></div><div class="mgrid" id="wsUploadsGrid"></div><div class="panel-empty" id="wsUploadsEmpty"><h4>No uploads yet</h4><p>Publish a preset from your current setup.</p></div></div>'
    + '<div class="tab-panel" data-panel="downloads"><div class="panel-head"><div class="ph-title">Downloaded presets</div></div><div class="mgrid" id="wsDownloadsGrid"></div><div class="panel-empty" id="wsDownloadsEmpty"><h4>Nothing downloaded yet</h4></div></div>'
    + '<div class="tab-panel" data-panel="liked"><div class="panel-head"><div class="ph-title">Liked presets</div></div><div class="mgrid" id="wsLikedGrid"></div><div class="panel-empty" id="wsLikedEmpty"><h4>No liked presets</h4></div></div>'
    + '</div>'
    + '<div class="ws-toast" id="wsToast"></div>';

  function HEART_PATH() { return 'M12 21s-7.5-4.9-10-9.2C.4 8.9 1.6 5.5 4.8 4.8 7 4.3 9 5.5 12 8c3-2.5 5-3.7 7.2-3.2 3.2.7 4.4 4.1 2.8 7C19.5 16.1 12 21 12 21z'; }
  const HEART_FILLED  = '<svg class="hx" viewBox="0 0 24 24" width="16" height="16" fill="#ff5d5d"><path d="'+HEART_PATH()+'"/></svg>';
  const HEART_OUTLINE = '<i class="ti ti-heart hx"></i>';

  // ---- state ----
  let workshopLoaded = false, currentPresetId = null, previewGradient = ['#3ddc84','#f5b02e','#ff5d5d'];
  const fstate = { sort:'pop', game:'all', device:'all', q:'' };
  let listEl, emptyEl, loadingEl, errEl;

  // ---- render ----
  function renderCard(p) {
    const card = document.createElement('div');
    card.className = 'pc'; card.dataset.id = p.id; card.dataset.name = p.title || '';
    card.dataset.game = p.game_name || p.game || ''; card.dataset.downloads = p.downloads || 0;
    card.dataset.likes = p.likes || 0; card.dataset.rating = (p.rating == null ? 0 : p.rating);
    card.dataset.days = daysSince(p.created_at || Date.now()); card.dataset.devices = (p.devices||[]).join(',');
    card.dataset.tags = (p.tags||[]).join(',');
    const rt = p.rating == null ? '—' : (+p.rating).toFixed(1);
    card.innerHTML =
      '<div class="thumb"><img class="thumb-logo" src="'+logoFor(p.game)+'" alt="'+esc(p.game_name||'')+'"></div>'
      + '<div class="pc-info"><div class="pc-titrow"><span class="pc-tit">'+esc(p.title)+'</span></div>'
      + '<p class="pc-game">'+esc(p.game_name||p.game)+' · <span class="pc-by">by '+esc(p.author_name||'unknown')+'</span></p>'
      + '<p class="pc-desc">'+(p.tags||[]).slice(0,4).map(t=>'#'+esc(t)).join(' ')+'</p></div>'
      + '<div class="zones"></div>'
      + '<div class="pc-stats"><div class="s"><i class="ti ti-download"></i> '+fmt(p.downloads)+'</div>'
      + '<div class="s"><i class="ti ti-heart"></i> '+fmt(p.likes)+'</div>'
      + '<div class="s"><i class="ti ti-star-filled star"></i> '+rt+'</div></div>'
      + '<div class="pc-act"><button class="dl"><i class="ti ti-download"></i> Download</button>'
      + '<button class="heart" style="margin:0 auto;"><i class="ti ti-heart hx"></i></button></div>';
    renderArc(card.querySelector('.zones'), (p.swatch && p.swatch.length) ? p.swatch : ['#8b7cf6']);
    return card;
  }
  function applyFilters() {
    const cards = [].slice.call(listEl.querySelectorAll('.pc')), q = fstate.q.trim().toLowerCase();
    cards.forEach(c => {
      const okG = fstate.game==='all' || c.dataset.game===fstate.game;
      const okD = fstate.device==='all' || (c.dataset.devices||'').split(',').indexOf(fstate.device)>=0;
      const hay = (c.dataset.name+' '+c.dataset.game+' '+(c.dataset.tags||'')+' '+(c.querySelector('.pc-by')?c.querySelector('.pc-by').textContent:'')).toLowerCase();
      c.style.display = (okG && okD && (!q || hay.indexOf(q)>=0)) ? '' : 'none';
    });
    const cmp = { pop:(a,b)=>b.dataset.downloads-a.dataset.downloads, likes:(a,b)=>b.dataset.likes-a.dataset.likes, rating:(a,b)=>b.dataset.rating-a.dataset.rating, new:(a,b)=>a.dataset.days-b.dataset.days, az:(a,b)=>a.dataset.name.localeCompare(b.dataset.name) }[fstate.sort];
    const vis = cards.filter(c=>c.style.display!=='none').sort(cmp);
    vis.forEach(c=>listEl.insertBefore(c, emptyEl));
    emptyEl.classList.toggle('show', workshopLoaded && vis.length===0);
  }
  function compatFor(theme) {
    const evs = (theme && theme.enabled_events) || [], hasStrip = USER_DEVICES.some(d=>d==='lifx'||d==='nanoleaf'), mz = evs.filter(e=>MZ_EFFECTS.indexOf(e)>=0).length;
    if (mz===0 || hasStrip) return { cls:'ok', html:'<b>Fully compatible</b> with your setup<span class="sub">Your devices can render every effect.</span>' };
    return { cls:'warn', html:'<b>Partially compatible</b> with your setup<span class="sub">Your theme applies fully — '+mz+' effect'+(mz>1?'s':'')+' need a multizone strip you don\'t have.</span>' };
  }
  function renderDetail(p) {
    currentPresetId = p.id;
    const theme = p.theme || {};
    previewGradient = (theme.rpm_gradient && theme.rpm_gradient.length) ? theme.rpm_gradient : (p.swatch && p.swatch.length ? p.swatch : ['#3ddc84','#f5b02e','#ff5d5d']);
    document.getElementById('wsHero').style.background = 'radial-gradient(120% 140% at 70% 10%, '+(previewGradient[previewGradient.length-1]||'#7a0f1a')+' 0%, rgba(10,7,16,.55) 55%, #0a0710 100%)';
    const badge = document.getElementById('wsBadge'); badge.src = logoFor(p.game); badge.alt = p.game_name||'';
    document.getElementById('wsTit').textContent = p.title||'';
    document.getElementById('wsBy').textContent = 'by ' + (p.author_name||'unknown');
    document.getElementById('wsDl').textContent = fmt(p.downloads);
    document.getElementById('wsLikes').textContent = fmt(p.likes);
    document.getElementById('wsRating').textContent = p.rating==null ? '—' : (+p.rating).toFixed(1)+(p.rating_count?' ('+p.rating_count+')':'');
    document.getElementById('wsUpdated').textContent = shortDate(p.updated_at||p.created_at);
    document.getElementById('wsGames').innerHTML = '<span class="chip">'+esc(p.game_name||p.game)+'</span>';
    document.getElementById('wsDevices').innerHTML = (p.devices||[]).map(d=>{ const m=DEV_META[d]; if(!m) return ''; const o=USER_DEVICES.indexOf(d)>=0; return '<span class="chip '+m.cls+(o?'':' missing')+'"><span class="d"></span> '+m.label+(o?' <i class="ti ti-check own"></i>':'')+'</span>'; }).join('');
    document.getElementById('wsDesc').textContent = p.description || '';
    const cp = compatFor(theme); document.getElementById('wsCompat').className = 'd-compat '+cp.cls; document.getElementById('wsCompatText').innerHTML = cp.html;
    document.getElementById('wsTags').innerHTML = (p.tags||[]).map(t=>'<button class="d-tag" type="button">'+esc(t)+'</button>').join('');
    document.getElementById('wsShareLink').textContent = 'gridglow.app/w/'+p.id;
    document.getElementById('wsRate').innerHTML = [1,2,3,4,5].map(n=>'<button class="star-in" type="button" data-star="'+n+'">★</button>').join('') + '<span class="rate-note">Tap to rate</span>';
    const dl = document.getElementById('wsLike'); dl.classList.remove('liked'); dl.dataset.likes = p.likes||0;
    dl.querySelector('.hx').outerHTML = '<svg class="hx" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="'+HEART_PATH()+'"/></svg>';
    document.getElementById('wsLikeCount').textContent = fmt(p.likes);
  }
  async function selectPreset(id, cardEl) {
    listEl.querySelectorAll('.pc.sel').forEach(c=>c.classList.remove('sel'));
    if (cardEl) cardEl.classList.add('sel');
    const res = await wsGet(id); if (res && !res.error) renderDetail(res);
  }
  function setState(s) { loadingEl.style.display = s==='loading'?'':'none'; errEl.style.display = s==='error'?'':'none'; }
  async function loadWorkshop() {
    setState('loading'); emptyEl.classList.remove('show');
    const res = await wsList();
    listEl.querySelectorAll('.pc').forEach(c=>c.remove());
    if (!res || res.error) { workshopLoaded = false; setState('error'); return; }
    (res.presets||[]).forEach(p=>listEl.insertBefore(renderCard(p), emptyEl));
    workshopLoaded = true; setState('ready'); applyFilters();
    const first = listEl.querySelector('.pc'); if (first) selectPreset(first.dataset.id, first);
  }

  // ---- like flourish (heart → checkered flag → heart) ----
  const REDUCE = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const FLAG = '<svg class="hx flag-pop" viewBox="0 0 24 24" width="16" height="16"><rect x="5" y="3" width="1.6" height="18" rx=".8" fill="#cdd3e0"/><rect x="6.6" y="3.5" width="13" height="10" fill="#fff"/><g fill="#0e1424"><rect x="6.6" y="3.5" width="3.25" height="3.33"/><rect x="13.1" y="3.5" width="3.25" height="3.33"/><rect x="9.85" y="6.83" width="3.25" height="3.33"/><rect x="16.35" y="6.83" width="3.25" height="3.33"/><rect x="6.6" y="10.16" width="3.25" height="3.33"/><rect x="13.1" y="10.16" width="3.25" height="3.33"/></g></svg>';
  function popHeart(el) { if (!el) return; el.classList.remove('heart-pop'); void el.offsetWidth; el.classList.add('heart-pop'); el.addEventListener('animationend', () => el.classList.remove('heart-pop'), { once:true }); }
  function setIcon(host, html, pop) { const cur = host.querySelector('.hx'); if (cur) cur.outerHTML = html; else host.insertAdjacentHTML('afterbegin', html); if (pop) popHeart(host.querySelector('.hx')); }
  function likeFlag(host) { if (REDUCE) { setIcon(host, HEART_FILLED, true); return; } setIcon(host, FLAG, false); setTimeout(() => setIcon(host, HEART_FILLED, true), 440); }

  // ---- download → apply ----
  let pendingApplyTheme = null;
  const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 6"/></svg>';
  function runDownload(btn) {
    if (btn.dataset.dl === '1') return;
    if (!requireLogin()) return;
    const card = btn.closest('.pc'), id = (card && card.dataset.id) || currentPresetId;
    if (id) wsWrite('/api/workshop/presets/' + id + '/download', 'POST').then(r => { if (r.ok && r.data) pendingApplyTheme = r.data.theme || r.data; });
    btn.dataset.dl = '1';
    const orig = btn.innerHTML; btn.classList.add('is-dl');
    btn.innerHTML = '<span class="dl-fill"></span><span class="dl-lbl">Downloading 0%</span>';
    const fill = btn.querySelector('.dl-fill'), lbl = btn.querySelector('.dl-lbl'), DUR = 1500, t0 = performance.now();
    function step(now) {
      const e = 1 - Math.pow(1 - Math.min(1, (now - t0)/DUR), 2);
      fill.style.width = (e*100) + '%'; lbl.textContent = 'Downloading ' + Math.round(e*100) + '%';
      if (e < 1) requestAnimationFrame(step);
      else { fill.style.width='100%'; btn.classList.add('dl-done'); lbl.innerHTML = CHECK + ' Downloaded';
        setTimeout(() => { btn.classList.remove('is-dl','dl-done'); btn.innerHTML = orig; btn.dataset.dl='';
          if (pendingApplyTheme) { callApi('apply_workshop_preset', pendingApplyTheme); toast('Applied to your lights', 'ok'); } }, 850); }
    }
    requestAnimationFrame(step);
  }

  // ---- auth UI ----
  function renderAuth(sess) {
    session = sess || null;
    const area = document.getElementById('wsAuth'); if (!area) return;
    if (sess && sess.user) {
      const m = sess.user.user_metadata || {}, name = m.full_name || m.name || sess.user.email || 'Signed in';
      const av = m.avatar_url || m.picture || '', initial = esc((name[0]||'?').toUpperCase());
      area.innerHTML = '<button class="auth-user" id="wsChip" title="Account">' + (av ? '<img class="auth-avatar" src="'+esc(av)+'" alt="">' : '<span class="auth-avatar">'+initial+'</span>') + '<span class="auth-name">'+esc(name)+'</span><i class="ti ti-chevron-down auth-caret"></i></button>';
      document.getElementById('wsChip').addEventListener('click', () => openAccount());
    } else {
      area.innerHTML = '<button class="auth-signin" id="wsSignin" type="button">Sign in</button>';
      document.getElementById('wsSignin').addEventListener('click', openLogin);
    }
  }
  let lgBackdrop, acBackdrop, lgMode = 'signin';
  function injectModals() {
    root.insertAdjacentHTML('beforeend',
      '<div class="up-backdrop" id="wsLg"><div class="up-modal" style="width:400px;"><div class="up-head"><div><h3 id="wsLgTitle">Sign in</h3><p id="wsLgSub">Sign in to upload, like and download presets.</p></div><button class="up-x" id="wsLgClose" type="button"><i class="ti ti-x"></i></button></div>'
      + '<label class="up-field" id="wsLgNameF" style="display:none;"><span class="up-lbl">Display name</span><input class="up-input" id="wsLgName" type="text" placeholder="Shown as the preset author"></label>'
      + '<label class="up-field"><span class="up-lbl">Email</span><input class="up-input" id="wsLgEmail" type="email" placeholder="you@example.com"></label>'
      + '<label class="up-field"><span class="up-lbl">Password</span><input class="up-input" id="wsLgPass" type="password" placeholder="••••••••"></label>'
      + '<div id="wsLgForgotRow" style="margin:-4px 0 8px;"><button class="lg-switch" id="wsLgForgot" type="button">Forgot password?</button></div>'
      + '<div class="lg-msg" id="wsLgMsg"></div><div class="up-foot" style="justify-content:space-between;align-items:center;"><button class="lg-switch" id="wsLgSwitch" type="button">Create an account</button><button class="btn btn-primary btn-sm" id="wsLgSubmit" type="button">Sign in</button></div></div></div>'
      + '<div class="up-backdrop" id="wsAc"><div class="up-modal" style="width:420px;"><div class="up-head"><div><h3>Account</h3><p id="wsAcWho">Signed in</p></div><button class="up-x" id="wsAcClose" type="button"><i class="ti ti-x"></i></button></div>'
      + '<div class="up-field"><span class="up-lbl">Email</span><div style="display:flex;gap:8px;"><input class="up-input" id="wsAcEmail" type="email"><button class="btn btn-ghost btn-sm" id="wsAcEmailBtn" type="button">Update</button></div></div>'
      + '<div class="up-field"><span class="up-lbl">New password</span><div style="display:flex;gap:8px;"><input class="up-input" id="wsAcPass" type="password" placeholder="At least 6 characters"><button class="btn btn-ghost btn-sm" id="wsAcPassBtn" type="button">Update</button></div></div>'
      + '<div class="lg-msg" id="wsAcMsg"></div><div class="up-foot" style="justify-content:flex-start;"><button class="btn btn-ghost btn-sm" id="wsAcSignout" type="button"><i class="ti ti-logout"></i> Sign out</button></div></div></div>');
    lgBackdrop = document.getElementById('wsLg'); acBackdrop = document.getElementById('wsAc');
    const lgMsg = (t,ok) => { const el=document.getElementById('wsLgMsg'); el.textContent=t||''; el.className='lg-msg'+(ok?' ok':''); };
    function setMode(mode) { lgMode = mode;
      document.getElementById('wsLgTitle').textContent = mode==='signup'?'Create account':'Sign in';
      document.getElementById('wsLgSubmit').textContent = mode==='signup'?'Create account':'Sign in';
      document.getElementById('wsLgSwitch').textContent = mode==='signup'?'Have an account? Sign in':'Create an account';
      document.getElementById('wsLgNameF').style.display = mode==='signup'?'':'none';
      document.getElementById('wsLgForgotRow').style.display = mode==='signup'?'none':''; lgMsg(''); }
    window._wsOpenLogin = () => { setMode('signin'); lgBackdrop.classList.add('open'); setTimeout(()=>document.getElementById('wsLgEmail').focus(),60); };
    document.getElementById('wsLgClose').addEventListener('click', ()=>lgBackdrop.classList.remove('open'));
    lgBackdrop.addEventListener('click', e=>{ if(e.target===lgBackdrop) lgBackdrop.classList.remove('open'); });
    document.getElementById('wsLgSwitch').addEventListener('click', ()=>setMode(lgMode==='signup'?'signin':'signup'));
    document.getElementById('wsLgForgot').addEventListener('click', async ()=>{ const email=document.getElementById('wsLgEmail').value.trim(); if(!email){lgMsg('Enter your email above first.');return;} if(!sb) return; const {error}=await sb.auth.resetPasswordForEmail(email,{redirectTo:location.href}); lgMsg(error?error.message:'Password reset link sent — check your email.', !error); });
    async function submit() {
      if (!sb) { lgMsg('Auth unavailable.'); return; }
      const email = document.getElementById('wsLgEmail').value.trim(), pass = document.getElementById('wsLgPass').value;
      if (!email || !pass) { lgMsg('Enter your email and password.'); return; }
      const btn = document.getElementById('wsLgSubmit'); btn.disabled = true; lgMsg('…');
      try {
        if (lgMode === 'signup') {
          const full_name = document.getElementById('wsLgName').value.trim();
          const opts = { emailRedirectTo: location.href }; if (full_name) opts.data = { full_name };
          const { data, error } = await sb.auth.signUp({ email, password: pass, options: opts });
          if (error) throw error;
          if (data.session) lgBackdrop.classList.remove('open'); else lgMsg('Check your email to confirm your account.', true);
        } else {
          const { error } = await sb.auth.signInWithPassword({ email, password: pass });
          if (error) throw error; lgBackdrop.classList.remove('open');
        }
      } catch (e) { lgMsg(e.message || 'Sign-in failed.'); } finally { btn.disabled = false; }
    }
    document.getElementById('wsLgSubmit').addEventListener('click', submit);
    document.getElementById('wsLgPass').addEventListener('keydown', e=>{ if(e.key==='Enter') submit(); });
    // account modal
    const acMsg = (t,ok) => { const el=document.getElementById('wsAcMsg'); el.textContent=t||''; el.className='lg-msg'+(ok?' ok':''); };
    window._wsOpenAccount = (mode) => { if(!session){ window._wsOpenLogin(); return; } document.getElementById('wsAcWho').textContent='Signed in as '+(session.user.email||''); document.getElementById('wsAcEmail').value=session.user.email||''; document.getElementById('wsAcPass').value=''; acMsg(mode==='password'?'Set a new password to finish resetting.':''); acBackdrop.classList.add('open'); };
    document.getElementById('wsAcClose').addEventListener('click', ()=>acBackdrop.classList.remove('open'));
    acBackdrop.addEventListener('click', e=>{ if(e.target===acBackdrop) acBackdrop.classList.remove('open'); });
    document.getElementById('wsAcEmailBtn').addEventListener('click', async ()=>{ const email=document.getElementById('wsAcEmail').value.trim(); if(!email){acMsg('Enter an email.');return;} const {error}=await sb.auth.updateUser({email},{emailRedirectTo:location.href}); acMsg(error?error.message:'Confirmation sent to the new address.', !error); });
    document.getElementById('wsAcPassBtn').addEventListener('click', async ()=>{ const pw=document.getElementById('wsAcPass').value; if(pw.length<6){acMsg('Password must be at least 6 characters.');return;} const {error}=await sb.auth.updateUser({password:pw}); if(error)acMsg(error.message); else {acMsg('Password updated.',true); document.getElementById('wsAcPass').value='';} });
    document.getElementById('wsAcSignout').addEventListener('click', ()=>{ if(sb) sb.auth.signOut(); acBackdrop.classList.remove('open'); });
  }
  function openLogin() { if (window._wsOpenLogin) window._wsOpenLogin(); }
  function openAccount(mode) { if (window._wsOpenAccount) window._wsOpenAccount(mode); }

  // ---- personal tabs ----
  const PERSONAL = { uploads:'mine', downloads:'downloaded', liked:'liked' };
  function mgmtActions(kind) {
    if (kind==='uploads')   return '<button class="mbtn danger" data-del title="Delete"><i class="ti ti-trash"></i></button>';
    if (kind==='downloads') return '<button class="mbtn primary" data-apply><i class="ti ti-bulb"></i> Apply</button><button class="mbtn danger" data-del><i class="ti ti-trash"></i></button>';
    return '<button class="mbtn primary" data-dl><i class="ti ti-download"></i> Download</button><button class="mbtn danger" data-unlike title="Unlike"><svg viewBox="0 0 24 24" width="15" height="15" fill="#ff5d5d"><path d="'+HEART_PATH()+'"/></svg></button>';
  }
  function mgmtRow(p, kind) {
    const row = document.createElement('div'); row.className='mrow'; row.dataset.id=p.id;
    const meta = kind==='downloads' ? '<span class="mi"><i class="ti ti-download"></i> Downloaded</span>'
      : '<span class="mi"><i class="ti ti-download"></i> '+fmt(p.downloads)+'</span><span class="mi"><i class="ti ti-heart"></i> '+fmt(p.likes)+'</span>';
    row.innerHTML = '<div class="thumb"><img class="thumb-logo" src="'+logoFor(p.game)+'" alt=""></div>'
      + '<div class="mrow-info"><div class="mrow-titrow"><span class="mrow-tit">'+esc(p.title)+'</span></div>'
      + '<div class="mrow-game">'+esc(p.game_name||p.game)+(kind!=='uploads'?' · by '+esc(p.author_name||'unknown'):'')+'</div><div class="mrow-meta">'+meta+'</div></div>'
      + '<div class="arc-mini"></div><div class="mrow-act">'+mgmtActions(kind)+'</div>';
    renderArc(row.querySelector('.arc-mini'), (p.swatch&&p.swatch.length)?p.swatch:['#8b7cf6']);
    return row;
  }
  async function loadPersonal(name) {
    const grid = document.getElementById('ws'+name.charAt(0).toUpperCase()+name.slice(1)+'Grid');
    const empty = document.getElementById('ws'+name.charAt(0).toUpperCase()+name.slice(1)+'Empty');
    if (empty) empty.classList.remove('show');
    if (!session) { grid.innerHTML = '<div class="list-state"><div class="ls-sub">Sign in to see your '+name+'.</div><button class="btn btn-primary btn-sm" id="wsPLogin" style="margin-top:12px;">Sign in</button></div>'; grid.querySelector('#wsPLogin').addEventListener('click', openLogin); return; }
    grid.innerHTML = '<div class="list-state">Loading…</div>';
    const r = await wsWrite('/api/workshop/presets?'+PERSONAL[name]+'=1', 'GET');
    const items = (r.data && r.data.presets) || [];
    grid.innerHTML = ''; items.forEach(p=>grid.appendChild(mgmtRow(p, name)));
    if (empty) empty.classList.toggle('show', items.length===0);
  }

  // ---- preview LED loop ----
  function initPreview() {
    const strip = document.getElementById('wsStrip'), flag = document.getElementById('wsFlag'), cap = document.getElementById('wsCap');
    if (!strip) return;
    const N = 18, cells = [];
    for (let i=0;i<N;i++){ const c=document.createElement('div'); c.className='lp-cell'; strip.appendChild(c); cells.push(c); }
    const colourAt = i => sampleGradient(previewGradient, i/(N-1));
    let phase='start', rpm=0, dir=1, last=0, t0=0, flagIdx=0, running=false, raf=0;
    const FLAG_MS=1300, STEP_MS=650;
    const flags=[{c:'rgba(109,178,255,.55)',label:'Blue flag · car behind'},{c:'rgba(245,176,46,.55)',label:'Yellow flag · caution'},{c:'rgba(61,220,132,.5)',label:'Green flag · track clear'}];
    const clear = () => cells.forEach(c=>{ c.style.background='#1c2438'; c.style.color=''; c.style.height='18px'; c.classList.remove('lit'); });
    const idle = () => { flag.style.opacity=0; clear(); cap.textContent='Idle · press play'; };
    function frame(now) {
      if (!running) return;
      const dt = Math.min(64, now-last); last = now;
      if (phase==='start') { const el=now-t0, HE=5*STEP_MS+750, OE=HE+450;
        if (el<HE){ const step=Math.min(5,Math.floor(el/STEP_MS)), lit=Math.round(step/5*N); cells.forEach((c,i)=>{const on=i<lit; c.style.color='#ff5d5d'; c.style.background=on?'#ff3b3b':'#1c2438'; c.style.height=on?'28px':'18px'; c.classList.toggle('lit',on);}); cap.textContent=step<5?'Start lights · '+step+'/5':'Lights set…'; }
        else if (el<OE){ clear(); cap.textContent='Lights out — GO!'; } else { phase='rev'; rpm=0; dir=1; t0=now; } }
      else if (phase==='rev') { rpm+=dir*dt/1400; if(rpm>=1){rpm=1;phase='shift';t0=now;} const lit=Math.round(rpm*N); cells.forEach((c,i)=>{const on=i<lit,col=colourAt(i); c.style.color=col; c.style.background=on?col:'#1c2438'; c.style.height=on?(22+(i/(N-1))*10)+'px':'18px'; c.classList.toggle('lit',on);}); cap.textContent=rpm>0.8?'RPM · redline':rpm>0.5?'RPM · building':'RPM · on throttle'; }
      else if (phase==='shift') { const on=(Math.floor(now/70)%2)===0; cells.forEach(c=>{const col=on?'#c4b5fd':'#ff5d5d'; c.style.color=col; c.style.background=col; c.style.height='30px'; c.classList.add('lit');}); cap.textContent='Shift ▲'; if(now-t0>520){phase='fall';t0=now;} }
      else if (phase==='fall') { rpm-=dt/500; if(rpm<=0){rpm=0;phase='fastest';t0=now;} const lit=Math.round(Math.max(0,rpm)*N); cells.forEach((c,i)=>{const on=i<lit,col=colourAt(i); c.style.color=col; c.style.background=on?col:'#1c2438'; c.style.height=on?'24px':'18px'; c.classList.toggle('lit',on);}); cap.textContent='Lift · coasting'; }
      else if (phase==='fastest') { const el=now-t0,on=(Math.floor(el/165)%2)===0; cells.forEach(c=>{c.style.color='#c4b5fd'; c.style.background=on?'#a855f7':'#1c2438'; c.style.height=on?'30px':'18px'; c.classList.toggle('lit',on);}); cap.textContent='Fastest lap ⏱'; if(el>1350){phase='flags';flagIdx=0;t0=now;} }
      else if (phase==='flags') { const el=now-t0,fk=flags[flagIdx],pulse=(Math.floor(el/180)%2)===0; flag.style.background=fk.c; flag.style.opacity=pulse?0.9:0.28; clear(); cap.textContent=fk.label; if(el>FLAG_MS){flagIdx++;t0=now; if(flagIdx>=flags.length){flag.style.opacity=0;phase='start';}} }
      raf = requestAnimationFrame(frame);
    }
    const btn = document.getElementById('wsPlay'), lbl = document.getElementById('wsPlayLbl');
    const PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>', PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>';
    function setBtn(p){ btn.innerHTML = (p?PAUSE:PLAY)+'<span id="wsPlayLbl">'+(p?'Pause':'Play')+'</span>'; }
    function play(){ if(running) return; running=true; phase='start'; rpm=0; dir=1; flag.style.opacity=0; last=performance.now(); t0=last; setBtn(true); raf=requestAnimationFrame(frame); }
    function pause(){ running=false; cancelAnimationFrame(raf); idle(); setBtn(false); }
    btn.addEventListener('click', ()=> running?pause():play());
    idle();
  }

  // ---- wiring ----
  function wire() {
    listEl = document.getElementById('wsList'); emptyEl = document.getElementById('wsEmpty');
    loadingEl = document.getElementById('wsLoading'); errEl = document.getElementById('wsErr');
    injectModals();
    // auth
    if (sb) { sb.auth.getSession().then(({data})=>{ renderAuth(data.session); callApi('set_workshop_token', data.session?data.session.access_token:null); if(/access_token|[?&#]type=/.test(location.hash)) history.replaceState(null,'',location.pathname+location.search); });
      sb.auth.onAuthStateChange((evt, sess)=>{ renderAuth(sess); callApi('set_workshop_token', sess?sess.access_token:null); if(evt==='PASSWORD_RECOVERY') openAccount('password'); }); }
    else renderAuth(null);
    document.getElementById('wsUploadBtn').addEventListener('click', openUpload);
    // search
    const si = document.getElementById('wsSearch'), box = si.closest('.search');
    function onSearch(){ fstate.q=si.value; box.classList.toggle('has-q', si.value.trim().length>0); applyFilters(); }
    si.addEventListener('input', onSearch);
    document.getElementById('wsSearchClear').addEventListener('click', ()=>{ si.value=''; onSearch(); si.focus(); });
    // dropdowns
    const OPTS = { sort:[['pop','Popular'],['likes','Most liked'],['rating','Highest rated'],['new','Newest'],['az','Name A–Z']], game:[['all','All Games'],['F1 25 – F1 21','F1 25 – F1 21'],['Assetto Corsa Competizione','ACC'],['DiRT Rally 2.0','DiRT Rally 2.0'],['EA SPORTS WRC','EA SPORTS WRC']], device:[['all','All Devices'],['hue','Philips Hue'],['nanoleaf','Nanoleaf'],['lifx','LIFX']] };
    root.querySelectorAll('.fdrop[data-key]').forEach(drop=>{ const key=drop.dataset.key, lbl=drop.querySelector('.fdrop-lbl'); const menu=document.createElement('div'); menu.className='fmenu'; menu.innerHTML=OPTS[key].map(o=>'<div class="fmenu-item" data-v="'+esc(o[0])+'">'+esc(o[1])+'<i class="ti ti-check ck"></i></div>').join(''); drop.appendChild(menu);
      menu.addEventListener('click', e=>{ const it=e.target.closest('.fmenu-item'); if(!it) return; e.stopPropagation(); fstate[key]=it.dataset.v; lbl.textContent=it.textContent.trim(); menu.querySelectorAll('.fmenu-item').forEach(x=>x.classList.remove('active')); it.classList.add('active'); drop.classList.remove('open'); applyFilters(); });
      drop.addEventListener('click', e=>{ if(e.target.closest('.fmenu')) return; const open=drop.classList.contains('open'); root.querySelectorAll('.fdrop.open').forEach(d=>d.classList.remove('open')); if(!open) drop.classList.add('open'); }); });
    document.addEventListener('click', e=>{ if(!e.target.closest('.fdrop')) root.querySelectorAll('.fdrop.open').forEach(d=>d.classList.remove('open')); });
    document.getElementById('wsReset').addEventListener('click', ()=>{ Object.assign(fstate,{sort:'pop',game:'all',device:'all',q:''}); si.value=''; box.classList.remove('has-q'); root.querySelectorAll('.fdrop[data-key]').forEach(d=>{d.querySelector('.fdrop-lbl').textContent=OPTS[d.dataset.key][0][1];}); applyFilters(); });
    // tabs
    root.querySelectorAll('.tab[data-tab]').forEach(tab=>tab.addEventListener('click', ()=>{ root.querySelectorAll('.tab').forEach(t=>t.classList.remove('active')); tab.classList.add('active'); root.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active', p.dataset.panel===tab.dataset.tab)); if(PERSONAL[tab.dataset.tab]) loadPersonal(tab.dataset.tab); }));
    // list delegation: select / download / like
    listEl.addEventListener('click', e=>{ const dl=e.target.closest('.dl'); if(dl){ e.stopPropagation(); runDownload(dl); return; } const h=e.target.closest('.heart'); if(h){ e.stopPropagation(); if(!requireLogin()) return; const c=h.closest('.pc'); const on=h.classList.toggle('on'); if(on) likeFlag(h); else setIcon(h, HEART_OUTLINE, true); if(c&&c.dataset.id) wsWrite('/api/workshop/presets/'+c.dataset.id+'/like','POST'); return; } const card=e.target.closest('.pc'); if(card) selectPreset(card.dataset.id, card); });
    // detail actions
    document.getElementById('wsDownload').addEventListener('click', function(){ runDownload(this); });
    const dLike = document.getElementById('wsLike');
    dLike.addEventListener('click', ()=>{ if(!requireLogin()) return; const liked=dLike.classList.toggle('liked'); const base=+(dLike.dataset.likes||0); document.getElementById('wsLikeCount').textContent=fmt(liked?base+1:base); if(liked) likeFlag(dLike); else setIcon(dLike, HEART_OUTLINE, true); if(currentPresetId) wsWrite('/api/workshop/presets/'+currentPresetId+'/like','POST'); });
    document.getElementById('wsTags').addEventListener('click', e=>{ const t=e.target.closest('.d-tag'); if(!t) return; si.value=t.textContent.trim(); onSearch(); });
    const rate = document.getElementById('wsRate');
    const paint = n => rate.querySelectorAll('.star-in').forEach(s=>s.classList.toggle('on', +s.dataset.star<=n));
    rate.addEventListener('mouseover', e=>{ const s=e.target.closest('.star-in'); if(s) paint(+s.dataset.star); });
    rate.addEventListener('mouseout', ()=> paint(+(rate.dataset.rated||0)));
    rate.addEventListener('click', async e=>{ const s=e.target.closest('.star-in'); if(!s) return; if(!requireLogin()) return; const stars=+s.dataset.star; const r=await wsWrite('/api/workshop/presets/'+currentPresetId+'/rate','POST',{stars}); if(r.ok){ rate.dataset.rated=stars; paint(stars); rate.querySelector('.rate-note').textContent='Rated '+stars+'★'; if(r.data&&r.data.rating!=null) document.getElementById('wsRating').textContent=(+r.data.rating).toFixed(1)+(r.data.rating_count?' ('+r.data.rating_count+')':''); toast('Thanks for rating!','ok'); } });
    // personal-panel delegation
    root.querySelectorAll('.tab-panel[data-panel] .mgrid').forEach(grid=> grid.addEventListener('click', async e=>{ const row=e.target.closest('.mrow'); if(!row) return; const id=row.dataset.id; if(e.target.closest('[data-apply]')){ const r=await wsWrite('/api/workshop/presets/'+id+'/download','POST'); if(r.ok&&r.data){ callApi('apply_workshop_preset', r.data.theme||r.data); toast('Applied to your lights','ok'); } return; } const d=e.target.closest('[data-dl]'); if(d){ runDownload(d); return; } if(e.target.closest('[data-del]')){ if(!requireLogin()) return; const r=await wsWrite('/api/workshop/presets/'+id,'DELETE'); if(r.ok){ row.remove(); toast('Removed','ok'); } else toast('Delete failed','err'); return; } if(e.target.closest('[data-unlike]')){ if(!requireLogin()) return; const r=await wsWrite('/api/workshop/presets/'+id+'/like','POST'); if(r.ok){ row.remove(); toast('Unliked','ok'); } } }));
    initPreview();
  }

  // ---- upload modal ----
  function openUpload() { if (window._wsOpenUpload) window._wsOpenUpload(); }
  function injectUpload() {
    root.insertAdjacentHTML('beforeend',
      '<div class="up-backdrop" id="wsUp"><div class="up-modal"><div class="up-head"><div><h3>Upload Preset</h3><p>Share your current lighting setup with the community.</p></div><button class="up-x" id="wsUpClose" type="button"><i class="ti ti-x"></i></button></div>'
      + '<label class="up-field"><span class="up-lbl">Preset name</span><input class="up-input" id="wsUpName" type="text" value="My preset"></label>'
      + '<label class="up-field"><span class="up-lbl">Description</span><textarea class="up-input up-textarea" id="wsUpDesc" rows="3"></textarea></label>'
      + '<div class="up-row"><label class="up-field"><span class="up-lbl">Game</span><div class="up-select" id="wsUpGame" data-game="f1_25">F1 25 – F1 21 <i class="ti ti-chevron-down"></i></div></label>'
      + '<div class="up-field"><span class="up-lbl">Visibility</span><div class="up-seg"><button class="up-seg-btn active" type="button">Public</button><button class="up-seg-btn" type="button">Unlisted</button></div></div></div>'
      + '<div class="up-field"><span class="up-lbl">Supported devices</span><div class="up-chips"><button class="up-chip on dev-hue" type="button"><span class="d"></span> Philips Hue</button><button class="up-chip on dev-nano" type="button"><span class="d"></span> Nanoleaf</button><button class="up-chip on dev-lifx" type="button"><span class="d"></span> LIFX</button></div></div>'
      + '<div class="up-field"><span class="up-lbl">Tags</span><div class="up-tags"><input class="up-tag-input" placeholder="Add tag…"></div></div>'
      + '<div class="up-foot"><button class="btn btn-ghost btn-sm" id="wsUpCancel" type="button">Cancel</button><button class="btn btn-primary btn-sm" id="wsUpPublish" type="button"><i class="ti ti-upload"></i> Publish preset</button></div></div></div>');
    const up = document.getElementById('wsUp');
    window._wsOpenUpload = () => { if(!requireLogin()) return; up.classList.add('open'); };
    document.getElementById('wsUpClose').addEventListener('click', ()=>up.classList.remove('open'));
    document.getElementById('wsUpCancel').addEventListener('click', ()=>up.classList.remove('open'));
    up.addEventListener('click', e=>{ if(e.target===up) up.classList.remove('open'); });
    up.querySelectorAll('.up-chip').forEach(c=>c.addEventListener('click', ()=>c.classList.toggle('on')));
    up.querySelectorAll('.up-seg-btn').forEach(b=>b.addEventListener('click', ()=>{ b.parentElement.querySelectorAll('.up-seg-btn').forEach(x=>x.classList.remove('active')); b.classList.add('active'); }));
    const tagsBox = up.querySelector('.up-tags'), tin = up.querySelector('.up-tag-input');
    tagsBox.addEventListener('click', e=>{ const x=e.target.closest('.up-tag i'); if(x) x.closest('.up-tag').remove(); });
    tin.addEventListener('keydown', e=>{ if(e.key==='Enter'||e.key===','){ e.preventDefault(); const v=tin.value.replace(/,+$/,'').trim(); if(v){ const chip=document.createElement('span'); chip.className='up-tag'; chip.innerHTML=esc(v)+' <i class="ti ti-x"></i>'; tagsBox.insertBefore(chip, tin); } tin.value=''; } else if(e.key==='Backspace'&&!tin.value){ const last=[].slice.call(tagsBox.querySelectorAll('.up-tag')).pop(); if(last) last.remove(); } });
    const GAMES=[['f1_25','F1 25 – F1 21'],['acc','Assetto Corsa Competizione'],['ac','Assetto Corsa'],['dr2','DiRT Rally 2.0'],['forza','Forza Horizon'],['pcars2','Project CARS 2'],['iracing','iRacing'],['wrc','EA SPORTS WRC']];
    const ug = document.getElementById('wsUpGame'); const gm=document.createElement('div'); gm.className='fmenu'; gm.innerHTML=GAMES.map(g=>'<div class="fmenu-item" data-slug="'+g[0]+'">'+esc(g[1])+'</div>').join(''); ug.style.position='relative'; ug.appendChild(gm);
    ug.addEventListener('click', e=>{ if(e.target.closest('.fmenu')) return; gm.style.display = gm.style.display==='block'?'none':'block'; });
    gm.addEventListener('click', e=>{ const it=e.target.closest('.fmenu-item'); if(!it) return; ug.dataset.game=it.dataset.slug; ug.firstChild.textContent=it.textContent+' '; gm.style.display='none'; });
    document.getElementById('wsUpPublish').addEventListener('click', async ()=>{ if(!requireLogin()) return; const devSlug={'dev-hue':'hue','dev-nano':'nanoleaf','dev-lifx':'lifx'}; const devices=[].slice.call(up.querySelectorAll('.up-chip.on')).map(c=>devSlug[[].slice.call(c.classList).find(k=>k.indexOf('dev-')===0)]).filter(Boolean); const tags=[].slice.call(up.querySelectorAll('.up-tag')).map(t=>(t.firstChild&&t.firstChild.textContent||'').trim()).filter(Boolean); const body={ title:document.getElementById('wsUpName').value.trim()||'Untitled preset', description:document.getElementById('wsUpDesc').value.trim(), game:document.getElementById('wsUpGame').dataset.game||'f1_25', visibility:(up.querySelector('.up-seg-btn.active')?up.querySelector('.up-seg-btn.active').textContent.trim().toLowerCase():'public'), tags, devices, gridglow_preset:1, app_min_version:'0.10.0', theme:{} }; const pub=document.getElementById('wsUpPublish'); pub.disabled=true; toast('Publishing…'); const r=await wsWrite('/api/workshop/presets','POST',body); pub.disabled=false; console.log('[workshop] upload result', r); if(r.ok){ up.classList.remove('open'); toast('Preset published!','ok'); loadWorkshop(); } else { const e=(r.data&&(r.data.error||r.data.detail))||('status '+(r.status||'?')); toast('Upload failed: '+e,'err'); } });
  }

  // ---- mount ----
  let mounted = false;
  function mount() {
    if (mounted) return; mounted = true;
    root.innerHTML = MARKUP;
    wire();
    injectUpload();
    loadWorkshop();
  }
  const navBtn = document.querySelector('.nav-btn[data-page="workshop"]');
  if (navBtn) navBtn.addEventListener('click', mount);
})();
