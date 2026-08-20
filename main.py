import json
import os
import sys
import threading
from pathlib import Path

# Console output must not depend on the OS codepage. A cp1252 stdout raises
# UnicodeEncodeError on any character it can't map (an arrow in a setup hint was
# enough to kill the bridge thread — #76), so force UTF-8 and never fail on an
# unmappable character. Silently skipped when the stream can't be reconfigured
# (e.g. no stdout in a windowed build).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# On Windows, use the WebView2 (edgechromium) backend — it renders via the OS's
# auto-updating WebView2 runtime, so the build ships no bundled Chromium (~15 MB
# vs ~210 MB on Qt). WebView2 is Chromium under the hood, so the same flags keep
# its compositor running while a fullscreen game occludes the window (preventing
# the blank-frame flicker on resume) — passed via WebView2's argument env var.
# macOS uses pywebview's native Cocoa/WKWebView backend; Linux keeps its default.
if sys.platform == "win32":
    os.environ.setdefault("PYWEBVIEW_GUI", "edgechromium")
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--disable-backgrounding-occluded-windows --disable-renderer-backgrounding",
    )

import webview

from bridge_runner import BridgeRunner

import replay
import runtime_check
from app_paths import BUNDLE_DIR, USER_DATA_DIR

try:
    import requests
except ImportError:  # mirrors the guarded import in hue_controller / nanoleaf_controller
    requests = None

UI_FILE = BUNDLE_DIR / "ui" / "index.html"

# Community Workshop API base. The app reaches the read API over HTTPS exactly like
# it reaches Hue/Nanoleaf — never D1 directly (D1 is only reachable inside the Pages
# Functions). Env-overridable: local wrangler in dev, the Pages domain in prod.
WORKSHOP_API_BASE = os.environ.get("GRIDGLOW_API_BASE", "https://gridglow.titanstowers.net")


