"""
Philips Hue integration for GridGlow.

Provides:
  - HueController     — mirrors NanoleafController so effects fire on
                        LIFX, Nanoleaf, and Hue side-by-side with no
                        changes to F1 packet handling logic.
  - discover_hue_bridge() — mDNS (zeroconf) + broker fallback discovery.
  - load/save helpers — hue_settings.json persistence.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

try:
    import requests as _requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    _REQUESTS_AVAILABLE = True
except ImportError:
    _requests = None
    _REQUESTS_AVAILABLE = False

try:
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    _ZEROCONF_AVAILABLE = True
except ImportError:
    _ZEROCONF_AVAILABLE = False

if getattr(sys, 'frozen', False):
    _BASE_DIR = Path(sys.executable).parent
else:
    _BASE_DIR = Path(__file__).resolve().parent

HUE_SETTINGS_FILE = str(_BASE_DIR / "hue_settings.json")

# CLIP v2 base path
_CLIP = "/clip/v2/resource"


# ── Colour conversion ────────────────────────────────────────────────────────

def _hsbk_to_rgb(h: int, s: int, b: int) -> tuple[int, int, int]:
    """Convert LIFX HSBK (0-65535 per channel) to RGB (0-255)."""
    h_f = (h / 65535.0) * 360.0
    s_f = s / 65535.0
    b_f = b / 65535.0
    c = b_f * s_f
    x = c * (1.0 - abs((h_f / 60.0) % 2.0 - 1.0))
    m = b_f - c
    if h_f < 60:   r1, g1, b1 = c, x, 0.0
    elif h_f < 120: r1, g1, b1 = x, c, 0.0
    elif h_f < 180: r1, g1, b1 = 0.0, c, x
    elif h_f < 240: r1, g1, b1 = 0.0, x, c
    elif h_f < 300: r1, g1, b1 = x, 0.0, c
    else:           r1, g1, b1 = c, 0.0, x
    return (
        max(0, min(255, int((r1 + m) * 255))),
        max(0, min(255, int((g1 + m) * 255))),
        max(0, min(255, int((b1 + m) * 255))),
    )


def _rgb_to_xy(r: int, g: int, b: int) -> tuple[float, float]:
    """Convert sRGB (0-255) to CIE 1931 xy for Hue CLIP v2."""
    r_f = r / 255.0
    g_f = g / 255.0
    b_f = b / 255.0

    # Gamma correction (sRGB)
    r_f = pow((r_f + 0.055) / 1.055, 2.4) if r_f > 0.04045 else r_f / 12.92
    g_f = pow((g_f + 0.055) / 1.055, 2.4) if g_f > 0.04045 else g_f / 12.92
    b_f = pow((b_f + 0.055) / 1.055, 2.4) if b_f > 0.04045 else b_f / 12.92

    # Wide gamut D65 RGB → XYZ
    X = r_f * 0.664511 + g_f * 0.154324 + b_f * 0.162028
    Y = r_f * 0.283881 + g_f * 0.668433 + b_f * 0.047685
    Z = r_f * 0.000088 + g_f * 0.072310 + b_f * 0.986039

    denom = X + Y + Z
    if denom == 0:
        return 0.3127, 0.3290  # D65 white point
    return round(X / denom, 4), round(Y / denom, 4)


# ── Settings helpers ─────────────────────────────────────────────────────────

def load_hue_settings() -> dict:
    if not os.path.exists(HUE_SETTINGS_FILE):
        return {}
    try:
        with open(HUE_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_hue_settings(data: dict):
    try:
        with open(HUE_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as exc:
        print(f"[HUE] Could not save settings: {exc}", flush=True)


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_hue_bridge(timeout: int = 5) -> list[dict]:
    """
    Discover Hue bridges. Tries mDNS (zeroconf) first, falls back to the
    Signify broker at discovery.meethue.com. Returns [{ip, name}, ...].
    """
    found: dict[str, str] = {}  # ip -> name

    # mDNS via zeroconf
    if _ZEROCONF_AVAILABLE and _REQUESTS_AVAILABLE:
        try:
            class _Listener(ServiceListener):
                def add_service(self, zc, type_, name):
                    info = zc.get_service_info(type_, name)
                    if info and info.addresses:
                        import socket
                        ip = socket.inet_ntoa(info.addresses[0])
                        found[ip] = info.server or name

                def update_service(self, *_): pass
                def remove_service(self, *_): pass

            zc = Zeroconf()
            browser = ServiceBrowser(zc, "_hue._tcp.local.", _Listener())
            time.sleep(min(timeout, 3))
            zc.close()
        except Exception as exc:
            print(f"[HUE] mDNS discovery error: {exc}", flush=True)

    # Broker fallback
    if not found and _REQUESTS_AVAILABLE:
        try:
            resp = _requests.get(
                "https://discovery.meethue.com",
                timeout=timeout,
            )
            resp.raise_for_status()
            for bridge in resp.json():
                ip = bridge.get("internalipaddress", "")
                if ip:
                    found[ip] = bridge.get("id", ip)
        except Exception as exc:
            print(f"[HUE] Broker discovery error: {exc}", flush=True)

    return [{"ip": ip, "name": name} for ip, name in found.items()]


# ── Controller ───────────────────────────────────────────────────────────────

class HueController:
    """
    Mirrors NanoleafController's effect interface so bridge_runner can fire
    effects on LIFX, Nanoleaf, and Hue in parallel without duplicating logic.

    Lifecycle:
      1. Instantiate with ip + username (application key from pairing).
      2. Check ._connected before use — HueController.try_connect handles this.
      3. Set self.selected_lights to a list of CLIP v2 light IDs.

    Pairing (first-time setup):
      - Call HueController.pair(ip) while user holds the link button.
      - Returns {"username": ..., "clientkey": ...}.
      - Save both + ip to hue_settings.json.
    """

    def __init__(self, ip: str, username: str, log_callback=None):
        self.ip = ip
        self.username = username
        self.log_callback = log_callback
        self.hue_diag = False

        # Lights to send effects to — list of CLIP v2 light IDs (strings).
        self.selected_lights: list[str] = []

        # Idle colour as RGB tuple (default: warm white ~2700K).
        self.idle_rgb: tuple[int, int, int] = (255, 200, 120)

        # Brightness scaling (0-100 percentage for Hue dimming).
        self.brightness_min: int = 0
        self.brightness_max: int = 100

        self._effect_lock = threading.Lock()
        self._active_effect: str | None = None
        self._current_effect_key: str | None = None
        self._connected = False

        # Cache of discovered lights: [{id, name, type, is_gradient}, ...]
        self._lights_cache: list[dict] = []

        self._connect()

    # ── Private helpers ──────────────────────────────────────────────────────

    def _base_url(self) -> str:
        return f"https://{self.ip}"

    def _headers(self) -> dict:
        return {"hue-application-key": self.username}

    def _get(self, path: str, timeout: int = 4) -> dict | None:
        if not _REQUESTS_AVAILABLE:
            return None
        try:
            resp = _requests.get(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                verify=False,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            self._log(f"[HUE ERROR] GET {path}: {exc}")
            return None

    def _put(self, path: str, body: dict, timeout: int = 3) -> bool:
        if not _REQUESTS_AVAILABLE:
            return False
        try:
            resp = _requests.put(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                json=body,
                verify=False,
                timeout=timeout,
            )
            if self.hue_diag:
                self._log(f"[HUE DIAG] PUT {path} → {resp.status_code} {resp.text[:120]}")
            return resp.status_code < 300
        except Exception as exc:
            self._log(f"[HUE ERROR] PUT {path}: {exc}")
            return False

    def _connect(self):
        if not _REQUESTS_AVAILABLE:
            self._log("[HUE] requests not installed — Hue disabled.")
            return
        data = self._get(f"{_CLIP}/light")
        if data is not None:
            self._connected = True
            self._lights_cache = self._parse_lights(data.get("data", []))
            self._log(f"[HUE] Connected to bridge @ {self.ip} — {len(self._lights_cache)} light(s)")
        else:
            self._log(f"[HUE] Connection failed @ {self.ip}")

    def _parse_lights(self, raw: list) -> list[dict]:
        lights = []
        for l in raw:
            meta = l.get("metadata", {})
            archetype = meta.get("archetype", "")
            is_gradient = "gradient" in l
            lights.append({
                "id":          l.get("id", ""),
                "name":        meta.get("name", l.get("id", "")),
                "type":        l.get("type", "light"),
                "is_gradient": is_gradient,
                "archetype":   archetype,
            })
        return lights

    def _log(self, msg: str):
        print(msg, flush=True)
        if self.log_callback:
            self.log_callback(msg)

    def _scale_brightness(self, pct: int) -> int:
        """Scale 0-100 into [brightness_min, brightness_max]."""
        return max(1, min(100, self.brightness_min + int(
            (self.brightness_max - self.brightness_min) * pct / 100
        )))

    def _get_gradient_strip_ids(self) -> list[str]:
        """Return the subset of selected_lights that are gradient lightstrips."""
        gradient_ids = {l["id"] for l in self._lights_cache if l["is_gradient"]}
        return [lid for lid in self.selected_lights if lid in gradient_ids]

    def _get_regular_light_ids(self) -> list[str]:
        """Return the subset of selected_lights that are NOT gradient lightstrips."""
        gradient_ids = {l["id"] for l in self._lights_cache if l["is_gradient"]}
        return [lid for lid in self.selected_lights if lid not in gradient_ids]

    def _put_gradient(self, light_id: str, points: list[dict], bri: int, duration_ms: int = 40):
        """PUT a gradient payload to a single light."""
        body = {
            "on":      {"on": True},
            "dimming": {"brightness": bri},
            "dynamics": {"duration": duration_ms},
            "gradient": {
                "points": points,
                "mode":   "interpolated_palette",
            },
        }
        self._put(f"{_CLIP}/light/{light_id}", body)

    def _build_gradient_points(self, num_lit: int, total_points: int = 7) -> list[dict]:
        """
        Build gradient point array for the start lights sweep.

        The first N points are red (lit zones); the rest show the idle colour
        (unlit zones). 7 points is the minimum supported by all gradient strips.

        num_lit: 0–5 matching the F1 start lights count.
        """
        red_x, red_y     = _rgb_to_xy(220, 0, 0)
        idle_x, idle_y   = _rgb_to_xy(*self.idle_rgb)
        lit_count = round(num_lit * total_points / 5)
        return [
            {"color": {"xy": {"x": red_x,  "y": red_y }}} if i < lit_count
            else {"color": {"xy": {"x": idle_x, "y": idle_y}}}
            for i in range(total_points)
        ]

    def _gradient_idle_points(self, total_points: int = 7) -> list[dict]:
        """Return a uniform idle-colour gradient (used to reset strip after start lights)."""
        idle_x, idle_y = _rgb_to_xy(*self.idle_rgb)
        return [{"color": {"xy": {"x": idle_x, "y": idle_y}}} for _ in range(total_points)]

    # ── Effect state ─────────────────────────────────────────────────────────

    def set_active_effect(self, name: str):
        with self._effect_lock:
            self._active_effect = name

    def clear_active_effect(self):
        with self._effect_lock:
            self._active_effect = None

    def is_effect_active(self, name: str) -> bool:
        with self._effect_lock:
            return self._active_effect == name

    # ── Core colour send ─────────────────────────────────────────────────────

    def set_color(self, r: int, g: int, b: int, brightness_pct: int = 100, duration_ms: int = 0):
        """Send a colour to all selected lights via CLIP v2."""
        if not self._connected or not self.selected_lights:
            return
        x, y = _rgb_to_xy(r, g, b)
        bri = self._scale_brightness(brightness_pct)
        body = {
            "on":      {"on": True},
            "color":   {"xy": {"x": x, "y": y}},
            "dimming": {"brightness": bri},
            "dynamics": {"duration": duration_ms},
        }
        for light_id in self.selected_lights:
            self._put(f"{_CLIP}/light/{light_id}", body)

    def set_color_all(self, hsbk: list, duration_ms: int = 50, stagger: bool = True):
        """Bridge-compatible interface — accepts LIFX HSBK and converts to Hue XY.

        Called by bridge_core._fire() so the shared bridge loops (yellow/blue/red
        flag) drive Hue alongside LIFX and Nanoleaf with no extra coordination.
        """
        h, s, b, _k = hsbk
        r, g, bl = _hsbk_to_rgb(h, s, b)
        bri = max(1, min(100, int(b / 65535.0 * 100)))
        self.set_color(r, g, bl, brightness_pct=bri, duration_ms=duration_ms)

    def set_idle(self):
        """Return all selected lights to the configured idle colour."""
        r, g, b = self.idle_rgb
        # Reset gradient strips to uniform idle colour so the start-lights
        # pattern doesn't linger after lights_out.
        for lid in self._get_gradient_strip_ids():
            self._put_gradient(lid, self._gradient_idle_points(),
                               self._scale_brightness(40), duration_ms=800)
        for lid in self._get_regular_light_ids():
            x, y = _rgb_to_xy(r, g, b)
            self._put(f"{_CLIP}/light/{lid}", {
                "on":      {"on": True},
                "color":   {"xy": {"x": x, "y": y}},
                "dimming": {"brightness": self._scale_brightness(40)},
                "dynamics": {"duration": 800},
            })

    # ── Flash helper ─────────────────────────────────────────────────────────

    def _snap_and_wait(self, r: int, g: int, b: int, bri: int, hold_ms: int):
        t0 = time.monotonic()
        self.set_color(r, g, b, brightness_pct=bri, duration_ms=0)
        elapsed_ms = (time.monotonic() - t0) * 1000
        remaining = (hold_ms - elapsed_ms) / 1000.0
        if remaining > 0:
            time.sleep(remaining)

    def flash_colors(self, colors: list[tuple], loops: int = 3, hold_ms: int = 200):
        """Flash through [(r, g, b, bri%), ...] tuples."""
        for _ in range(loops):
            for r, g, b, bri in colors:
                self._snap_and_wait(r, g, b, bri, hold_ms)

    # ── Public: lights ────────────────────────────────────────────────────────

    def list_lights(self) -> list[dict]:
        """Return cached light list. Call refresh_lights() to re-fetch."""
        return list(self._lights_cache)

    def refresh_lights(self):
        data = self._get(f"{_CLIP}/light")
        if data is not None:
            self._lights_cache = self._parse_lights(data.get("data", []))

    def identify(self, light_id: str):
        """Pulse a single light for identification."""
        self._put(f"{_CLIP}/light/{light_id}", {"identify": {"action": "breathe"}})

    # ── Effects ──────────────────────────────────────────────────────────────

    def neutral(self):
        self.clear_active_effect()
        self._current_effect_key = "neutral"
        self.set_idle()

    def yellow_flag(self):
        self._current_effect_key = "yellow_flag"
        if self.is_effect_active("yellow_flash"):
            return
        self.set_active_effect("yellow_flash")
        threading.Thread(target=self._yellow_flash_loop, daemon=True).start()

    def _yellow_flash_loop(self):
        while self.is_effect_active("yellow_flash"):
            self.set_color(255, 180, 0, brightness_pct=100)
            time.sleep(0.45)
            if not self.is_effect_active("yellow_flash"):
                break
            self.set_color(0, 0, 0, brightness_pct=1)
            time.sleep(0.45)

    def blue_flag(self):
        self.clear_active_effect()
        self._current_effect_key = "blue_flag"
        self.set_active_effect("blue_pulse")
        threading.Thread(target=self._blue_pulse_loop, daemon=True).start()

    def _blue_pulse_loop(self):
        while self.is_effect_active("blue_pulse"):
            self.set_color(0, 100, 255, brightness_pct=100)
            for _ in range(7):
                if not self.is_effect_active("blue_pulse"):
                    return
                time.sleep(0.1)
            self.set_color(0, 100, 255, brightness_pct=20)
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
        while self.is_effect_active("red_pulse"):
            self.set_color(255, 0, 0, brightness_pct=100)
            for _ in range(7):
                if not self.is_effect_active("red_pulse"):
                    return
                time.sleep(0.1)
            self.set_color(255, 0, 0, brightness_pct=15)
            for _ in range(7):
                if not self.is_effect_active("red_pulse"):
                    return
                time.sleep(0.1)

    def white_warning(self):
        self.clear_active_effect()
        self._current_effect_key = "white_warning"
        self.flash_colors([
            (255, 255, 255, 100),
            (0, 0, 0, 1),
        ], loops=3, hold_ms=250)
        self.neutral()

    def fastest_lap(self):
        self._current_effect_key = "fastest_lap"
        self.flash_colors([
            (180, 0, 255, 100),
            (0, 0, 0, 1),
        ], loops=3, hold_ms=200)
        self.neutral()

    def chequered_flag(self):
        self.clear_active_effect()
        self._current_effect_key = "chequered_flag"
        self.flash_colors([
            (255, 255, 255, 100),
            (0, 200, 0, 100),
        ], loops=5, hold_ms=300)
        self.neutral()

    def _flash_gradient_strips_green(self, bri: int):
        """Send a full-green gradient to all gradient strips in selected_lights."""
        gx, gy = _rgb_to_xy(0, 255, 0)
        pts = [{"color": {"xy": {"x": gx, "y": gy}}} for _ in range(7)]
        for lid in self._get_gradient_strip_ids():
            self._put_gradient(lid, pts, bri, duration_ms=0)

    def lights_out(self):
        self.clear_active_effect()
        self._current_effect_key = "lights_out"
        # Green flash — gradient strips get a full-green gradient to immediately
        # replace the start-lights red pattern; regular bulbs use set_color.
        self._flash_gradient_strips_green(self._scale_brightness(100))
        self._snap_and_wait(0, 255, 0, 100, 200)   # also covers regular bulbs
        self._snap_and_wait(0, 0, 0, 1, 150)
        self._flash_gradient_strips_green(self._scale_brightness(100))
        self._snap_and_wait(0, 255, 0, 100, 350)
        self.set_idle()

    def start_lights(self, num_lights: int):
        """
        Start lights sweep.

        Gradient strips: fill segments left-to-right one by one as each F1
        start light illuminates (points 0..N become red, rest stay idle colour).

        Regular bulbs: solid red at stepped brightness matching num_lights.
        """
        self.clear_active_effect()
        self._current_effect_key = "start_lights"
        num_lights = max(0, min(5, num_lights))

        # Gradient strips — progressive fill
        for lid in self._get_gradient_strip_ids():
            pts = self._build_gradient_points(num_lights)
            self._put_gradient(lid, pts, self._scale_brightness(100))

        # Regular bulbs — stepped brightness
        brightness_by_count = {0: 15, 1: 30, 2: 45, 3: 65, 4: 80, 5: 100}
        bri_pct = brightness_by_count[num_lights]
        x, y = _rgb_to_xy(220, 0, 0)
        body = {
            "on":      {"on": True},
            "color":   {"xy": {"x": x, "y": y}},
            "dimming": {"brightness": self._scale_brightness(bri_pct)},
            "dynamics": {"duration": 40},
        }
        for lid in self._get_regular_light_ids():
            self._put(f"{_CLIP}/light/{lid}", body)

    # ── Class-level helpers ───────────────────────────────────────────────────

    @classmethod
    def try_connect(cls, ip: str, username: str, log_callback=None) -> "HueController | None":
        ctrl = cls(ip, username, log_callback=log_callback)
        return ctrl if ctrl._connected else None

    @staticmethod
    def pair(ip: str) -> "dict | None":
        """
        Register GridGlow with a Hue Bridge.

        User must press the physical link button on the Bridge before calling.
        Returns {"username": ..., "clientkey": ...} or None on failure.
        """
        if not _REQUESTS_AVAILABLE:
            print("[HUE] requests not installed — cannot pair.", flush=True)
            return None
        try:
            resp = _requests.post(
                f"https://{ip}/api",
                json={"devicetype": "gridglow#app", "generateclientkey": True},
                verify=False,
                timeout=6,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                entry = data[0]
                if "success" in entry:
                    return {
                        "username":  entry["success"].get("username", ""),
                        "clientkey": entry["success"].get("clientkey", ""),
                    }
                if "error" in entry:
                    desc = entry["error"].get("description", "unknown error")
                    print(f"[HUE] Pairing error: {desc}", flush=True)
            return None
        except Exception as exc:
            print(f"[HUE] Pairing failed: {exc}", flush=True)
            return None
