"""
Nanoleaf integration for GridGlow.

Provides:
  - NanoleafController  — mirrors LocalLifxController so effects fire on
                          LIFX and Nanoleaf side-by-side with no changes to
                          F1 packet handling logic.
  - discover_nanoleaf() — SSDP-based device discovery.
  - load/save helpers   — nanoleaf_settings.json persistence.
"""

import json
import os
import socket
import struct
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

from app_paths import USER_DATA_DIR

NANOLEAF_SETTINGS_FILE = str(USER_DATA_DIR / "nanoleaf_settings.json")

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
#
# Nanoleaf devices advertise on two protocols:
#   • mDNS  — service `_nanoleafapi._tcp.local.` on 224.0.0.251:5353 (modern firmware)
#   • SSDP  — M-SEARCH on 239.255.255.250:1900, ST `nanoleaf_aurora:light` / `nanoleaf:nl29`
#
# The reason `nanoleafapi.discover_devices()` (SSDP only) fails on Windows is that it
# sends the multicast probe from a single default socket; on a multi-homed host (VPN
# tunnels, Hyper-V / WSL virtual adapters, multiple NICs) the OS routes that probe out
# the wrong adapter and the device never hears it. The fix below sends probes from
# *every* LAN interface explicitly (binding the socket + IP_MULTICAST_IF to each local
# address) and reads the unicast replies, whose source IP is the device address.
# Pure stdlib — works whether or not nanoleafapi is installed.

_SSDP_ADDR  = "239.255.255.250"
_SSDP_PORT  = 1900
_MDNS_ADDR  = "224.0.0.251"
_MDNS_PORT  = 5353
_NL_SERVICE = "_nanoleafapi._tcp.local."


_DISCOVERY_LOCK = threading.Lock()


def _record_device(results: dict, ip: str, name: str):
    """Merge a discovered device, preferring a specific name over the generic fallback.

    mDNS and SSDP probes race across threads; SSDP often has no friendly name. Don't
    let a generic 'Nanoleaf' from whichever replied first mask a real name like
    'Canvas 47A0'. Guarded by a lock since probe threads share `results`.
    """
    with _DISCOVERY_LOCK:
        existing = results.get(ip)
        if existing is None or (existing == "Nanoleaf" and name and name != "Nanoleaf"):
            results[ip] = name or "Nanoleaf"