class Api:
    """
    Exposed to the page as `pywebview.api.*`.

    All Python→JS updates are queued here and pulled by the JS side
    via get_pending_updates() on a 250 ms interval.  This means Python
    never calls evaluate_js, eliminating Qt compositor repaints that
    caused UI flickering.
    """

    def __init__(self):
        self._window: webview.Window | None = None
        self._queue: list = []
        self._queue_lock = threading.Lock()

        # Supabase access token for Workshop write/personal calls (set by the webview
        # on auth state change; None when signed out). Reads never use it.
        self._workshop_token: str | None = None

        # A replay runs for ~20-35s on its own thread so the UI stays live.
        self._replay_thread: threading.Thread | None = None
        self._replay_stop = threading.Event()
        self._replay_key: str | None = None

        self.runner = BridgeRunner(
            on_log=self._push_log,
            on_state_change=self._push_state,
            on_status_text=self._push_status_text,
            on_stat=self._push_stat,
            on_lights_discovered=self._push_lights,
            on_discovering=self._push_discovering,
            on_selection_changed=self._push_selection,
        )

    def set_window(self, window: webview.Window):
        self._window = window

    # ---- JS polling endpoint ----

    def get_pending_updates(self):
        """Called by JS every 250 ms. Returns queued [[fn, args], ...] and clears the queue."""
        with self._queue_lock:
            if not self._queue:
                return None
            batch = self._queue[:]
            self._queue.clear()
        return batch

    # ---- bridge control ----

    def start_bridge(self):
        self.runner.start()
        return {"ok": True}

    def stop_bridge(self):
        self.runner.stop()
        return {"ok": True}

    def discover_lights(self):
        self.runner.discover_lights()
        return {"ok": True}

    def test_selected(self):
        self.runner.test_selected()
        return {"ok": True}

    def test_multizone(self):
        self.runner.test_multizone()
        return {"ok": True}

    def trigger_effect(self, name: str):
        self.runner.trigger_effect(name)
        return {"ok": True}

    # ---- lights / group management ----

    def get_discovered_lights(self):
        return self.runner.get_discovered_lights()

    def get_workshop_capabilities(self):
        """What the Workshop compatibility banner needs about the user's real
        setup: {'brands': [...], 'has_multizone': bool}. brands is the set of
        controller types present ('lifx' | 'hue' | 'nanoleaf'); has_multizone is
        True when the user owns a device that can render zone-based effects — a
        LIFX multizone strip (zones > 0) or a Nanoleaf (its panels act as zones).
        """
        brands: set[str] = set()
        has_multizone = False
        zones = 0            # colorable sections on the multizone device (strip zones / NL panels)
        light_count = 0      # individual bulbs/devices, for the per-light view fallback
        try:
            for d in self.runner.get_discovered_lights() or []:
                t = d.get("type")
                if t:
                    brands.add(t)
                light_count += 1
                if t == "lifx" and (d.get("zones") or 0) > 0:
                    has_multizone = True
                    zones = max(zones, int(d.get("zones") or 0))
                elif t == "nanoleaf":
                    has_multizone = True
                    try:
                        zones = max(zones, len(self.runner.get_nanoleaf_layout() or []))
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            hue = self.runner.get_hue_lights()
            if hue:
                brands.add("hue")
                light_count += len(hue)
                # Hue gradient lightstrips are zoned (a ~7-point gradient).
                if any(l.get("is_gradient") for l in hue):
                    has_multizone = True
                    zones = max(zones, 7)
        except Exception:
            pass
        # A configured Nanoleaf counts even when the bridge is stopped (no live probe).
        try:
            nl = self.runner.get_nanoleaf_settings() or {}
            if nl.get("ip") and nl.get("paired"):   # `paired` is bool(auth_token)
                has_multizone = True
                brands.add("nanoleaf")
        except Exception:
            pass

        gs = {}
        try:
            gs = self.runner.get_gui_settings() or {}
        except Exception:
            pass

        if has_multizone:
            if zones <= 0:
                zones = int(gs.get("multizone_zones") or 0) or 16
            # Remember it so per-zone stays available after the bridge is stopped or
            # the strip goes offline — detection is otherwise live-only.
            try:
                if not gs.get("multizone_seen") or int(gs.get("multizone_zones") or 0) != zones:
                    self.runner.save_gui_settings({"multizone_seen": True, "multizone_zones": zones})
            except Exception:
                pass
        elif gs.get("multizone_seen"):
            has_multizone = True
            zones = int(gs.get("multizone_zones") or 0) or 16

        return {"brands": sorted(brands), "has_multizone": has_multizone,
                "zones": zones, "light_count": light_count,
                "multizone_seen": bool(gs.get("multizone_seen")) or has_multizone}

    def set_selected_lights(self, labels: list):
        self.runner.set_selected_lights(labels)
        return {"ok": True}

    def get_groups(self):
        return self.runner.get_groups()

    def save_group(self, name: str, labels: list):
        self.runner.save_group(name, labels)
        return {"ok": True}

    def delete_group(self, name: str):
        self.runner.delete_group(name)
        return {"ok": True}

    def load_group(self, name: str):
        missing = self.runner.load_group(name)
        if missing:
            labels = ", ".join(sorted(missing))
            n = len(missing)
            self._push_toast(
                f"{n} device{'s' if n != 1 else ''} not found: {labels}",
                "warning",
                4000,
            )
        return {"ok": True}

    # ---- GUI settings ----

    def get_gui_settings(self):
        return self.runner.get_gui_settings()

    def save_gui_settings(self, data: dict):
        self.runner.save_gui_settings(data)
        return {"ok": True}

    def set_enabled_events(self, names):
        self.runner.set_enabled_events(names)
        return {"ok": True}

    def set_brightness_range(self, min_pct: int, max_pct: int):
        self.runner.set_brightness_range(min_pct, max_pct)
        return {"ok": True}

    def set_stagger(self, enabled: bool, ms: int):
        self.runner.set_stagger(enabled, ms)
        return {"ok": True}

    def set_idle_state(self, color_hex: str, pulse: bool):
        self.runner.set_idle_state(color_hex, pulse)
        return {"ok": True}

    def set_forwarding(self, enabled: bool, host: str, port: int):
        self.runner.set_forwarding(enabled, host, port)
        return {"ok": True}

    def set_mz_startlights(self, direction: str, mode: str):
        self.runner.set_mz_startlights(direction, mode)
        return {"ok": True}

    def set_rpm_gradient(self, stops: list):
        self.runner.set_rpm_gradient(stops)
        return {"ok": True}

    def rpm_gradient_swatch(self, stops: list, samples: int = 24):
        return self.runner.rpm_gradient_swatch(stops, samples)

    def set_debug_timing(self, enabled: bool):
        self.runner.set_debug_timing(enabled)
        return {"ok": True}

    def set_nanoleaf_diag(self, enabled: bool):
        self.runner.set_nanoleaf_diag(enabled)
        return {"ok": True}

    # ---- Nanoleaf ----

    def get_nanoleaf_settings(self):
        return self.runner.get_nanoleaf_settings()

    def get_nanoleaf_device_info(self):
        return self.runner.get_nanoleaf_device_info()

    def save_nanoleaf_settings(self, data: dict):
        self.runner.save_nanoleaf_settings_data(data)
        return {"ok": True}

    def pair_nanoleaf(self, ip: str):
        return self.runner.pair_nanoleaf(ip)

    def discover_nanoleaf(self):
        return self.runner.discover_nanoleaf_devices()

    def set_nanoleaf_enabled(self, enabled: bool):
        self.runner.set_nanoleaf_enabled(enabled)
        return {"ok": True}

    # ---- Hue ----

    def get_hue_settings(self):
        return self.runner.get_hue_settings()

    def save_hue_settings(self, data: dict):
        self.runner.save_hue_settings_data(data)
        return {"ok": True}

    def set_hue_enabled(self, enabled: bool):
        self.runner.set_hue_enabled(enabled)
        return {"ok": True}

    def pair_hue(self, ip: str):
        return self.runner.pair_hue(ip)

    def discover_hue(self):
        return self.runner.discover_hue_devices()

    def get_hue_lights(self):
        return self.runner.get_hue_lights()

    def set_hue_diag(self, enabled: bool):
        self.runner.set_hue_diag(enabled)
        return {"ok": True}

    # ---- Community Workshop (read path) ----

    def _workshop_request(self, path, method="GET", params=None, body=None, auth=False):
        """Call {WORKSHOP_API_BASE}{path} → parsed JSON, or an {'error': ...} dict.

        Never raises into the webview bridge; the page always gets JSON back so it can
        render a friendly offline / empty / needs-login state. `auth=True` attaches the
        Supabase access token (from set_workshop_token) as a Bearer header — required by
        every write / personal-tab endpoint. Reads send no header.
        """
        if requests is None:
            return {"error": "requests_unavailable"}
        headers: dict = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            if not self._workshop_token:
                return {"error": "not_authenticated", "status": 401}
            headers["Authorization"] = "Bearer " + self._workshop_token
        try:
            resp = requests.request(
                method, f"{WORKSHOP_API_BASE}{path}",
                params=params, json=body, headers=headers, timeout=8,
            )
            if resp.status_code == 401:
                return {"error": "unauthorized", "status": 401}
            if resp.status_code == 404:
                return {"error": "not_found", "status": 404}
            if resp.status_code == 429:
                return {"error": "rate_limited", "status": 429,
                        "retry_after": resp.headers.get("Retry-After")}
            if not resp.ok:
                # 4xx/5xx we don't special-case above (e.g. a 422 validation
                # reject). Surface the server's own message — it names the exact
                # offending field — instead of a generic HTTPError string.
                detail = None
                try:
                    detail = resp.json().get("error")
                except ValueError:
                    detail = (resp.text or "").strip()[:200] or None
                return {"error": detail or f"http_{resp.status_code}",
                        "status": resp.status_code}
            try:
                return resp.json()
            except ValueError:  # empty/no-JSON body (e.g. a 204)
                return {"ok": True, "status": resp.status_code}
        except Exception as exc:  # network down, API unreachable, bad JSON, …
            return {"error": "request_failed", "detail": str(exc)}

    def workshop_list(self, game=None, sort="hot", q=None, cursor=None):
        """Browse presets. Mirrors GET /api/workshop/presets (game/sort/q/cursor)."""
        params = {"sort": sort or "hot"}
        if game:
            params["game"] = game
        if q:
            params["q"] = q
        if cursor:
            params["cursor"] = cursor
        return self._workshop_request("/api/workshop/presets", params=params)

    @staticmethod
    def _valid_preset_id(preset_id) -> bool:
        """Accept only backend-issued ids — alphanumerics, '-' and '_' (covers
        UUIDs and the ULID-style ids the API mints). Rejects empty, over-long,
        and anything with path separators or traversal segments, so a crafted id
        can't reshape the request URL."""
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        return (isinstance(preset_id, str) and 1 <= len(preset_id) <= 64
                and all(c in allowed for c in preset_id))

    def workshop_get(self, preset_id: str):
        """Fetch one preset's full detail (incl. `theme`). GET /api/workshop/presets/:id."""
        if not self._valid_preset_id(preset_id):
            return {"error": "bad_id"}
        return self._workshop_request(f"/api/workshop/presets/{preset_id}")

    def apply_workshop_preset(self, theme: dict):
        """Apply a downloaded preset's device-agnostic `theme` to the user's lights.

        A downloaded preset is just a partial gui_settings payload — loop each field
        that's present through the existing live setter so it takes effect on running
        lights (and persists), exactly like changing it in Settings. Fields map 1:1 to
        the `theme` shape documented in docs/community-workshop-backend.md §2.1.
        """
        if not isinstance(theme, dict):
            return {"ok": False, "error": "invalid_theme"}
        applied: list[str] = []
        try:
            if isinstance(theme.get("enabled_events"), list):
                self.runner.set_enabled_events(theme["enabled_events"])
                applied.append("enabled_events")
            br = theme.get("brightness_range")
            if isinstance(br, dict) and "min_pct" in br and "max_pct" in br:
                self.runner.set_brightness_range(int(br["min_pct"]), int(br["max_pct"]))
                applied.append("brightness_range")
            st = theme.get("stagger")
            if isinstance(st, dict) and "enabled" in st:
                self.runner.set_stagger(bool(st["enabled"]), int(st.get("ms", 0)))
                applied.append("stagger")
            idle = theme.get("idle_state")
            if isinstance(idle, dict) and "color_hex" in idle:
                self.runner.set_idle_state(idle["color_hex"], bool(idle.get("pulse", False)))
                applied.append("idle_state")
            mz = theme.get("mz_startlights")
            if isinstance(mz, dict) and "direction" in mz and "mode" in mz:
                self.runner.set_mz_startlights(mz["direction"], mz["mode"])
                applied.append("mz_startlights")
            if isinstance(theme.get("rpm_gradient"), list):
                self.runner.set_rpm_gradient(theme["rpm_gradient"])
                applied.append("rpm_gradient")
            if isinstance(theme.get("curves"), dict):
                self.runner.set_curves(theme["curves"])
                applied.append("curves")
        except Exception as exc:
            return {"ok": False, "error": str(exc), "applied": applied}
        # Persist the applied theme to gui_settings so it survives a restart and the
        # Effects page reflects it — the live setters above only touch the running
        # bridge. Mirrors what each Effects control does (set + save_gui_settings).
        try:
            flat = self._theme_to_gui_settings(theme)
        except Exception as exc:
            # Flattening the theme failed — that's a malformed/unsupported theme,
            # not a disk problem, so surface it as a failure (not a persist warning).
            print(f"[workshop] theme conversion failed: {exc}", flush=True)
            return {"ok": False, "error": "theme_conversion_failed",
                    "detail": str(exc), "applied": applied}
        try:
            if flat:
                self.runner.save_gui_settings(flat)
        except Exception as exc:
            # The lights already changed (live apply above), so this isn't a hard
            # failure — but the preset won't survive a restart. Report success with
            # a warning rather than swallowing it silently or claiming an outright
            # failure the user can plainly see didn't happen.
            print(f"[workshop] apply persisted failed: {exc}", flush=True)
            return {"ok": True, "applied": applied, "persist_warning": str(exc)}
        return {"ok": True, "applied": applied}

    @staticmethod
    def _theme_to_gui_settings(theme: dict) -> dict:
        """Flatten a preset `theme` back into the app's gui_settings keys (the inverse
        of _gui_settings_to_theme) so an applied preset persists and shows in the UI."""
        gs: dict = {}
        if isinstance(theme.get("enabled_events"), list):
            gs["enabled_events"] = theme["enabled_events"]
        br = theme.get("brightness_range")
        if isinstance(br, dict) and "min_pct" in br and "max_pct" in br:
            gs["brightness_min"] = int(br["min_pct"])
            gs["brightness_max"] = int(br["max_pct"])
        st = theme.get("stagger")
        if isinstance(st, dict) and "enabled" in st:
            gs["stagger_enabled"] = bool(st["enabled"])
            gs["stagger_ms"] = int(st.get("ms", 0) or 0)
        idle = theme.get("idle_state")
        if isinstance(idle, dict) and "color_hex" in idle:
            gs["idle_color"] = idle["color_hex"]
            gs["idle_pulse"] = bool(idle.get("pulse", False))
        mz = theme.get("mz_startlights")
        if isinstance(mz, dict) and "direction" in mz and "mode" in mz:
            gs["mz_startlights_direction"] = mz["direction"]
            gs["mz_startlights_mode"] = mz["mode"]
        if isinstance(theme.get("rpm_gradient"), list):
            gs["rpm_gradient"] = theme["rpm_gradient"]
        if isinstance(theme.get("curves"), dict):
            gs["curves"] = theme["curves"]
        if isinstance(theme.get("effect_colors"), dict):
            gs["effect_colors"] = theme["effect_colors"]
        return gs

    # ---- Community Workshop (write path — all require a Supabase login) ----

    def set_workshop_token(self, access_token):
        """Store the Supabase access token (None clears it on sign-out). The webview
        pushes this on auth state change; write / personal calls send it as Bearer."""
        self._workshop_token = access_token or None
        return {"ok": True}

    def workshop_personal(self, kind):
        """Personal tabs. kind: 'mine' | 'liked' | 'downloaded' → GET ?<kind>=1 (Bearer)."""
        if kind not in ("mine", "liked", "downloaded"):
            return {"error": "bad_kind"}
        return self._workshop_request("/api/workshop/presets", params={kind: 1}, auth=True)

    def workshop_download(self, preset_id):
        """POST /:id/download → returns the preset's theme and records the download."""
        if not self._valid_preset_id(preset_id):
            return {"error": "bad_id"}
        return self._workshop_request(
            f"/api/workshop/presets/{preset_id}/download", method="POST", auth=True)

    def workshop_like(self, preset_id):
        """POST /:id/like → toggle like."""
        if not self._valid_preset_id(preset_id):
            return {"error": "bad_id"}
        return self._workshop_request(
            f"/api/workshop/presets/{preset_id}/like", method="POST", auth=True)

    def workshop_rate(self, preset_id, stars):
        """POST /:id/rate {stars:1-5}."""
        if not self._valid_preset_id(preset_id):
            return {"error": "bad_id"}
        try:
            stars = int(stars)
        except (TypeError, ValueError):
            return {"error": "bad_stars"}
        if not (1 <= stars <= 5):   # contract is 1–5; reject 0, 6, negatives
            return {"error": "bad_stars"}
        return self._workshop_request(
            f"/api/workshop/presets/{preset_id}/rate",
            method="POST", body={"stars": stars}, auth=True)

    def workshop_delete(self, preset_id):
        """DELETE /:id (owner only)."""
        if not self._valid_preset_id(preset_id):
            return {"error": "bad_id"}
        return self._workshop_request(
            f"/api/workshop/presets/{preset_id}", method="DELETE", auth=True)

    def workshop_upload(self, meta):
        """POST /presets — publish the user's CURRENT setup as a preset.

        `meta` carries the presentation fields from the upload form
        ({title, description, game, visibility, tags, devices}); the device-agnostic
        `theme` is built here from the live gui_settings (the reverse of
        apply_workshop_preset). This is the real-setup upload the mockup faked with a
        SAMPLE_THEME.
        """
        if not isinstance(meta, dict):
            return {"error": "bad_payload"}
        body = {
            "title": (meta.get("title") or "Untitled preset"),
            "description": meta.get("description", ""),
            "game": meta.get("game", ""),
            "visibility": meta.get("visibility", "public"),
            "tags": meta.get("tags", []),
            "devices": meta.get("devices", []),
            "gridglow_preset": 1,
            "app_min_version": "0.10.0",
            "theme": self._gui_settings_to_theme(self.runner.get_gui_settings() or {}),
        }
        return self._workshop_request(
            "/api/workshop/presets", method="POST", body=body, auth=True)

    # Event keys the Workshop backend accepts (mirrors _validate.js KNOWN_EVENTS).
    # The app has extra internal keys (lights_out / white_warning / neutral) that a
    # shared, device-agnostic preset must not carry — the backend 422s on them.
    _WORKSHOP_EVENTS = frozenset({
        "start_lights", "fastest_lap", "sector_status", "rpm_meter",
        "red_flag", "yellow_flag", "blue_flag", "black_flag", "chequered_flag",
    })

    # Effects that carry custom colours (Effect Customization). neutral = Idle
    # Color and rpm_meter = gradient are shared through their own theme fields.
    _EFFECT_COLOR_KEYS = frozenset({
        "start_lights", "lights_out", "yellow_flag", "blue_flag", "red_flag",
        "fastest_lap", "chequered_flag", "white_warning", "crash",
    })

    @classmethod
    def _gui_settings_to_theme(cls, gs: dict) -> dict:
        """Map the app's flat gui_settings keys → the backend `theme` shape (the inverse
        of apply_workshop_preset). Only includes fields that are present."""
        theme: dict = {}
        if isinstance(gs.get("enabled_events"), list):
            # Drop any app-internal keys the backend doesn't know, so the upload
            # passes validation instead of 422-ing on e.g. "lights_out".
            evs = [e for e in gs["enabled_events"] if e in cls._WORKSHOP_EVENTS]
            if evs:
                theme["enabled_events"] = evs
        if "brightness_min" in gs or "brightness_max" in gs:
            theme["brightness_range"] = {
                "min_pct": int(gs.get("brightness_min", 0)),
                "max_pct": int(gs.get("brightness_max", 100)),
            }
        if "stagger_enabled" in gs or "stagger_ms" in gs:
            theme["stagger"] = {
                "enabled": bool(gs.get("stagger_enabled", False)),
                "ms": int(gs.get("stagger_ms", 0) or 0),
            }
        idle_hex = cls._norm_hex(gs.get("idle_color"))
        if idle_hex:
            theme["idle_state"] = {
                "color_hex": idle_hex,
                "pulse": bool(gs.get("idle_pulse", False)),
            }
        if gs.get("mz_startlights_direction") or gs.get("mz_startlights_mode"):
            theme["mz_startlights"] = {
                "direction": gs.get("mz_startlights_direction", "ltr"),
                "mode": gs.get("mz_startlights_mode", "sweep"),
            }
        if isinstance(gs.get("rpm_gradient"), list):
            stops = [cls._norm_hex(c) for c in gs["rpm_gradient"]]
            stops = [c for c in stops if c][:12]
            if stops:
                theme["rpm_gradient"] = stops
        if isinstance(gs.get("curves"), dict):
            theme["curves"] = gs["curves"]
        ec = cls._sanitize_effect_colors(gs.get("effect_colors"))
        if ec:
            theme["effect_colors"] = ec
        return theme

    @classmethod
    def _sanitize_effect_colors(cls, ec) -> dict:
        """Device-agnostic effect colours for sharing: keep the Sync-All colours and
        per-zone stops (both portable) and drop per-light — it's keyed by the user's
        own light labels, which are device-specific and rejected by the validator.
        A per-light effect collapses to its Sync-All colour."""
        out: dict = {}
        if not isinstance(ec, dict):
            return out
        for key, conf in ec.items():
            if key not in cls._EFFECT_COLOR_KEYS or not isinstance(conf, dict):
                continue
            entry: dict = {}
            colors = {}
            for slot, hx in (conf.get("colors") or {}).items():
                nh = cls._norm_hex(hx)
                if nh and isinstance(slot, str) and slot in ("main", "a", "b"):
                    colors[slot] = nh
            if colors:
                entry["colors"] = colors
            pz = conf.get("per_zone")
            if isinstance(pz, list):
                stops = [cls._norm_hex(c) for c in pz]
                stops = [c for c in stops if c][:96]
                if stops:
                    entry["per_zone"] = stops
            if not (entry.get("colors") or entry.get("per_zone")):
                continue
            entry["mode"] = "per_zone" if (conf.get("mode") == "per_zone" and entry.get("per_zone")) else "all"
            out[key] = entry
        return out

    @staticmethod
    def _norm_hex(v) -> "str | None":
        """Coerce a colour to the strict #RRGGBB the backend validator wants, or
        None if it can't. Accepts '#RGB', 'RGB', 'RRGGBB', '#RRGGBB' (any case)."""
        if not isinstance(v, str):
            return None
        s = v.strip().lstrip("#")
        if len(s) == 3 and all(c in "0123456789abcdefABCDEF" for c in s):
            s = "".join(c * 2 for c in s)
        if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
            return "#" + s.lower()
        return None

    def get_nanoleaf_layout(self):
        return self.runner.get_nanoleaf_layout()

    def save_nanoleaf_layout(self, panels: list):
        self.runner.save_nanoleaf_layout(panels)
        return {"ok": True}

    def reset_nanoleaf_layout(self):
        return self.runner.reset_nanoleaf_layout()

    def set_mini_mode(self, mini: bool):
        if self._window is None:
            return {"ok": False}
        if mini:
            self._window.resize(380, 100)
        else:
            self._window.resize(1320, 860)
        return {"ok": True}

    def copy_to_clipboard(self, text: str):
        if sys.platform == "win32":
            import ctypes
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            encoded = (text + "\0").encode("utf-16-le")
            kernel32 = ctypes.windll.kernel32
            user32   = ctypes.windll.user32
            kernel32.GlobalAlloc.restype       = ctypes.c_void_p
            kernel32.GlobalAlloc.argtypes      = [ctypes.c_uint, ctypes.c_size_t]
            kernel32.GlobalLock.restype        = ctypes.c_void_p
            kernel32.GlobalLock.argtypes       = [ctypes.c_void_p]
            kernel32.GlobalUnlock.argtypes     = [ctypes.c_void_p]
            kernel32.GlobalFree.argtypes       = [ctypes.c_void_p]
            user32.SetClipboardData.argtypes   = [ctypes.c_uint, ctypes.c_void_p]
            hMem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            if not hMem:
                return {"ok": False}
            ptr = kernel32.GlobalLock(hMem)
            if not ptr:
                kernel32.GlobalFree(hMem)
                return {"ok": False}
            ctypes.memmove(ptr, encoded, len(encoded))
            kernel32.GlobalUnlock(hMem)
            user32.OpenClipboard(0)
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, hMem)
            user32.CloseClipboard()
        return {"ok": True}

    def get_lan_interfaces(self):
        return self.runner.get_lan_interfaces()

    def set_listen_address(self, ip: str, port: int):
        self.runner.set_listen_address(ip, int(port))
        return {"ok": True}

    def set_game_mode(self, mode: str):
        self.runner.set_game_mode(mode)
        return {"ok": True}

    def install_wrc_config(self, port=None):
        return self.runner.install_wrc_config(port)

    # ---- developer / self-test (hidden unless dev mode) ----

    def is_dev_mode(self) -> bool:
        """Dev mode is on when running from source, or when GRIDGLOW_DEV=1.

        Gates the in-app 'Run self-tests' button so end users of the frozen .exe
        never see it (and the tests/ folder is never bundled into the build).
        """
        return (not getattr(sys, "frozen", False)
                or os.environ.get("GRIDGLOW_DEV") == "1")

    def run_self_tests(self) -> dict:
        """Run the tests/ unittest suite in-process and return a result summary."""
        import io
        import contextlib
        import unittest

        tests_dir = BUNDLE_DIR / "tests"
        if not tests_dir.is_dir():
            return {"ok": False, "total": 0, "failures": 0, "errors": 0,
                    "output": "tests/ directory not found — this is a dev-only "
                              "feature and tests are not bundled into the build."}

        buf = io.StringIO()
        try:
            suite = unittest.TestLoader().discover(
                str(tests_dir), top_level_dir=str(BUNDLE_DIR))
            runner = unittest.TextTestRunner(stream=buf, verbosity=2)
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                result = runner.run(suite)
            return {
                "ok": result.wasSuccessful(),
                "total": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "output": buf.getvalue(),
            }
        except Exception as exc:
            return {"ok": False, "total": 0, "failures": 0, "errors": 0,
                    "output": f"{buf.getvalue()}\n\nRunner error: {exc}"}

    # ---- effect replays (Settings -> Advanced) ----

    def list_replays(self) -> list:
        """The replays on offer. replay.py owns the list so the UI can't drift."""
        return [{"key": r.key, "label": r.label, "blurb": r.blurb,
                 "needs": r.needs, "seconds": r.seconds} for r in replay.REPLAYS]

    def get_replay_state(self) -> dict:
        running = self._replay_thread is not None and self._replay_thread.is_alive()
        return {"running": running, "key": self._replay_key if running else None}

    def run_replay(self, key: str) -> dict:
        """Start one replay on a background thread. Returns why not, if not."""
        if key not in replay.BY_KEY:
            return {"ok": False, "error": f"Unknown replay: {key}"}
        if self.get_replay_state()["running"]:
            return {"ok": False, "error": "A replay is already running."}

        target = self.runner.get_replay_target()
        if not target.get("ready"):
            return {"ok": False, "error": target.get("reason", "Not ready.")}

        self._replay_stop.clear()
        self._replay_key = key

        def worker():
            try:
                replay.run(key, target["host"], target["port"],
                           self._push_log, self._replay_stop.is_set)
            except Exception as exc:
                # Surfaces as an error banner (#73) rather than dying quietly on
                # a thread nobody is watching.
                self._push_log(f"[REPLAY ERROR] {replay.BY_KEY[key].label}: {exc}")
            finally:
                self._enqueue("setReplayRunning", False, key)

        self._replay_thread = threading.Thread(target=worker, daemon=True)
        self._replay_thread.start()
        self._enqueue("setReplayRunning", True, key)
        return {"ok": True, "seconds": replay.BY_KEY[key].seconds}

    def stop_replay(self) -> dict:
        self._replay_stop.set()
        return {"ok": True}

    def set_light_assignments(self, data: dict):
        self.runner.set_light_assignments(data)
        return {"ok": True}

    def identify_light(self, label: str):
        self.runner.identify_light(label)
        return {"ok": True}

    # ---- internal: enqueue updates for JS to poll ----

    def _enqueue(self, fn_name: str, *args):
        with self._queue_lock:
            self._queue.append([fn_name, list(args)])

    def _push_log(self, line: str):
        self._enqueue("appendLog", line)

    def _push_toast(self, message: str, type: str = "success", duration_ms: int = 2200):
        self._enqueue("showToast", message, type, duration_ms)

    def _push_stat(self, key: str, value: str):
        self._enqueue("setStat", key, value)

    def _push_state(self, running: bool):
        self._enqueue("setBridgeRunning", running)

    def _push_status_text(self, text: str):
        self._enqueue("setStatusText", text)

    def _push_lights(self, lights: list):
        self._enqueue("setLights", lights)

    def _push_discovering(self, active: bool):
        self._enqueue("setDiscovering", active)

    def _push_selection(self, labels: list):
        self._enqueue("setActiveLabels", labels)


def main():
    # Before pywebview touches the rendering stack: if WebView2 or .NET is
    # missing it neither renders nor fails usefully, so say what's wrong while we
    # still can (#72).
    if not runtime_check.verify_or_explain():
        return

    api = Api()

    window = webview.create_window(
        "GridGlow",
        url=UI_FILE.as_uri(),
        js_api=api,
        width=1320,
        height=860,
        min_size=(320, 80),
        background_color="#0b1020",
    )
    api.set_window(window)

    storage = USER_DATA_DIR / "webview_storage"
    webview.start(private_mode=False, storage_path=str(storage))


if __name__ == "__main__":
    main()
