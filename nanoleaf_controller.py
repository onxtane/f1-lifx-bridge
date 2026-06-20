"""
Nanoleaf integration for F1 LIFX Bridge.

Provides:
  - NanoleafController  — mirrors LocalLifxController so effects fire on
                          LIFX and Nanoleaf side-by-side with no changes to
                          F1 packet handling logic.
  - discover_nanoleaf() — SSDP-based device discovery.
  - load/save helpers   — nanoleaf_settings.json persistence.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

try:
    import requests as _requests
except ImportError:
    _requests = None

try:
    from nanoleafapi import Nanoleaf
    try:
        from nanoleafapi import discover_devices as _discover_devices
    except ImportError:
        _discover_devices = None
    _NANOLEAF_AVAILABLE = True
except ImportError:
    Nanoleaf = None
    _discover_devices = None
    _NANOLEAF_AVAILABLE = False

if getattr(sys, 'frozen', False):
    _BASE_DIR = Path(sys.executable).parent
else:
    _BASE_DIR = Path(__file__).resolve().parent
NANOLEAF_SETTINGS_FILE = str(_BASE_DIR / "nanoleaf_settings.json")

# Map firmware model codes → human-readable product names.
NANOLEAF_MODELS: dict[str, str] = {
    "NL22": "Light Panels",
    "NL29": "Canvas",
    "NL42": "Shapes",
    "NL45": "Shapes",
    "NL47": "Shapes",
    "NL52": "Elements",
    "NL55": "Lines",
    "NL59": "Skylight",
    "NL64": "Shapes Hexagons",
    "NL67": "Shapes Mini Triangles",
    "NL69": "Lines Square",
}


# ── Colour conversion ────────────────────────────────────────────────────────

def _hsbk_to_rgb(h, s, b):
    """Convert LIFX HSBK (0-65535 per channel) to RGB (0-255)."""
    h_f = (h / 65535.0) * 360.0
    s_f = s / 65535.0
    b_f = b / 65535.0

    c = b_f * s_f
    x = c * (1.0 - abs((h_f / 60.0) % 2.0 - 1.0))
    m = b_f - c

    if h_f < 60:
        r1, g1, b1 = c, x, 0.0
    elif h_f < 120:
        r1, g1, b1 = x, c, 0.0
    elif h_f < 180:
        r1, g1, b1 = 0.0, c, x
    elif h_f < 240:
        r1, g1, b1 = 0.0, x, c
    elif h_f < 300:
        r1, g1, b1 = x, 0.0, c
    else:
        r1, g1, b1 = c, 0.0, x

    return (
        max(0, min(255, int((r1 + m) * 255))),
        max(0, min(255, int((g1 + m) * 255))),
        max(0, min(255, int((b1 + m) * 255))),
    )


# ── Settings helpers ─────────────────────────────────────────────────────────

def load_nanoleaf_settings() -> dict:
    """Load nanoleaf_settings.json. Returns {} if missing or unreadable."""
    if not os.path.exists(NANOLEAF_SETTINGS_FILE):
        return {}
    try:
        with open(NANOLEAF_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_nanoleaf_settings(data: dict):
    try:
        with open(NANOLEAF_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as exc:
        print(f"[NANOLEAF] Could not save settings: {exc}", flush=True)


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_nanoleaf(timeout: int = 5) -> list:
    """
    Discover Nanoleaf devices via SSDP. Returns [{name, ip}, ...].
    Requires nanoleafapi to be installed.
    """
    if not _NANOLEAF_AVAILABLE or _discover_devices is None:
        return []
    try:
        raw = _discover_devices(timeout)
        if isinstance(raw, dict):
            return [{"name": name, "ip": ip} for name, ip in raw.items()]
        return []
    except Exception as exc:
        print(f"[NANOLEAF] Discovery error: {exc}", flush=True)
        return []


# ── Controller ───────────────────────────────────────────────────────────────

class NanoleafController:
    """
    Mirrors LocalLifxController's effect interface so bridge_core.py can fire
    effects on LIFX and Nanoleaf in parallel without duplicating logic.

    Lifecycle:
      1. Instantiate with ip + auth_token (from nanoleaf_settings.json).
      2. Check ._nl is not None before use (NanoleafController.try_connect handles this).
      3. Set on F1LifxBridgeCore.nanoleaf — the bridge calls _fire() which
         delegates to both self.lifx and self.nanoleaf.

    Pairing (first-time setup):
      - User holds power button on device for 5-7 s (LED pulses white).
      - Call NanoleafController.pair(ip) → returns auth_token string.
      - Save token + ip to nanoleaf_settings.json.
    """

    def __init__(self, ip: str, auth_token: str, log_callback=None):
        self.ip = ip
        self.auth_token = auth_token
        self.log_callback = log_callback

        # Brightness scaling (same semantics as LocalLifxController).
        self.brightness_min = 0
        self.brightness_max = 65535

        # Per-effect light assignment placeholder — mirrors LIFX interface.
        self.light_assignments: dict = {}
        self._current_effect_key: str | None = None

        self._effect_lock = threading.Lock()
        self._active_effect: str | None = None

        # Populated by _fetch_device_info() after connect.
        # Keys: name, model, model_name, firmware, num_panels
        self.device_info: dict = {}

        # Panel IDs and layout fetched from the device API.
        self._panel_ids: list[int] = []
        self._side_length: int = 150
        self._raw_layout: list[dict] = []
        self._diag_logged = False  # log first set_color_all call only

        # Multizone start-lights behaviour (mirrors F1LifxBridgeCore attributes).
        self.mz_startlights_direction = "ltr"  # "ltr" | "rtl"
        self.mz_startlights_mode      = "sweep"  # "sweep" | "solid"
        # Panels sorted for sweep effects — updated via update_panel_order().
        self._sweep_panel_ids: list[int] = []

        self._nl: Nanoleaf | None = None
        self._connect()

    # ── Private helpers ──────────────────────────────────────────────────────

    def _connect(self):
        if not _NANOLEAF_AVAILABLE:
            self._log("[NANOLEAF] nanoleafapi not installed — Nanoleaf disabled.")
            return
        try:
            self._nl = Nanoleaf(self.ip, self.auth_token)
            self._fetch_device_info()
            name = self.device_info.get("name", self.ip)
            model_name = self.device_info.get("model_name", "Unknown")
            self._log(f"[NANOLEAF] Connected: {name} ({model_name}) @ {self.ip}")
        except Exception as exc:
            self._log(f"[NANOLEAF] Connection failed ({self.ip}): {exc}")
            self._nl = None

    def _fetch_device_info(self):
        """
        Hit GET /api/v1/<token>/ to detect product type and panel count.
        Populates self.device_info with: name, model, model_name, firmware, num_panels.
        """
        if _requests is None:
            return
        try:
            url = f"http://{self.ip}:16021/api/v1/{self.auth_token}/"
            resp = _requests.get(url, timeout=4)
            resp.raise_for_status()
            data = resp.json()

            model_code = data.get("model", "")
            layout = data.get("panelLayout", {}).get("layout", {})
            num_panels = layout.get("numPanels", 0)
            position_data = layout.get("positionData", [])
            # shapeType 12 = Rhythm module (not a light panel — exclude it).
            light_panels = [p for p in position_data if p.get("shapeType", 0) != 12]
            self._side_length = layout.get("sideLength", 150)
            self._panel_ids = [p["panelId"] for p in light_panels]
            self._sweep_panel_ids = [p["panelId"] for p in sorted(light_panels, key=lambda p: p["x"])]
            self._raw_layout = [
                {
                    "panelId":   p["panelId"],
                    "x":         p["x"],
                    "y":         p["y"],
                    "o":         p.get("o", 0),
                    "shapeType": p.get("shapeType", 0),
                }
                for p in light_panels
            ]

            self.device_info = {
                "name":        data.get("name", "Nanoleaf"),
                "model":       model_code,
                "model_name":  NANOLEAF_MODELS.get(model_code, f"Nanoleaf ({model_code})"),
                "firmware":    data.get("firmwareVersion", ""),
                "num_panels":  num_panels,
            }
            self._log(f"[NANOLEAF] Panel IDs ({len(self._panel_ids)}): {self._panel_ids}")
        except Exception as exc:
            self._log(f"[NANOLEAF] Device info fetch failed: {exc}")

    def get_panel_layout(self) -> dict:
        """Return raw device panel layout for rendering."""
        return {
            "sideLength": self._side_length,
            "model":      self.device_info.get("model", ""),
            "modelName":  self.device_info.get("model_name", ""),
            "panels":     [dict(p) for p in self._raw_layout],
        }

    def update_panel_order(self, panels: list[dict]):
        """Re-sort sweep order using merged (custom) panel positions.

        Called by bridge_runner after connecting, passing in the layout that
        already has custom user positions overlaid.  Panels are sorted L→R
        by X coordinate so sweep effects use real-world physical order.
        """
        if not panels:
            return
        sorted_panels = sorted(panels, key=lambda p: p["x"])
        self._sweep_panel_ids = [p["panelId"] for p in sorted_panels]

    def set_panel_colors(self, panel_rgb: list[tuple[int, int, int, int]]):
        """Send per-panel colors in one HTTP call.

        panel_rgb: list of (panelId, R, G, B) tuples, one per panel.
        Uses the /effects static write endpoint with transitionTime=0 per panel.
        """
        if self._nl is None or _requests is None or not panel_rgb:
            return
        n = len(panel_rgb)
        anim_data = f"{n} " + " ".join(f"{pid} {r} {g} {b} 0 0" for pid, r, g, b in panel_rgb)
        url = f"http://{self.ip}:16021/api/v1/{self.auth_token}/effects"
        try:
            resp = _requests.put(url, json={
                "write": {
                    "command":  "display",
                    "animType": "static",
                    "animData": anim_data,
                    "loop":     False,
                    "palette":  [],
                }
            }, timeout=2)
            if resp.status_code >= 300:
                self._log(f"[NANOLEAF] set_panel_colors failed: {resp.status_code}")
        except Exception as exc:
            self._log(f"[NANOLEAF ERROR] set_panel_colors: {exc}")

    def _log(self, msg: str):
        print(msg, flush=True)
        if self.log_callback:
            self.log_callback(msg)

    def _scale_brightness(self, b: int) -> int:
        """Scale into [brightness_min, brightness_max], same as LocalLifxController."""
        if b <= 500:
            return b
        ratio = b / 65535.0
        scaled = self.brightness_min + (self.brightness_max - self.brightness_min) * ratio
        return max(1, min(65535, int(scaled)))

    # ── Effect state ─────────────────────────────────────────────────────────

    def set_active_effect(self, effect_name: str):
        with self._effect_lock:
            self._active_effect = effect_name

    def clear_active_effect(self):
        with self._effect_lock:
            self._active_effect = None

    def is_effect_active(self, effect_name: str) -> bool:
        with self._effect_lock:
            return self._active_effect == effect_name

    # ── Core colour send ─────────────────────────────────────────────────────

    def _state_put(self, h, s, b_scaled):
        """Fallback: /state endpoint. Hue/sat may fade on some firmware."""
        nl_h = int(h / 65535.0 * 360)
        nl_s = int(s / 65535.0 * 100)
        nl_b = max(1, int(b_scaled / 65535.0 * 100))
        url = f"http://{self.ip}:16021/api/v1/{self.auth_token}/state"
        _requests.put(url, json={
            "hue":        {"value": nl_h},
            "sat":        {"value": nl_s},
            "brightness": {"value": nl_b, "duration": 0},
        }, timeout=2)

    def set_color_all(self, hsbk, duration_ms=50, stagger=True):
        """Set all Nanoleaf panels to hsbk colour.

        Attempts the /effects write+static endpoint with transitionTime=0 per panel
        for an instant snap (no hue/sat fade).  Falls back to /state if the device
        rejects the request or panel IDs are unavailable.
        """
        if self._nl is None or _requests is None:
            return
        h, s, b, k = hsbk
        b_scaled = self._scale_brightness(b)
        try:
            if self._panel_ids:
                r, g, bl = _hsbk_to_rgb(h, s, b_scaled)
                n = len(self._panel_ids)
                # animData: "<numPanels> <panelId> <R> <G> <B> <W> <transTime(100ms)> ..."
                anim_data = f"{n} " + " ".join(
                    f"{pid} {r} {g} {bl} 0 0" for pid in self._panel_ids
                )
                url = f"http://{self.ip}:16021/api/v1/{self.auth_token}/effects"
                resp = _requests.put(url, json={
                    "write": {
                        "command":  "display",
                        "animType": "static",
                        "animData": anim_data,
                        "loop":     False,
                        "palette":  [],
                    }
                }, timeout=2)
                if not self._diag_logged:
                    self._diag_logged = True
                    self._log(f"[NANOLEAF DIAG] effects/static status={resp.status_code} anim_data={anim_data[:100]}")
                    if resp.status_code >= 300:
                        self._log(f"[NANOLEAF DIAG] body={resp.text[:200]}")
                if resp.status_code < 200 or resp.status_code >= 300:
                    self._state_put(h, s, b_scaled)
            else:
                if not self._diag_logged:
                    self._diag_logged = True
                    self._log(f"[NANOLEAF DIAG] no panel IDs — using /state fallback")
                self._state_put(h, s, b_scaled)
        except Exception as exc:
            self._log(f"[NANOLEAF ERROR] set_color_all: {exc}")

    def _snap_and_wait(self, hsbk, hold_ms: int):
        """Set colour and hold for hold_ms, compensating for HTTP round-trip time."""
        t0 = time.monotonic()
        self.set_color_all(hsbk, duration_ms=0, stagger=False)
        elapsed_ms = (time.monotonic() - t0) * 1000
        remaining = (hold_ms - elapsed_ms) / 1000.0
        if remaining > 0:
            time.sleep(remaining)

    def flash_colors(self, colors, loops=3, hold_ms=200):
        """Flash through a list of HSBK colours, compensating for HTTP latency."""
        for _ in range(loops):
            for color in colors:
                self._snap_and_wait(color, hold_ms)

    # ── Effects ──────────────────────────────────────────────────────────────

    def neutral(self):
        self.clear_active_effect()
        self._current_effect_key = "neutral"
        self.set_color_all([0, 0, 50000, 4500], duration_ms=800)

    def yellow_flag(self):
        self._current_effect_key = "yellow_flag"
        if self.is_effect_active("yellow_flash"):
            return
        self.set_active_effect("yellow_flash")
        threading.Thread(target=self._yellow_flash_loop, daemon=True).start()

    def _yellow_flash_loop(self):
        yellow = [10922, 65535, 65535, 3500]
        dark   = [0, 0, 1, 3500]
        while self.is_effect_active("yellow_flash"):
            self.set_color_all(yellow, duration_ms=40, stagger=False)
            time.sleep(0.45)
            if not self.is_effect_active("yellow_flash"):
                break
            self.set_color_all(dark, duration_ms=40, stagger=False)
            time.sleep(0.45)

    def blue_flag(self):
        self.clear_active_effect()
        self._current_effect_key = "blue_flag"
        self.set_active_effect("blue_pulse")
        threading.Thread(target=self._blue_pulse_loop, daemon=True).start()

    def _blue_pulse_loop(self):
        bright = [43690, 65535, 65535, 3500]
        dim    = [43690, 65535, 8000, 3500]
        while self.is_effect_active("blue_pulse"):
            self.set_color_all(bright, duration_ms=600, stagger=False)
            for _ in range(7):
                if not self.is_effect_active("blue_pulse"):
                    return
                time.sleep(0.1)
            self.set_color_all(dim, duration_ms=600, stagger=False)
            for _ in range(7):
                if not self.is_effect_active("blue_pulse"):
                    return
                time.sleep(0.1)

    def red_flag(self):
        self.clear_active_effect()
        self._current_effect_key = "red_flag"
        self.set_active_effect("red_pulse")
        threading.Thread(target=self._red_pulse_loop, daemon=True).start()

    def _red_pulse_loop(self):
        bright = [0, 65535, 65535, 3500]
        dim    = [0, 65535, 8000, 3500]
        while self.is_effect_active("red_pulse"):
            self.set_color_all(bright, duration_ms=600, stagger=False)
            for _ in range(7):
                if not self.is_effect_active("red_pulse"):
                    return
                time.sleep(0.1)
            self.set_color_all(dim, duration_ms=600, stagger=False)
            for _ in range(7):
                if not self.is_effect_active("red_pulse"):
                    return
                time.sleep(0.1)

    def black_flag(self):
        self.clear_active_effect()
        self._current_effect_key = "black_flag"
        dark      = [0, 0, 1, 3500]
        white_dim = [0, 0, 12000, 4500]
        self.flash_colors([dark, white_dim], loops=3, hold_ms=400)
        self.set_color_all(dark, duration_ms=500, stagger=False)

    def white_warning(self):
        self.clear_active_effect()
        self._current_effect_key = "white_warning"
        white = [0, 0, 65535, 4500]
        dark  = [0, 0, 1, 3500]
        self.flash_colors([white, dark], loops=3, hold_ms=250)
        self.neutral()

    def fastest_lap(self):
        self._current_effect_key = "fastest_lap"
        purple = [54613, 65535, 65535, 3500]
        dark   = [0, 0, 1, 3500]
        self.flash_colors([purple, dark], loops=3, hold_ms=200)
        self.neutral()

    def chequered_flag(self):
        self.clear_active_effect()
        self._current_effect_key = "chequered_flag"
        white = [0, 0, 65535, 4500]
        green = [21845, 65535, 65535, 3500]
        self.flash_colors([white, green], loops=5, hold_ms=300)
        self.neutral()

    def lights_out(self):
        self.clear_active_effect()
        self._current_effect_key = "lights_out"
        green = [21845, 65535, 65535, 3500]
        dark  = [0, 0, 1, 3500]
        white = [0, 0, 50000, 4500]
        self._snap_and_wait(green, 200)
        self._snap_and_wait(dark,  150)
        self._snap_and_wait(green, 350)
        self.set_color_all(white)

    def start_lights(self, num_lights: int):
        """Sweep red panels L→R (or R→L) as start lights appear, dark otherwise."""
        self.clear_active_effect()
        self._current_effect_key = "start_lights"
        num_lights = max(0, min(5, num_lights))

        brightness_by_count = {0: 8000, 1: 16000, 2: 26000, 3: 38000, 4: 50000, 5: 65535}
        red_hsbk  = [0, 65535, brightness_by_count[num_lights], 3500]
        dark_hsbk = [0, 0, 100, 3500]

        sweep_ids = self._sweep_panel_ids or self._panel_ids
        if self.mz_startlights_mode != "sweep" or not sweep_ids:
            self.set_color_all(red_hsbk, duration_ms=40, stagger=False)
            return

        panel_count = len(sweep_ids)
        lit = max(0, min(panel_count, round(num_lights / 5 * panel_count)))

        if self.mz_startlights_direction == "rtl":
            sweep_ids = list(reversed(sweep_ids))

        red_rgb  = _hsbk_to_rgb(*red_hsbk[:3])
        dark_rgb = _hsbk_to_rgb(*dark_hsbk[:3])

        panel_rgb = []
        for i, pid in enumerate(sweep_ids):
            rgb = red_rgb if i < lit else dark_rgb
            panel_rgb.append((pid, rgb[0], rgb[1], rgb[2]))

        self.set_panel_colors(panel_rgb)

    # ── Class-level helpers ──────────────────────────────────────────────────

    @classmethod
    def try_connect(cls, ip: str, auth_token: str, log_callback=None) -> "NanoleafController | None":
        """Create a controller; returns None if the connection fails."""
        ctrl = cls(ip, auth_token, log_callback=log_callback)
        return ctrl if ctrl._nl is not None else None

    @staticmethod
    def pair(ip: str) -> "str | None":
        """
        Request a new auth token from a Nanoleaf device.

        The user must hold the power button for 5-7 seconds (until the LED
        pulses) before calling this.  Returns the auth token string or None.
        """
        if not _NANOLEAF_AVAILABLE:
            print("[NANOLEAF] nanoleafapi not installed — cannot pair.", flush=True)
            return None
        try:
            nl = Nanoleaf(ip)
            token = nl.create_auth_token()
            return token if token else None
        except Exception as exc:
            print(f"[NANOLEAF] Pairing failed: {exc}", flush=True)
            return None