def _local_ipv4_addresses() -> list:
    """Return routable local IPv4 addresses (excludes loopback / link-local)."""
    addrs = []
    try:
        import psutil
        for _name, entries in psutil.net_if_addrs().items():
            for a in entries:
                if a.family == socket.AF_INET:
                    ip = a.address
                    if not ip.startswith(("127.", "169.254.")):
                        addrs.append(ip)
    except Exception:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith(("127.", "169.254.")):
                    addrs.append(ip)
        except Exception:
            pass
    if not addrs:
        addrs = ["0.0.0.0"]
    # Preserve order, drop duplicates.
    seen, out = set(), []
    for ip in addrs:
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def _ssdp_discover(local_ip: str, timeout: float, results: dict):
    """Send an SSDP M-SEARCH from one interface and collect Nanoleaf replies."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {_SSDP_ADDR}:{_SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: ssdp:all\r\n"
        "\r\n"
    ).encode("ascii")
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if local_ip != "0.0.0.0":
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(local_ip))
            sock.bind((local_ip, 0))
        sock.settimeout(timeout)
        sock.sendto(msg, (_SSDP_ADDR, _SSDP_PORT))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                sock.settimeout(max(0.1, deadline - time.monotonic()))
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                break
            except OSError:
                break
            text = data.decode("utf-8", "ignore")
            low = text.lower()
            if "nanoleaf" not in low:
                continue
            ip = addr[0]
            name = None
            for line in text.split("\r\n"):
                key, _, val = line.partition(":")
                k = key.strip().lower()
                if k in ("nl-devicename", "s") and val.strip():
                    name = val.strip()
                    break
            _record_device(results, ip, name or "Nanoleaf")
    except Exception as exc:
        print(f"[NANOLEAF] SSDP probe on {local_ip} failed: {exc}", flush=True)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def _build_mdns_query(service: str) -> bytes:
    """Build an mDNS PTR query for `service`, with the unicast-response (QU) bit set."""
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)  # id=0, flags=0, qd=1
    qname = b"".join(
        struct.pack("B", len(label)) + label.encode("ascii")
        for label in service.rstrip(".").split(".")
    ) + b"\x00"
    # QTYPE=12 (PTR), QCLASS=0x8001 (IN + QU unicast-response bit)
    return header + qname + struct.pack(">HH", 12, 0x8001)


def _mdns_discover(local_ip: str, timeout: float, results: dict):
    """Send an mDNS PTR query from one interface; device IP = reply source address."""
    query = _build_mdns_query(_NL_SERVICE)
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if local_ip != "0.0.0.0":
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(local_ip))
            sock.bind((local_ip, 0))
        sock.settimeout(timeout)
        sock.sendto(query, (_MDNS_ADDR, _MDNS_PORT))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                sock.settimeout(max(0.1, deadline - time.monotonic()))
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            except OSError:
                break
            # A reply to our service query means addr[0] is a Nanoleaf. Use the
            # source IP directly (avoids brittle DNS name-compression parsing).
            name = _parse_mdns_instance_name(data) or "Nanoleaf"
            _record_device(results, addr[0], name)
    except Exception as exc:
        print(f"[NANOLEAF] mDNS probe on {local_ip} failed: {exc}", flush=True)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def _parse_mdns_instance_name(data: bytes) -> str | None:
    """Best-effort: pull the human instance label from a PTR answer, e.g. 'Canvas 47A0'.

    The PTR target ('<instance>._nanoleafapi._tcp.local') stores the instance as a
    length-prefixed label immediately followed by a compression pointer (0xC0…) back
    to the service suffix. We scan for that pattern and return the first label that
    isn't a service/protocol token. Cosmetic only — the IP comes from the reply's
    source address, so None here is harmless (caller falls back to 'Nanoleaf').
    """
    try:
        skip = {"_tcp", "_nanoleafapi", "_nanoleaf", "local"}
        # Check every offset (don't label-walk — the header/question bytes would
        # misalign us past the PTR RDATA). A match is: length byte, printable label,
        # then a 0xC0 compression pointer back to the service suffix.
        for i in range(len(data) - 2):
            ln = data[i]
            if not (1 <= ln <= 63) or i + 1 + ln >= len(data):
                continue
            chunk = data[i + 1:i + 1 + ln]
            if data[i + 1 + ln] != 0xC0:
                continue
            if not all(32 <= b <= 126 for b in chunk):
                continue
            label = chunk.decode("ascii", "ignore")
            if not label.startswith("_") and label.lower() not in skip:
                return label
        return None
    except Exception:
        return None


def discover_nanoleaf(timeout: int = 5) -> list:
    """
    Discover Nanoleaf devices on the LAN via mDNS + SSDP across all interfaces.
    Returns [{name, ip}, ...], deduplicated by IP. Pure stdlib; falls back to
    nanoleafapi.discover_devices() only if the active scan finds nothing.
    """
    results: dict = {}
    per_iface_timeout = max(1.5, min(float(timeout), 6.0))
    interfaces = _local_ipv4_addresses()

    threads = []
    for ip in interfaces:
        for fn in (_mdns_discover, _ssdp_discover):
            t = threading.Thread(target=fn, args=(ip, per_iface_timeout, results),
                                 daemon=True)
            t.start()
            threads.append(t)
    for t in threads:
        t.join(per_iface_timeout + 1.0)

    if results:
        devices = [{"name": name, "ip": ip} for ip, name in results.items()]
        print(f"[NANOLEAF] Discovery found {len(devices)} device(s): "
              f"{', '.join(d['ip'] for d in devices)}", flush=True)
        return devices

    # Fallback: library SSDP (older behaviour) in case the manual scan missed.
    if _NANOLEAF_AVAILABLE and _discover_devices is not None:
        try:
            raw = _discover_devices(int(per_iface_timeout))
            if isinstance(raw, dict):
                return [{"name": name, "ip": ip} for name, ip in raw.items()]
        except Exception as exc:
            print(f"[NANOLEAF] Library discovery error: {exc}", flush=True)

    print("[NANOLEAF] Discovery found no devices.", flush=True)
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
        self.nanoleaf_diag = False

        # Brightness scaling (same semantics as LocalLifxController).
        self.brightness_min = 0
        self.brightness_max = 65535

        # Label used as the key in light_assignments — matches what get_discovered_lights()
        # returns so the UI and backend stay in sync.  Set properly after device info fetch.
        self.label: str = ip

        # Per-effect light assignment — same semantics as LocalLifxController.
        # {label: None | [effect_keys]}  None = all effects; list = only those keys.
        self.light_assignments: dict = {}
        self._current_effect_key: str | None = None

        # Intensity curves: {label: {points: [[t,v],...], duration_ms: int}}
        self.curves: dict = {}
        self._curve_pts: list | None = None
        self._curve_start: float | None = None
        self._curve_dur: float = 2.0

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
            # Exclude non-light panels:
            #   shapeType  1 = Rhythm module (Light Panels)
            #   shapeType 12 = Rhythm module (Shapes / Canvas, modern firmware)
            #   shapeType  3 on NL29 = Canvas controller square in older firmware
            #   (shapeType 3 is a valid hexagon on Shapes devices — only exclude for Canvas)
            is_canvas = model_code.startswith('NL29')
            light_panels = [
                p for p in position_data
                if p.get("shapeType", 0) not in {1, 12}
                and not (is_canvas and p.get("shapeType", 0) == 3)
            ]
            self._side_length = layout.get("sideLength", 150)
            self._panel_ids = [p["panelId"] for p in light_panels]
            self._sweep_panel_ids = [p["panelId"] for p in sorted(light_panels, key=lambda p: (p["y"], p["x"]))]
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

            name       = data.get("name", "Nanoleaf")
            model_name = NANOLEAF_MODELS.get(model_code, f"Nanoleaf ({model_code})")
            self.device_info = {
                "name":        name,
                "model":       model_code,
                "model_name":  model_name,
                "firmware":    data.get("firmwareVersion", ""),
                "num_panels":  num_panels,
            }
            # Keep label in sync with get_discovered_lights() computation.
            self.label = f"{name} ({model_name})" if model_name and model_name.lower() not in name.lower() else name
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
        sorted_panels = sorted(panels, key=lambda p: (p["y"], p["x"]))
        self._sweep_panel_ids = [p["panelId"] for p in sorted_panels]

    def set_panel_colors(self, panel_rgb: list[tuple[int, int, int, int]]):
        """Send per-panel colors in one HTTP call.

        panel_rgb: list of (panelId, R, G, B) tuples, one per panel.
        Uses the /effects static write endpoint with transitionTime=0 per panel.
        """
        if self._nl is None or _requests is None or not panel_rgb:
            return
        n = len(panel_rgb)
        # animType "custom" with numFrames=1 per panel is the reliable per-panel format.
        # Format: "<numPanels> <panelId> <numFrames> <R> <G> <B> <W> <transTime> ..."
        anim_data = f"{n} " + " ".join(f"{pid} 1 {r} {g} {b} 0 1" for pid, r, g, b in panel_rgb)
        url = f"http://{self.ip}:16021/api/v1/{self.auth_token}/effects"
        try:
            resp = _requests.put(url, json={
                "write": {
                    "command":  "display",
                    "animType": "custom",
                    "animData": anim_data,
                    "loop":     False,
                    "palette":  [],
                }
            }, timeout=2)
            if resp.status_code >= 300:
                self._log(f"[NANOLEAF] set_panel_colors failed: {resp.status_code} — {resp.text[:200]}")
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

    def set_current_effect_key(self, key: str):
        """Called by the bridge before bridge-level effect loops to keep _current_effect_key current."""
        self._current_effect_key = key
        self._activate_curve(key)

    def _is_assigned(self, effect_key: str) -> bool:
        """Return True if this Nanoleaf device should fire for effect_key."""
        if not self.light_assignments or not self.label:
            return True
        assignment = self.light_assignments.get(self.label)
        return assignment is None or effect_key in assignment

    @staticmethod
    def _eval_curve(pts: list, t: float) -> float:
        if not pts or len(pts) < 2:
            return 1.0
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            if t <= pts[i][0]:
                t0, v0 = pts[i - 1]
                t1, v1 = pts[i]
                return v0 + (v1 - v0) * ((t - t0) / (t1 - t0))
        return 1.0

    def _activate_curve(self, effect_key: str):
        from bridge_core import _EFFECT_CURVE_LABEL
        label = _EFFECT_CURVE_LABEL.get(effect_key)
        curve = self.curves.get(label) if label else None
        if curve and len(curve.get('points', [])) >= 2:
            self._curve_pts   = curve['points']
            self._curve_dur   = max(0.001, curve.get('duration_ms', 2000) / 1000.0)
            self._curve_start = time.monotonic()
        else:
            self._curve_pts   = None
            self._curve_start = None

    def _deactivate_curve(self):
        self._curve_pts   = None
        self._curve_start = None

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
        if self._current_effect_key and not self._is_assigned(self._current_effect_key):
            return
        h, s, b, k = hsbk
        if self._curve_pts is not None and self._curve_start is not None and b > 500:
            t = min(1.0, (time.monotonic() - self._curve_start) / self._curve_dur)
            b = max(1, min(65535, int(b * self._eval_curve(self._curve_pts, t))))
        b_scaled = self._scale_brightness(b)
        try:
            if self._panel_ids:
                r, g, bl = _hsbk_to_rgb(h, s, b_scaled)
                n = len(self._panel_ids)
                # animType "custom" with numFrames=1: "<numPanels> <panelId> 1 <R> <G> <B> <W> <transTime> ..."
                anim_data = f"{n} " + " ".join(
                    f"{pid} 1 {r} {g} {bl} 0 1" for pid in self._panel_ids
                )
                url = f"http://{self.ip}:16021/api/v1/{self.auth_token}/effects"
                resp = _requests.put(url, json={
                    "write": {
                        "command":  "display",
                        "animType": "custom",
                        "animData": anim_data,
                        "loop":     False,
                        "palette":  [],
                    }
                }, timeout=2)
                if self.nanoleaf_diag and not self._diag_logged:
                    self._diag_logged = True
                    self._log(f"[NANOLEAF DIAG] effects/custom status={resp.status_code} anim_data={anim_data[:120]}")
                    if resp.status_code >= 300:
                        self._log(f"[NANOLEAF DIAG] body={resp.text[:200]}")
                if resp.status_code < 200 or resp.status_code >= 300:
                    self._state_put(h, s, b_scaled)
            else:
                if self.nanoleaf_diag and not self._diag_logged:
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
        if not self._is_assigned("neutral"):
            return
        self.clear_active_effect()
        self._current_effect_key = "neutral"
        self._activate_curve("neutral")
        self.set_color_all([0, 0, 50000, 4500], duration_ms=800)
        self._deactivate_curve()

    def sector_status(self, sector_flags):
        """No-op for now: the 3-segment sector display is multizone-strip only.

        Nanoleaf panels aren't a linear strip, so they keep the normal flag flash.
        Spatial (panel-position) sector rendering is a possible follow-up.
        """
        return

    def rpm_meter(self, percent):
        """No-op: the RPM meter is multizone-strip only.

        Nanoleaf panels aren't a linear strip, so they keep the normal effects.
        Spatial (panel-position) rev rendering is a possible follow-up.
        """
        return

    def rpm_redline(self):
        """No-op: the RPM meter (and its redline blink) is multizone-strip only."""
        return

    def yellow_flag(self):
        if not self._is_assigned("yellow_flag"):
            return
        self._current_effect_key = "yellow_flag"
        self._activate_curve("yellow_flag")
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
        if not self._is_assigned("blue_flag"):
            return
        self.clear_active_effect()
        self._current_effect_key = "blue_flag"
        self._activate_curve("blue_flag")
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
        if not self._is_assigned("red_flag"):
            return
        self.clear_active_effect()
        self._current_effect_key = "red_flag"
        self._activate_curve("red_flag")
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
        if not self._is_assigned("black_flag"):
            return
        self.clear_active_effect()
        self._current_effect_key = "black_flag"
        self._activate_curve("black_flag")
        dark      = [0, 0, 1, 3500]
        white_dim = [0, 0, 12000, 4500]
        self.flash_colors([dark, white_dim], loops=3, hold_ms=400)
        self.set_color_all(dark, duration_ms=500, stagger=False)

    def white_warning(self):
        if not self._is_assigned("white_warning"):
            return
        self.clear_active_effect()
        self._current_effect_key = "white_warning"
        self._activate_curve("white_warning")
        white = [0, 0, 65535, 4500]
        dark  = [0, 0, 1, 3500]
        self.flash_colors([white, dark], loops=3, hold_ms=250)
        self._deactivate_curve()
        self.neutral()

    def fastest_lap(self):
        if not self._is_assigned("fastest_lap"):
            return
        self._current_effect_key = "fastest_lap"
        self._activate_curve("fastest_lap")
        purple = [54613, 65535, 65535, 3500]
        dark   = [0, 0, 1, 3500]
        self.flash_colors([purple, dark], loops=3, hold_ms=200)
        self._deactivate_curve()
        self.neutral()

    def chequered_flag(self):
        if not self._is_assigned("chequered_flag"):
            return
        self.clear_active_effect()
        self._current_effect_key = "chequered_flag"
        self._activate_curve("chequered_flag")
        white = [0, 0, 65535, 4500]
        green = [21845, 65535, 65535, 3500]
        self.flash_colors([white, green], loops=5, hold_ms=300)
        self._deactivate_curve()
        self.neutral()

    def lights_out(self):
        if not self._is_assigned("lights_out"):
            return
        self.clear_active_effect()
        self._current_effect_key = "lights_out"
        self._activate_curve("lights_out")
        green = [21845, 65535, 65535, 3500]
        dark  = [0, 0, 1, 3500]
        white = [0, 0, 50000, 4500]
        self._snap_and_wait(green, 200)
        self._snap_and_wait(dark,  150)
        self._snap_and_wait(green, 350)
        self.set_color_all(white)
        self._deactivate_curve()

    def multizone_color_test(self):
        """Green-to-red sequential panel fill, matching the LIFX multizone test."""
        sweep_ids = list(self._sweep_panel_ids or self._panel_ids)
        if not sweep_ids:
            self._log("[NANOLEAF] No panels for multizone test.")
            return

        if self.mz_startlights_direction == "rtl":
            sweep_ids = list(reversed(sweep_ids))

        dark_rgb = (0, 0, 0)
        panel_count = len(sweep_ids)

        # Start fully dark
        self.set_panel_colors([(pid, 0, 0, 0) for pid in sweep_ids])
        time.sleep(0.35)

        # Fill panel-by-panel: gradient from green (first) → red (last)
        current_colors = {pid: dark_rgb for pid in sweep_ids}
        for i, pid in enumerate(sweep_ids):
            t = i / max(panel_count - 1, 1)
            hue_norm = 1.0 - t          # 1.0=green, 0.0=red
            hsbk = [int(hue_norm * 21845), 65535, self._scale_brightness(65535), 3500]
            rgb = _hsbk_to_rgb(*hsbk[:3])
            current_colors[pid] = rgb
            self.set_panel_colors([(p, *current_colors[p]) for p in sweep_ids])
            time.sleep(0.06)

    def start_lights(self, num_lights: int):
        """Sweep red panels L→R (or R→L) as start lights appear, dark otherwise."""
        if not self._is_assigned("start_lights"):
            return
        self.clear_active_effect()
        self._current_effect_key = "start_lights"
        self._activate_curve("start_lights")
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
