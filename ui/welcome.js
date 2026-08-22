/* GridGlow — post-verification welcome overlay.
 *
 * A faithful port of the "GridGlow Welcome.dc.html" Claude Design motion mockup.
 * The original is one continuous composition keyed to an authored time axis T
 * (see welcome-scene.jsx). We reproduce that timeline natively over the LIVE app:
 * the real Workshop is dimmed/blurred underneath while a card rises, a check ring
 * draws and shrinks to a badge, the LED strip ignites, and the welcome copy wipes
 * in. Unlike the looping mockup, we HOLD on the welcome beat until the user acts,
 * then play the hand-off.
 *
 * window.playWelcome({ onEnter }) mounts and runs it once.
 */
(function () {
  'use strict';

  // ── easing + timeline primitives (verbatim from animations-v3.jsx) ──────────
  var E = {
    outCubic:   function (t) { return (--t) * t * t + 1; },
    outQuart:   function (t) { return 1 - (--t) * t * t * t; },
    outBack:    function (t) { var c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
    inOutCubic: function (t) { return t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1; },
    inCubic:    function (t) { return t * t * t; },
  };
  function animate(from, to, start, end, ease) {
    return function (t) {
      if (t <= start) return from;
      if (t >= end) return to;
      return from + (to - from) * ease((t - start) / (end - start));
    };
  }
  function interp(inp, out, ease) {
    return function (t) {
      if (t <= inp[0]) return out[0];
      if (t >= inp[inp.length - 1]) return out[out.length - 1];
      for (var i = 0; i < inp.length - 1; i++) {
        if (t >= inp[i] && t <= inp[i + 1]) {
          var span = inp[i + 1] - inp[i];
          var l = span === 0 ? 0 : (t - inp[i]) / span;
          return out[i] + (out[i + 1] - out[i]) * ease(l);
        }
      }
      return out[out.length - 1];
    };
  }
  var enter = function (f, t, s, e) { return animate(f, t, s, e, E.outCubic); };
  var draw  = function (f, t, s, e) { return animate(f, t, s, e, E.outQuart); };
  var pop   = function (f, t, s, e) { return animate(f, t, s, e, E.outBack); };

  function hexA(hex, a) {
    var h = String(hex).replace('#', '');
    var n = parseInt(h.length === 3 ? h.split('').map(function (c) { return c + c; }).join('') : h, 16);
    a = Math.max(0, Math.min(1, a));
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a.toFixed(3) + ')';
  }

  // ── design constants (from welcome-scene.jsx) ───────────────────────────────
  var A = '#9184d9';                 // accent (the app's Pro blurple)
  var STRIP = 'linear-gradient(90deg,#9184d9 0%,#6ea8ff 22%,#3fd6c0 44%,#8ed44a 64%,#f2b53b 82%,#e0453a 100%)';
  var INTRO_END = 3.4;               // authored T where the welcome beat is fully settled
  var OUTRO_DUR = 0.55;              // hand-off duration (seconds)

  var running = false;

  function playWelcome(opts) {
    if (running) return;             // never stack two
    running = true;
    opts = opts || {};
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── build DOM ─────────────────────────────────────────────────────────────
    var root = document.createElement('div');
    root.className = 'gw-root';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');   // keep AT out of the dimmed app behind us
    root.setAttribute('aria-label', 'Welcome to GridGlow');
    root.tabIndex = -1;                         // focusable container so focus can move in
    root.innerHTML =
      '<div class="gw-dim"></div>' +
      '<div class="gw-halo"></div>' +
      '<div class="gw-wash gw-wash0"></div>' +
      '<div class="gw-wash gw-wash1"></div>' +
      '<button class="gw-skip" type="button">Skip</button>' +
      '<div class="gw-card">' +
        '<div class="gw-strip-base"></div>' +
        '<div class="gw-strip-grad"></div>' +
        '<div class="gw-strip-glow"></div>' +
        '<div class="gw-strip-dot"></div>' +
        '<div class="gw-hold"></div>' +
        '<div class="gw-ring"><svg viewBox="0 0 100 100">' +
          '<circle class="gw-ring-c" cx="50" cy="50" r="44" fill="none" stroke-width="4" stroke-dasharray="276.5" transform="rotate(-90 50 50)"></circle>' +
          '<path class="gw-ring-p" d="M30 51 L43.5 65 L71 36" fill="none" stroke="var(--gw-text)" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="66"></path>' +
        '</svg></div>' +
        '<div class="gw-verified"><span>EMAIL VERIFIED</span></div>' +
        '<div class="gw-kicker">ACCESS UNLOCKED</div>' +
        '<div class="gw-head"></div>' +
        '<div class="gw-sub"></div>' +
        '<button class="gw-btn gw-btn-primary" type="button">Enter the Workshop</button>' +
      '</div>';
    document.body.appendChild(root);

    var $ = function (s) { return root.querySelector(s); };
    var dim = $('.gw-dim'), halo = $('.gw-halo'),
        wash0 = $('.gw-wash0'), wash1 = $('.gw-wash1'), washes = [wash0, wash1],
        card = $('.gw-card'),
        stripGrad = $('.gw-strip-grad'), stripGlow = $('.gw-strip-glow'),
        stripDot = $('.gw-strip-dot'), holdBar = $('.gw-hold'),
        ringWrap = $('.gw-ring'), ringSvg = ringWrap.querySelector('svg'),
        ringC = $('.gw-ring-c'), ringP = $('.gw-ring-p'),
        verified = $('.gw-verified'), kicker = $('.gw-kicker'),
        head = $('.gw-head'), sub = $('.gw-sub'),
        btnPrimary = $('.gw-btn-primary'),
        skip = $('.gw-skip');

    head.textContent = 'Welcome to the grid';
    sub.textContent = 'Your email is verified. Every preset in the Workshop is yours to download, remix and share.';
    stripGrad.style.background = STRIP;
    stripGlow.style.background = STRIP;
    ringC.setAttribute('stroke', hexA(A, 0.9));

    // dim the real app underneath (restored on cleanup)
    var appEl = document.querySelector('.app');
    var savedFilter = appEl ? appEl.style.filter : '';
    var savedTransform = appEl ? appEl.style.transform : '';
    var savedOrigin = appEl ? appEl.style.transformOrigin : '';

    // ── per-frame renderer ──────────────────────────────────────────────────
    // T: authored time for the beat animations. cont: continuous clock for the
    // sin-based drift/breathe. holdVal: strip highlight position (<0 = hidden).
    // outroP: 0 during play, 0→1 on hand-off (fades everything out, lifts card).
    function render(T, cont, holdVal, outroP) {
      var A_ = A;
      var lit = enter(0, 1, 1.20, 1.55)(T);
      var veil = enter(0, 1, 0.04, 0.55)(T) * enter(1, 0, 5.50, 5.96)(T);
      veil *= (1 - outroP);

      // the app, dimmed and pushed back
      if (appEl) {
        appEl.style.filter = 'blur(' + (2.6 * veil).toFixed(2) + 'px) saturate(' + (1 - 0.25 * veil) + ')';
        appEl.style.transform = 'scale(' + (1 - 0.012 * veil) + ')';
        appEl.style.transformOrigin = '50% 46%';
      }
      dim.style.opacity = (0.62 * veil).toFixed(3);
      halo.style.opacity = veil.toFixed(3);
      halo.style.background = 'radial-gradient(58% 52% at 50% 46%, ' + hexA(A_, 0.20 * (0.35 + 0.65 * lit)) + ' 0%, rgba(0,0,0,0) 62%)';

      // room-scale light washes
      for (var i = 0; i < 2; i++) {
        var p = draw(-0.25, 1.25, 1.10 + i * 0.22, 2.35 + i * 0.22)(T);
        var aa = Math.sin(Math.max(0, Math.min(1, (p + 0.25) / 1.5)) * Math.PI);
        var w = washes[i];
        w.style.left = (p * 100) + '%';
        w.style.top = i ? '18%' : '58%';
        w.style.background = 'linear-gradient(90deg,rgba(0,0,0,0),' + hexA(A_, 0.9) + ',rgba(0,0,0,0))';
        w.style.opacity = (aa * 0.5 * veil).toFixed(3);
      }

      // card geometry
      var cardW = interp([1.00, 1.42, 1.95, 2.58], [430, 470, 470, 620], E.inOutCubic)(T);
      var cardH = interp([1.00, 1.42, 1.95, 2.58], [186, 190, 190, 302], E.inOutCubic)(T);
      var cardOp = enter(0, 1, 0.10, 0.50)(T) * enter(1, 0, 5.56, 5.98)(T) * (1 - outroP);
      var cardScale = pop(0.94, 1, 0.10, 0.74)(T) + enter(0, 0.008, 3.50, 5.40)(T) + 0.035 * outroP;
      var drift = Math.sin(cont * 1.05) * 2.2;
      var liftY = -26 * outroP;
      var fit = Math.min(1, (window.innerWidth - 48) / cardW, (window.innerHeight - 48) / cardH);

      card.style.width = cardW + 'px';
      card.style.height = cardH + 'px';
      card.style.transform = 'translate(-50%,-50%) translateY(' + (drift + liftY) + 'px) scale(' + (cardScale * fit) + ')';
      card.style.opacity = cardOp.toFixed(3);
      card.style.background = 'linear-gradient(165deg, color-mix(in srgb, var(--gw-surface) 70%, transparent) 0%, var(--gw-n900) 46%, color-mix(in srgb, var(--gw-bg) 80%, transparent) 100%), var(--gw-n900)';
      card.style.boxShadow = '0 0 0 1px ' + hexA(A_, 0.10 + 0.22 * lit) + ', 0 24px 60px rgba(0,0,0,0.7), 0 0 ' + (40 * lit) + 'px ' + hexA(A_, 0.14 * lit);

      // LED strip ignition
      var sweep = draw(0, 1, 1.18, 2.10)(T);
      var breathe = 0.86 + 0.14 * Math.sin(cont * 2.4);
      var clip = 'inset(0 ' + ((1 - sweep) * 100) + '% 0 0)';
      stripGrad.style.clipPath = clip; stripGrad.style.opacity = breathe.toFixed(3);
      stripGlow.style.clipPath = clip; stripGlow.style.opacity = (0.5 * lit * breathe).toFixed(3);
      if (sweep > 0.001 && sweep < 0.999) {
        stripDot.style.display = 'block';
        stripDot.style.left = (sweep * 100) + '%';
        stripDot.style.boxShadow = '0 0 18px 6px ' + hexA(A_, 0.85);
      } else { stripDot.style.display = 'none'; }
      if (holdVal >= 0.001 && holdVal <= 0.999) {
        holdBar.style.display = 'block';
        holdBar.style.left = (holdVal * 100) + '%';
      } else { holdBar.style.display = 'none'; }

      // verification mark (draws, then migrates to a top-left badge)
      var ringPP = draw(0, 1, 0.26, 0.94)(T);
      var checkP = draw(0, 1, 0.70, 1.12)(T);
      var ringSize = interp([1.78, 2.28], [66, 34], E.inOutCubic)(T);
      var ringCX = interp([1.78, 2.28], [cardW / 2, 61], E.inOutCubic)(T);
      var ringCY = interp([1.78, 2.28], [76, 62], E.inOutCubic)(T);
      ringWrap.style.left = (ringCX - ringSize / 2) + 'px';
      ringWrap.style.top = (ringCY - ringSize / 2) + 'px';
      ringWrap.style.width = ringSize + 'px';
      ringWrap.style.height = ringSize + 'px';
      ringSvg.setAttribute('width', ringSize);
      ringSvg.setAttribute('height', ringSize);
      ringC.style.strokeDashoffset = (276.5 * (1 - ringPP)).toFixed(2);
      ringC.style.filter = 'drop-shadow(0 0 ' + (6 * lit) + 'px ' + hexA(A_, 0.7) + ')';
      ringP.style.strokeDashoffset = (66 * (1 - checkP)).toFixed(2);

      // copy
      var verifiedOp = enter(0, 1, 0.92, 1.24)(T) * enter(1, 0, 1.74, 2.02)(T);
      verified.style.opacity = (verifiedOp * (1 - outroP)).toFixed(3);
      kicker.style.opacity = (enter(0, 1, 2.16, 2.46)(T) * (1 - outroP)).toFixed(3);
      // Wipe is horizontal only; extend the clip past the line vertically so
      // descenders (the "g" in "grid") aren't cut by the tight line box.
      head.style.clipPath = 'inset(-0.1em ' + draw(100, 0, 2.12, 2.72)(T) + '% -0.35em 0)';
      head.style.transform = 'translateY(' + enter(12, 0, 2.12, 2.72)(T) + 'px)';
      head.style.width = (cardW - 88) + 'px';
      head.style.opacity = (1 - outroP).toFixed(3);
      sub.style.opacity = (enter(0, 1, 2.54, 2.92)(T) * (1 - outroP)).toFixed(3);
      sub.style.transform = 'translateY(' + enter(8, 0, 2.54, 2.92)(T) + 'px)';
      sub.style.width = Math.min(470, cardW - 88) + 'px';

      // action
      // Controls are interactive/tabbable only while visible — a hidden control
      // must not take clicks or a Tab stop.
      var op = enter(0, 1, 2.80, 3.14)(T) * (1 - outroP);
      btnPrimary.style.opacity = op.toFixed(3);
      btnPrimary.style.transform = 'translateY(' + enter(10, 0, 2.80, 3.14)(T) + 'px)';
      btnPrimary.style.pointerEvents = op > 0.6 ? 'auto' : 'none';
      btnPrimary.tabIndex = op > 0.6 ? 0 : -1;
      var skOp = 0.9 * enter(0, 1, 1.0, 1.6)(T) * (1 - outroP);
      skip.style.opacity = skOp.toFixed(3);
      skip.style.pointerEvents = skOp > 0.5 ? 'auto' : 'none';
      skip.tabIndex = skOp > 0.5 ? 0 : -1;
    }

    // ── run loop ──────────────────────────────────────────────────────────────
    var phase = 'intro', raf = 0, t0 = 0, outroStart = 0, contAtOutro = 0, pending = null;

    function cleanup() {
      if (raf) cancelAnimationFrame(raf);
      if (appEl) { appEl.style.filter = savedFilter; appEl.style.transform = savedTransform; appEl.style.transformOrigin = savedOrigin; }
      if (root.parentNode) root.parentNode.removeChild(root);
      window.removeEventListener('keydown', onKey);
      running = false;
      if (pending) { var p = pending; pending = null; try { p(); } catch (e) {} }
    }
    function beginOutro(action) {
      if (phase === 'outro' || phase === 'done') return;
      pending = action || null;
      if (reduce) { cleanup(); phase = 'done'; return; }
      phase = 'outro'; outroStart = performance.now();
      contAtOutro = (outroStart - t0) / 1000;
    }
    function frame(now) {
      raf = requestAnimationFrame(frame);   // reschedule FIRST — a render throw must not kill the loop
      if (!t0) t0 = now;                     // anchor to the first real frame, not mount time
      try {
        var el = (now - t0) / 1000;
        if (phase === 'intro') {
          var T = Math.min(el, INTRO_END);
          render(T, el, draw(0, 1, 3.60, 5.40)(T), 0);
          if (el >= INTRO_END) phase = 'hold';
        } else if (phase === 'hold') {
          var hc = (el - INTRO_END) % 2.6;              // slow highlight travels the strip on a loop
          var hv = hc < 1.8 ? hc / 1.8 : -1;
          render(INTRO_END, el, hv, 0);
        } else if (phase === 'outro') {
          var op = Math.min(1, (now - outroStart) / 1000 / OUTRO_DUR);
          render(INTRO_END, contAtOutro + (now - outroStart) / 1000, -1, E.inCubic(op));
          if (op >= 1) { phase = 'done'; cleanup(); }
        }
      } catch (err) {
        if (window.console) console.error('[welcome] frame error', err);
        phase = 'done'; cleanup();           // tear down rather than lock the app behind the overlay
      }
    }

    // ── actions ───────────────────────────────────────────────────────────────
    function onKey(e) { if (e.key === 'Escape') { e.preventDefault(); beginOutro(opts.onEnter); } }
    btnPrimary.addEventListener('click', function () { beginOutro(opts.onEnter); });
    skip.addEventListener('click', function () { beginOutro(opts.onEnter); });
    window.addEventListener('keydown', onKey);

    if (reduce) {
      // Static settled state, no motion — the user reads it and chooses. Render
      // once, then swap the heavy blur for a light static dim (blur is GPU-costly
      // and pointless without the push-back animation to justify it).
      render(INTRO_END, 0, -1, 0);
      if (appEl) appEl.style.filter = 'saturate(0.9)';
      dim.style.opacity = '0.66';
      skip.style.opacity = '0.9';
      skip.style.pointerEvents = 'auto'; skip.tabIndex = 0;
      btnPrimary.focus();
    } else {
      root.focus();   // move focus into the dialog so Tab/AT stay inside it
      raf = requestAnimationFrame(frame);
    }
  }

  window.playWelcome = playWelcome;
})();
