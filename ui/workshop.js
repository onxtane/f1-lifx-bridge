/* Community Workshop — app module.
 *
 * Ported from docs/mockups/workshop-community.html. Differences vs. the mockup:
 *   - Data goes through Python: callApi('workshop_*') instead of fetch(). Python holds
 *     the Bearer token; the webview only pushes it via set_workshop_token.
 *   - Auth runs in the webview via the vendored supabase-js (window.supabase).
 *   - Logos are 'logos/<slug>.png' (app serves ui/ as root), not '../../ui/logos/'.
 *   - No standalone window chrome; mounts into #workshopRoot on the #page-workshop page.
 *
 * SCAFFOLD: this establishes the bridge + auth + mount hook and does a test read.
 * The full Discover/detail/modals/write-path port lands in the next B2 pass.
 */
(function () {
  'use strict';

  const root = document.getElementById('workshopRoot');
  if (!root) return;

  const SUPABASE_URL  = 'https://upvbmoseimgiathprtsj.supabase.co';
  const SUPABASE_ANON = 'sb_publishable_bChP2PoDJXHNSHysW63DNA_CK7M1RKC';

  // pywebview bridge — same contract as index.html's callApi (no-ops in browser preview)
  function api(fn) {
    const args = Array.prototype.slice.call(arguments, 1);
    if (window.pywebview && window.pywebview.api && window.pywebview.api[fn]) {
      return window.pywebview.api[fn].apply(null, args);
    }
    return Promise.resolve(null);
  }

  // Supabase auth client (vendored UMD). On auth change, hand the token to Python so
  // its workshop_* write/personal calls can send it as Bearer.
  let sb = null;
  if (window.supabase && window.supabase.createClient) {
    sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON);
    sb.auth.onAuthStateChange((_evt, session) =>
      api('set_workshop_token', session ? session.access_token : null));
  }

  let mounted = false;
  async function mount() {
    if (mounted) return;
    mounted = true;
    // TODO(B2): render the scoped Discover split, detail panel, Upload/Apply modals,
    // personal tabs and auth UI here. For now, a placeholder + a bridge read to prove wiring.
    root.innerHTML =
      '<div style="padding:40px 28px;color:var(--text-muted);font-size:14px;">' +
      'Community Workshop — module loaded. UI port in progress.</div>';
    try {
      const res = await api('workshop_list');
      console.log('[workshop] workshop_list →', res);
    } catch (e) { console.warn('[workshop] read failed', e); }
  }

  // mount lazily the first time the Workshop tab is opened
  const navBtn = document.querySelector('.nav-btn[data-page="workshop"]');
  if (navBtn) navBtn.addEventListener('click', mount);
})();
