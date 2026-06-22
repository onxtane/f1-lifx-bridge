import ipaddress
import socket
import struct
import sys
import time
import threading
import json
import os
from dataclasses import dataclass

try:
    from lifxlan import LifxLAN
    from lifxlan import MultiZoneLight
except ImportError as e:
    print("ERROR: lifxlan is not installed in the active Python environment.", flush=True)
    print(f"Python executable: {sys.executable}", flush=True)
    print("Install it with: python -m pip install lifxlan", flush=True)
    raise SystemExit(1) from e

try:
    from nanoleaf_controller import NanoleafController
except ImportError:
    NanoleafController = None


# ============================================================
# CONFIG
# ============================================================

UDP_IP = "0.0.0.0"
UDP_PORT = 20777

# Keep this True first. It will print what it WOULD do,
# but it will not change the bulbs.
DRY_RUN = False

# Set True to print per-call timing for every set_color_all and effect to CMD.
# Output goes to stdout only — not the GUI log.
DEBUG_TIMING = False

# Max bulbs to discover on your LAN.
# Set higher than the number of LIFX bulbs you own.
LIFX_BULB_COUNT = 40

# True = after discovery, ask in the console which bulbs to use.
# False = use all discovered bulbs.
SELECT_LIGHTS_IN_CONSOLE = True

# Optional startup test.
# Leave False for now.
MANUAL_TEST_LIGHTS_ON_STARTUP = False

GROUPS_FILE = "lifx_groups.json"

# True = allow choosing/saving light groups in console.
USE_SAVED_LIGHT_GROUPS = True

# ============================================================
# PACKET CONSTANTS (F1 21 – F1 25)
# ============================================================

# Header structs differ between game years.
# F1 24/25: uint16 format, uint8 gameYear, uint8 majorVer, uint8 minorVer,
#           uint8 packetVer, uint8 packetId, uint64 sessionUID, float sessionTime,
#           uint32 frameId, uint32 overallFrameId, uint8 playerIdx, uint8 secPlayerIdx
# F1 23:    same but no gameYear field (28 bytes)
# F1 21/22: same as F1 23 but also no overallFrameId (24 bytes)
_HEADER_FORMAT_2425 = "<HBBBBBQfIIBB"   # 29 bytes
_HEADER_FORMAT_23   = "<HBBBBQfIIBB"    # 28 bytes
_HEADER_FORMAT_2122 = "<HBBBBQfIBB"     # 24 bytes

HEADER_SIZE = struct.calcsize(_HEADER_FORMAT_2425)   # 29 — reference for F1 24/25

PACKET_FORMAT_F1_21 = 2021
PACKET_FORMAT_F1_22 = 2022
PACKET_FORMAT_F1_23 = 2023
PACKET_FORMAT_F1_24 = 2024
PACKET_FORMAT_F1_25 = 2025
SUPPORTED_PACKET_FORMATS = {
    PACKET_FORMAT_F1_21, PACKET_FORMAT_F1_22, PACKET_FORMAT_F1_23,
    PACKET_FORMAT_F1_24, PACKET_FORMAT_F1_25,
}
PACKET_ID_EVENT = 3
PACKET_ID_CAR_STATUS = 7
PACKET_ID_SESSION = 1

# Session packet offsets are relative to header_size (passed dynamically).
# These constants are kept for F1 24/25 reference only.
SESSION_NUM_MARSHAL_ZONES_OFFSET = HEADER_SIZE + 18
SESSION_MARSHAL_ZONES_OFFSET = HEADER_SIZE + 19
MARSHAL_ZONE_SIZE = 5
MARSHAL_ZONE_FLAG_OFFSET = 4

EVENT_RED_FLAG = "RDFL"
EVENT_CHEQUERED_FLAG = "CHQF"
EVENT_PENALTY = "PENA"
EVENT_RETIREMENT = "RTMT"
EVENT_FASTEST_LAP = "FTLP"

CAR_STATUS_DATA_SIZE = 55
VEHICLE_FIA_FLAG_OFFSET_IN_CAR_STATUS = 28

FIA_FLAG_INVALID = -1
FIA_FLAG_NONE = 0
FIA_FLAG_GREEN = 1
FIA_FLAG_BLUE = 2
FIA_FLAG_YELLOW = 3

FIA_FLAG_NAMES = {
    FIA_FLAG_INVALID: "invalid",
    FIA_FLAG_NONE: "none",
    FIA_FLAG_GREEN: "green",
    FIA_FLAG_BLUE: "blue",
    FIA_FLAG_YELLOW: "yellow",
}

# Infringement IDs from the spec appendix that we want to treat as white warning flashes.
# These include corner cutting, running wide, invalidated laps, ignoring flags,
# multiple warnings, blocking/parking, and other behavior-type warnings.
WHITE_WARNING_INFRINGEMENTS = {
    7,   # Corner cutting gained time
    8,   # Corner cutting overtake single
    9,   # Corner cutting overtake multiple
    11,  # Ignoring blue flags
    12,  # Ignoring yellow flags
    18,  # Parked for too long
    21,  # Multiple warnings
    25,  # Lap invalidated corner cutting
    26,  # Lap invalidated running wide
    27,  # Corner cutting/running wide gained time minor
    28,  # Corner cutting/running wide gained time significant
    29,  # Corner cutting/running wide gained time extreme
    30,  # Lap invalidated wall riding
    32,  # Lap invalidated reset to track
    33,  # Blocking the pitlane
    36,  # Safety car illegal overtake
    37,  # Safety car exceeding allowed pace
    38,  # Virtual safety car exceeding allowed pace
    39,  # Formation lap below allowed speed
    40,  # Formation lap parking
}

BLACK_FLAG_INFRINGEMENTS = {
    44,  # Black flag timer
    45,  # Unserved stop go penalty
    46,  # Unserved drive through penalty
}

# Event offsets are header_size + N and are computed dynamically per packet.

EVENT_START_LIGHTS = "STLG"
EVENT_LIGHTS_OUT = "LGOT"


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class PacketHeader:
    packet_format: int
    game_year: int
    game_major_version: int
    game_minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float
    frame_identifier: int
    overall_frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int
    header_size: int = 29


# ============================================================
# LIFX LOCAL LAN CONTROL
# ============================================================

# Maps effect keys to the curve labels used in localStorage / bridge settings.
_EFFECT_CURVE_LABEL = {
    'start_lights':   'Start Lights',
    'lights_out':     'Lights Out',
    'yellow_flag':    'Yellow Flag',
    'blue_flag':      'Blue Flag',
    'red_flag':       'Red Flag',
    'fastest_lap':    'Fastest Lap',
    'chequered_flag': 'Chequered',
    'white_warning':  'White Warning',
    'neutral':        'Neutral',
}


class LocalLifxController:
    def __init__(
        self,
        bulb_count,
        select_in_console=True,
        use_saved_groups=True,
        groups_file="lifx_groups.json",
        dry_run=True,
        log_callback=None,
    ):
        self.bulb_count = bulb_count
        self.select_in_console = select_in_console
        self.use_saved_groups = use_saved_groups
        self.groups_file = groups_file
        self.dry_run = dry_run
        self.log_callback = log_callback
        self.lights = []
        self.discovered_lights = []
        self._zone_counts = {}   # mac_addr -> int, populated during discovery

        # Multizone start-lights behaviour
        self.mz_startlights_mode      = "sweep"  # "sweep" | "solid"
        self.mz_startlights_direction = "ltr"    # "ltr"   | "rtl"

        # Master brightness range in HSBK units (0–65535).
        self.brightness_min = 0
        self.brightness_max = 65535

        # Stagger delay between bulbs in ms (0 = disabled).
        self.stagger_ms = 0

        # Debug timing — prints per-call durations to stdout (CMD), not GUI.
        self.debug_timing = DEBUG_TIMING

        # Idle state: HSBK color + pulse flag.
        self.idle_hsbk = [0, 0, 50000, 4500]
        self.idle_pulse = False

        self._effect_lock = threading.Lock()
        self._active_effect = None

        # light_assignments: {label: None | [effect_keys]}
        # None = assigned to all effects; list = only those effects; absent = all
        self.light_assignments = {}
        self._current_effect_key = None

        # Intensity curves: {label: {points: [[t,v],...], duration_ms: int}}
        # Applied as a brightness multiplier in set_color_all during effects.
        self.curves: dict = {}
        self._curve_pts: list | None = None
        self._curve_start: float | None = None
        self._curve_dur: float = 2.0  # seconds

        # When live sector status is active, multizone strips are reserved for the
        # sector display: whole-light solid effects (set_color_all, neutral, flag
        # flashes) skip them, so only sector_status / start_lights paint the strip.
        self.sector_mode = False

        if self.dry_run:
            print("[LIFX] DRY_RUN is enabled. Bulbs will NOT change.")
            return

        if LifxLAN is None:
            raise RuntimeError(
                "lifxlan is not installed. Install it in Visual Studio's Python environment."
            )

        self.discover_lights()

    def identify_light(self, label: str):
        """Flash a single light by label so the user can identify the physical device."""
        target = next(
            (l for l in self.discovered_lights if self.safe_label(l).lower() == label.lower()),
            None,
        )
        if target is None:
            if self.log_callback:
                self.log_callback(f"[IDENTIFY] Light '{label}' not found in discovered lights.")
            return
        white  = [0, 0, 65535, 4500]
        dark   = [0, 0, 200,   4500]
        try:
            from lifxlan import MultiZoneLight
            for _ in range(3):
                if isinstance(target, MultiZoneLight):
                    target.set_zone_color(0, 255, white, 80, rapid=True)
                else:
                    target.set_color(white, 80)
                time.sleep(0.18)
                if isinstance(target, MultiZoneLight):
                    target.set_zone_color(0, 255, dark, 80, rapid=True)
                else:
                    target.set_color(dark, 80)
                time.sleep(0.18)
        except Exception as exc:
            if self.log_callback:
                self.log_callback(f"[IDENTIFY] Error flashing '{label}': {exc}")

    def start_lights_test(self):
        for num_lights in range(0, 6):
            self.start_lights(num_lights)
            time.sleep(0.5)

    def multizone_color_test(self):
        """Green-to-red sequential zone fill test, respecting direction setting."""
        dark = [0, 0, 0, 3500]

        mz_lights = [l for l in self.lights if isinstance(l, MultiZoneLight)]
        if not mz_lights:
            print("[MZ TEST] No multizone lights in selection.")
            return

        for light in mz_lights:
            zone_count = self.get_zone_count(light)
            if zone_count < 1:
                continue
            try:
                # Start fully dark
                light.set_zone_color(0, zone_count - 1, dark, 200, rapid=True)
                time.sleep(0.35)

                # Fill zone-by-zone: gradient from green (first filled) → red (last filled)
                for i in range(zone_count):
                    zone_idx = i if self.mz_startlights_direction == "ltr" else (zone_count - 1 - i)
                    t = i / max(zone_count - 1, 1)
                    hue = round(21845 * (1.0 - t))   # 21845=green, 0=red
                    brightness = self._scale_brightness(65535)
                    color = [hue, 65535, brightness, 3500]
                    light.set_zone_color(zone_idx, zone_idx, color, 120, rapid=False)
                    time.sleep(0.06)
            except Exception as exc:
                msg = f"[LIFX ERROR] MZ test {self.safe_label(light)}: {exc}"
                print(msg)
                if self.log_callback:
                    self.log_callback(msg)

    def discover_lights(self):
        print(f"[LIFX] Discovering up to {self.bulb_count} bulb(s) on your LAN...")

        # Bind discovery to each candidate LAN interface and keep the result that
        # finds the most bulbs. On multi-homed hosts an unbound socket lets the OS
        # broadcast out a VPN/virtual adapter, finding nothing — or aborting with a
        # connection reset (issue #1). Binding the source IP routes via the LAN NIC.
        candidates = _lan_source_ips()
        best_lifx, discovered_lights = None, []

        for src_ip in candidates:
            print(f"[LIFX] Trying discovery via {src_ip}...", flush=True)
            try:
                lifx = _BoundLifxLAN(self.bulb_count, source_ip=src_ip)
                lights = lifx.get_lights() or []
            except Exception as exc:
                print(f"[LIFX] Discovery via {src_ip} failed: {exc}", flush=True)
                continue
            print(f"[LIFX] {src_ip}: {len(lights)} bulb(s)", flush=True)
            if len(lights) > len(discovered_lights):
                best_lifx, discovered_lights = lifx, lights
                # If we already found everything we were asked to look for, stop.
                if self.bulb_count and len(discovered_lights) >= self.bulb_count:
                    break

        # Last resort: lifxlan's default INADDR_ANY behaviour (single-homed hosts).
        if not discovered_lights:
            print("[LIFX] No interface-bound discovery succeeded; trying default.", flush=True)
            try:
                lifx = LifxLAN(self.bulb_count)
                discovered_lights = lifx.get_lights() or []
                best_lifx = lifx
            except Exception as exc:
                print(f"[LIFX] Default discovery failed: {exc}", flush=True)

        if not discovered_lights:
            raise RuntimeError("No LIFX bulbs found on LAN.")

        # Reuse the winning bound instance for the multizone passes below so they
        # broadcast on the same (correct) interface.
        lifx = best_lifx
        print(f"[LIFX] Using {len(discovered_lights)} bulb(s) from the best interface.",
              flush=True)

        # Upgrade multizone-capable lights to MultiZoneLight objects.
        # Strategy 1: broadcast-based get_multizone_lights() with one retry.
        mz_by_mac = {}
        for attempt in range(2):
            try:
                mz_lights = lifx.get_multizone_lights()
                mz_by_mac = {l.mac_addr: l for l in (mz_lights or [])}
                if mz_by_mac:
                    break
            except Exception as exc:
                print(f"[LIFX] Multizone broadcast attempt {attempt + 1} failed: {exc}")
            if attempt == 0:
                time.sleep(0.5)

        for i, light in enumerate(discovered_lights):
            if light.mac_addr in mz_by_mac:
                discovered_lights[i] = mz_by_mac[light.mac_addr]
                print(f"[LIFX] Multizone strip detected: {self.safe_label(discovered_lights[i])}")
                continue
            # Strategy 2: direct product-feature probe for lights the broadcast missed.
            if not isinstance(light, MultiZoneLight):
                try:
                    features = light.get_product_features()
                    if features and features.get("multizone"):
                        mz = MultiZoneLight(light.mac_addr, light.ip_addr)
                        discovered_lights[i] = mz
                        print(f"[LIFX] Multizone strip detected (direct probe): {self.safe_label(mz)}")
                except Exception as exc:
                    print(f"[LIFX] Could not probe {self.safe_label(light)} for multizone: {exc}")

        # Cache zone counts now so get_zone_count() never blocks in hot paths.
        self._zone_counts = {}
        for light in discovered_lights:
            if isinstance(light, MultiZoneLight):
                try:
                    zones = light.get_color_zones(0, 255)
                    self._zone_counts[light.mac_addr] = len(zones) if zones else 0
                except Exception:
                    self._zone_counts[light.mac_addr] = 0

        self.discovered_lights = discovered_lights

        print()
        print("[LIFX] Discovered bulbs:")
        for index, light in enumerate(discovered_lights, start=1):
            print(f"  [{index}] {self.safe_label(light)} @ {light.get_ip_addr()}")

        if self.use_saved_groups:
            self.lights = self.prompt_for_group_or_selection(discovered_lights)
        elif self.select_in_console:
            self.lights = self.prompt_for_light_selection(discovered_lights)
        else:
            self.lights = discovered_lights

        if not self.lights:
            raise RuntimeError("No LIFX bulbs selected.")

        print()
        print("[LIFX] Selected bulbs for effects:")
        for light in self.lights:
            print(f"  - {self.safe_label(light)} @ {light.get_ip_addr()}")

    def load_groups(self):
        if not os.path.exists(self.groups_file):
            return {}

        try:
            with open(self.groups_file, "r", encoding="utf-8") as file:
                data = json.load(file)

            if not isinstance(data, dict):
                return {}

            return data

        except Exception as exc:
            print(f"[GROUP WARNING] Could not read {self.groups_file}: {exc}")
            return {}

    def save_groups(self, groups):
        try:
            with open(self.groups_file, "w", encoding="utf-8") as file:
                json.dump(groups, file, indent=4)

            print(f"[GROUP] Saved groups to {self.groups_file}")

        except Exception as exc:
            print(f"[GROUP ERROR] Could not save {self.groups_file}: {exc}")

    def prompt_for_group_or_selection(self, discovered_lights):
        groups = self.load_groups()

        while True:
            print()
            print("Light group options:")
            print("  all       = use all discovered bulbs")
            print("  new       = create a new saved group")
            print("  select    = select bulbs once without saving")
            print("  list      = show discovered bulbs again")

            if groups:
                print()
                print("Saved groups:")
                for group_name, labels in groups.items():
                    label_text = ", ".join(labels)
                    print(f"  {group_name} = {label_text}")

            print()

            choice = input("Choose group/options: ").strip()

            if not choice:
                print("Enter a saved group name, 'new', 'select', 'all', or 'list'.")
                continue

            choice_lower = choice.lower()

            if choice_lower == "all":
                return discovered_lights

            if choice_lower == "list":
                print()
                print("[LIFX] Discovered bulbs:")
                for index, light in enumerate(discovered_lights, start=1):
                    print(f"  [{index}] {self.safe_label(light)} @ {light.get_ip_addr()}")
                continue

            if choice_lower == "select":
                return self.prompt_for_light_selection(discovered_lights)

            if choice_lower == "new":
                selected_lights = self.prompt_for_light_selection(discovered_lights)

                group_name = input("Save this group as: ").strip()

                if not group_name:
                    print("Group name cannot be blank. Using selection without saving.")
                    return selected_lights

                labels = [self.safe_label(light) for light in selected_lights]
                groups[group_name] = labels
                self.save_groups(groups)

                return selected_lights

            if choice in groups:
                selected_lights = self.get_lights_by_saved_labels(discovered_lights, groups[choice])

                if selected_lights:
                    return selected_lights

                print(f"[GROUP WARNING] Saved group '{choice}' did not match any discovered bulbs.")
                print("Check if the bulbs are online or if their labels changed.")
                continue

            print(f"Unknown option or group: {choice}")

    def get_lights_by_saved_labels(self, discovered_lights, saved_labels):
        wanted = {label.lower() for label in saved_labels}
        selected = []
        found = set()

        for light in discovered_lights:
            label = self.safe_label(light)
            label_lower = label.lower()

            if label_lower in wanted:
                selected.append(light)
                found.add(label_lower)

        missing = wanted - found

        if missing:
            print()
            print("[GROUP WARNING] These saved bulbs were not discovered:")
            for label in sorted(missing):
                print(f"  - {label}")

        return selected

    def prompt_for_light_selection(self, discovered_lights):
        while True:
            print()
            print("Select which bulbs to use.")
            print("Examples:")
            print("  all       = use all bulbs")
            print("  1,3,5     = use bulbs 1, 3, and 5")
            print("  2         = use only bulb 2")
            print()

            choice = input("Bulbs to use: ").strip().lower()

            if choice == "all":
                return discovered_lights

            if not choice:
                print("No selection entered. Type 'all' or numbers like 1,3,5.")
                continue

            try:
                selected_indexes = []
                for part in choice.split(","):
                    number = int(part.strip())

                    if number < 1 or number > len(discovered_lights):
                        raise ValueError(f"Bulb number out of range: {number}")

                    selected_indexes.append(number)

                # Remove duplicates while keeping order.
                unique_indexes = []
                for number in selected_indexes:
                    if number not in unique_indexes:
                        unique_indexes.append(number)

                return [discovered_lights[number - 1] for number in unique_indexes]

            except ValueError as exc:
                print(f"Invalid selection: {exc}")
                print("Try again with something like: 1,3,5")

    def safe_label(self, light):
        cached = getattr(light, 'label', None)
        if cached:
            return cached
        try:
            return light.get_label()
        except Exception:
            return getattr(light, 'mac_addr', None) or "Unknown LIFX"

    def get_zone_count(self, light) -> int:
        """Return cached zone count for a light, 0 if not multizone."""
        return self._zone_counts.get(getattr(light, 'mac_addr', None), 0)

    def _scale_brightness(self, b: int) -> int:
        """Scale a brightness value into [brightness_min, brightness_max].
        Values ≤ 500 are treated as intentional dark/off frames and left alone."""
        if b <= 500:
            return b
        ratio = b / 65535.0
        scaled = self.brightness_min + (self.brightness_max - self.brightness_min) * ratio
        return max(1, min(65535, int(scaled)))

    @staticmethod
    def _eval_curve(pts: list, t: float) -> float:
        """Piecewise-linear interpolation on curve points [[t,v],...]. Returns 1.0 if no curve."""
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
        """Arm the intensity curve for effect_key, resetting the start clock."""
        label = _EFFECT_CURVE_LABEL.get(effect_key)
        curve = self.curves.get(label) if label else None
        if curve and len(curve.get('points', [])) >= 2:
            self._curve_pts  = curve['points']
            self._curve_dur  = max(0.001, curve.get('duration_ms', 2000) / 1000.0)
            self._curve_start = time.monotonic()
        else:
            self._curve_pts  = None
            self._curve_start = None

    def _deactivate_curve(self):
        self._curve_pts  = None
        self._curve_start = None

    def _effect_lights(self):
        """Return the subset of self.lights that should receive the current effect."""
        if not self.light_assignments or self._current_effect_key is None:
            return self.lights
        key = self._current_effect_key
        result = []
        for light in self.lights:
            label = self.safe_label(light)
            assignment = self.light_assignments.get(label)
            if assignment is None or key in assignment:
                result.append(light)
        return result

    def set_color_all(self, hsbk, duration_ms=50, stagger=True):
        """
        hsbk = [hue, saturation, brightness, kelvin]

        hue:
          red   = 0
          green = 21845
          blue  = 43690

        saturation:
          0     = white
          65535 = fully saturated color

        brightness:
          1 to 65535

        kelvin:
          normal white range is usually 2500-9000
        """
        scaled = list(hsbk)
        b = hsbk[2]
        if self._curve_pts is not None and self._curve_start is not None and b > 500:
            t = min(1.0, (time.monotonic() - self._curve_start) / self._curve_dur)
            b = max(1, min(65535, int(b * self._eval_curve(self._curve_pts, t))))
        scaled[2] = self._scale_brightness(b)

        if self.dry_run:
            print(f"[DRY RUN] Set LIFX color: hsbk={scaled}, duration_ms={duration_ms}")
            return

        lights = self._effect_lights()
        if self.sector_mode:
            # Multizone strips are reserved for the live sector display.
            lights = [l for l in lights if not isinstance(l, MultiZoneLight)]
        _dbg = self.debug_timing

        def _send(light):
            label = self.safe_label(light)
            t0 = time.perf_counter() if _dbg else None
            try:
                if isinstance(light, MultiZoneLight):
                    light.set_zone_color(0, 255, scaled, duration_ms, rapid=True)
                else:
                    light.set_color(scaled, duration_ms, rapid=True)
            except Exception as exc:
                msg = f"[LIFX ERROR] {label}: {exc}"
                print(msg)
                if self.log_callback:
                    self.log_callback(msg)
            if _dbg:
                print(f"[DBG] send {label}: {(time.perf_counter()-t0)*1000:.1f}ms", flush=True)

        t_start = time.perf_counter() if _dbg else None

        for i, light in enumerate(lights):
            _send(light)
            if stagger and self.stagger_ms > 0 and i < len(lights) - 1:
                time.sleep(self.stagger_ms / 1000.0)

        if _dbg:
            print(f"[DBG] set_color_all({len(lights)} lights, stagger={stagger and self.stagger_ms>0}): {(time.perf_counter()-t_start)*1000:.1f}ms total", flush=True)

    def set_active_effect(self, effect_name):
        with self._effect_lock:
            self._active_effect = effect_name

    def clear_active_effect(self):
        with self._effect_lock:
            self._active_effect = None

    def set_current_effect_key(self, key: str):
        self._current_effect_key = key
        self._activate_curve(key)

    def is_effect_active(self, effect_name):
        with self._effect_lock:
            return self._active_effect == effect_name

    def start_yellow_flash_effect(self):
        if self.is_effect_active("yellow_flash"):
            return

        print("[FLAG] Yellow flashing")
        self.set_active_effect("yellow_flash")

        thread = threading.Thread(
            target=self.yellow_flash_loop,
            daemon=True
        )
        thread.start()

    def yellow_flash_loop(self):
        yellow = [10922, 65535, 65535, 3500]
        dark = [0, 0, 1, 3500]

        while self.is_effect_active("yellow_flash"):
            self.set_color_all(yellow, duration_ms=40, stagger=False)
            time.sleep(0.45)

            if not self.is_effect_active("yellow_flash"):
                break

            self.set_color_all(dark, duration_ms=40, stagger=False)
            time.sleep(0.45)

    def start_lights(self, num_lights):
        self.clear_active_effect()
        self._current_effect_key = 'start_lights'
        self._activate_curve('start_lights')
        num_lights = max(0, min(5, num_lights))

        brightness_by_count = {0: 8000, 1: 16000, 2: 26000, 3: 38000, 4: 50000, 5: 65535}
        red  = [0, 65535, brightness_by_count[num_lights], 3500]
        dark = [0, 0, 100, 3500]

        print(f"[START LIGHTS] {num_lights}/5")

        if self.dry_run:
            print(f"[DRY RUN] Start lights {num_lights}/5")
            return

        lights = self._effect_lights()
        for i, light in enumerate(lights):
            try:
                if isinstance(light, MultiZoneLight) and self.mz_startlights_mode == "sweep":
                    zone_count = self.get_zone_count(light)
                    if zone_count > 0:
                        red_s  = list(red);  red_s[2]  = self._scale_brightness(red[2])
                        dark_s = list(dark); dark_s[2] = self._scale_brightness(dark[2])
                        lit = max(0, min(zone_count, round(num_lights / 5 * zone_count)))

                        if lit == 0:
                            # All dark
                            light.set_zone_color(0, zone_count - 1, dark_s, 40, rapid=True)
                        elif lit == zone_count:
                            # All red
                            light.set_zone_color(0, zone_count - 1, red_s, 40, rapid=True)
                        elif self.mz_startlights_direction == "ltr":
                            light.set_zone_color(0,   lit - 1,        red_s,  40, rapid=True)
                            light.set_zone_color(lit, zone_count - 1, dark_s, 40, rapid=True)
                        else:  # rtl
                            light.set_zone_color(0,                    zone_count - lit - 1, dark_s, 40, rapid=True)
                            light.set_zone_color(zone_count - lit, zone_count - 1,           red_s,  40, rapid=True)
                        continue
                # Regular bulb or solid-mode multizone — uniform color
                scaled = list(red)
                scaled[2] = self._scale_brightness(red[2])
                if isinstance(light, MultiZoneLight):
                    light.set_zone_color(0, 255, scaled, 40, rapid=True)
                else:
                    light.set_color(scaled, 40, rapid=True)
            except Exception as exc:
                msg = f"[LIFX ERROR] {self.safe_label(light)}: {exc}"
                print(msg)
                if self.log_callback:
                    self.log_callback(msg)
            if self.stagger_ms > 0 and i < len(lights) - 1:
                time.sleep(self.stagger_ms / 1000.0)

    def _sector_flag_hsbk(self, flag):
        """HSBK colour for a sector's flag — clear/green = green, caution = yellow."""
        b = self._scale_brightness(60000)
        if flag == FIA_FLAG_YELLOW:
            return [10922, 65535, b, 3500]   # amber
        if flag == FIA_FLAG_BLUE:
            return [43690, 65535, b, 3500]   # blue
        return [21845, 65535, b, 3500]       # none / green → all-clear green

    def sector_status(self, sector_flags):
        """Paint the three track sectors across multizone strips by flag colour.

        sector_flags = [s1, s2, s3] (FIA_FLAG_* values from
        parse_session_sector_flags). Only multizone strips with 3+ zones are
        painted — non-multizone lights keep the normal flag flash, so this method
        deliberately ignores them.
        """
        self.clear_active_effect()
        self._current_effect_key = 'sector_status'

        flags = (list(sector_flags or []) + [FIA_FLAG_NONE] * 3)[:3]
        colors = [self._sector_flag_hsbk(f) for f in flags]

        if self.dry_run:
            print(f"[SECTOR] flags={flags}")
            return

        for light in self._effect_lights():
            if not isinstance(light, MultiZoneLight):
                continue   # bulbs / non-strips are handled by the flag flash
            try:
                zc = self.get_zone_count(light)
                if zc < 3:
                    continue
                b1 = zc // 3
                b2 = (2 * zc) // 3
                light.set_zone_color(0,  b1 - 1, colors[0], 150, rapid=True)
                light.set_zone_color(b1, b2 - 1, colors[1], 150, rapid=True)
                light.set_zone_color(b2, zc - 1, colors[2], 150, rapid=True)
            except Exception as exc:
                msg = f"[LIFX ERROR] {self.safe_label(light)}: {exc}"
                print(msg)
                if self.log_callback:
                    self.log_callback(msg)

    def lights_out(self):
        self.clear_active_effect()
        self._current_effect_key = 'lights_out'
        self._activate_curve('lights_out')
        print("[LIGHTS OUT]")

        green = [21845, 65535, 65535, 3500]
        dark = [0, 0, 1, 3500]
        white = [0, 0, 50000, 4500]

        self.set_color_all(green, duration_ms=40)
        time.sleep(0.20)

        self.set_color_all(dark, duration_ms=40)
        time.sleep(0.15)

        self.set_color_all(green, duration_ms=40)
        time.sleep(0.35)

        self.set_color_all(white, duration_ms=800, stagger=False)
        self._deactivate_curve()

    def neutral(self):
        self.clear_active_effect()
        self._current_effect_key = 'neutral'
        self._activate_curve('neutral')
        print("[LIFX] Idle state")
        self.set_color_all(self.idle_hsbk, duration_ms=800, stagger=False)
        self._deactivate_curve()
        if self.idle_pulse:
            self.set_active_effect("idle_pulse")
            threading.Thread(target=self._idle_pulse_loop, daemon=True).start()

    def _idle_pulse_loop(self):
        base = list(self.idle_hsbk)
        max_b = self._scale_brightness(base[2])
        min_b = max(self.brightness_min if self.brightness_min > 0 else 1, int(max_b * 0.35))

        while self.is_effect_active("idle_pulse"):
            # Fade down over 2.5 s
            self.set_color_all([base[0], base[1], min_b, base[3]], duration_ms=2500, stagger=False)
            for _ in range(30):
                if not self.is_effect_active("idle_pulse"):
                    return
                time.sleep(0.1)

            # Fade up over 2.5 s
            self.set_color_all([base[0], base[1], max_b, base[3]], duration_ms=2500, stagger=False)
            for _ in range(30):
                if not self.is_effect_active("idle_pulse"):
                    return
                time.sleep(0.1)

    def yellow_flag(self):
        self._current_effect_key = 'yellow_flag'
        self._activate_curve('yellow_flag')
        self.start_yellow_flash_effect()

    def blue_flag(self):
        self.clear_active_effect()
        self._current_effect_key = 'blue_flag'
        self._activate_curve('blue_flag')
        print("[FLAG] Blue")
        self.set_active_effect("blue_pulse")
        threading.Thread(target=self._blue_pulse_loop, daemon=True).start()

    def _blue_pulse_loop(self):
        bright = [43690, 65535, 65535, 3500]
        dim    = [43690, 65535, 8000,  3500]
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
        self._current_effect_key = 'red_flag'
        self._activate_curve('red_flag')
        print("[FLAG] Red")
        self.set_active_effect("red_pulse")
        threading.Thread(target=self._red_pulse_loop, daemon=True).start()

    def _red_pulse_loop(self):
        bright = [0, 65535, 65535, 3500]
        dim    = [0, 65535, 8000,  3500]
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
        self._current_effect_key = 'black_flag'
        self._activate_curve('black_flag')
        print("[FLAG] Black")
        dark = [0, 0, 1, 3500]
        white_dim = [0, 0, 12000, 4500]
        self.flash_colors([dark, white_dim], loops=3, hold_ms=400)
        self.set_color_all(dark, duration_ms=500, stagger=False)
        self._deactivate_curve()

    def white_warning(self):
        self.clear_active_effect()
        self._current_effect_key = 'white_warning'
        self._activate_curve('white_warning')
        print("[WARNING] White flashing")
        white = [0, 0, 65535, 4500]
        dark = [0, 0, 1, 3500]
        self.flash_colors([white, dark], loops=3, hold_ms=250)
        self._deactivate_curve()
        self.neutral()

    def crash(self):
        self.clear_active_effect()
        self._current_effect_key = 'crash'
        print("[EVENT] Crash impact flash")
        # Single sharp white burst — distinct from white_warning's 3-pulse pattern
        white = [0, 0, 65535, 5500]
        dark  = [0, 0, 0, 3500]
        self.flash_colors([white, dark], loops=1, hold_ms=120)
        self.neutral()

    def fastest_lap(self):
        self._current_effect_key = 'fastest_lap'
        self._activate_curve('fastest_lap')
        print("[EVENT] Fastest lap - purple flash")
        _dbg = self.debug_timing
        t0 = time.perf_counter() if _dbg else None
        purple = [54613, 65535, 65535, 3500]
        dark = [0, 0, 1, 3500]
        self.flash_colors([purple, dark], loops=3, hold_ms=200)
        if _dbg:
            print(f"[DBG] fastest_lap flash done: {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
        self._deactivate_curve()
        self.neutral()

    def chequered_flag(self):
        self.clear_active_effect()
        self._current_effect_key = 'chequered_flag'
        self._activate_curve('chequered_flag')
        print("[FLAG] Chequered")
        _dbg = self.debug_timing
        t0 = time.perf_counter() if _dbg else None
        white = [0, 0, 65535, 4500]
        green = [21845, 65535, 65535, 3500]
        self.flash_colors([white, green], loops=5, hold_ms=300)
        if _dbg:
            print(f"[DBG] chequered_flag flash done: {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
        self._deactivate_curve()
        self.neutral()

    def apply_fia_flag(self, flag):
        if flag == FIA_FLAG_YELLOW:
            self.yellow_flag()
        elif flag == FIA_FLAG_BLUE:
            self.blue_flag()
        elif flag == FIA_FLAG_GREEN:
            # Green means track clear. Return to neutral.
            print("[FLAG] Green / clear")
            self.neutral()
        elif flag == FIA_FLAG_NONE:
            print("[FLAG] None / clear")
            self.neutral()

    def flash_colors(self, colors, loops=3, hold_ms=200):
        _dbg = self.debug_timing
        t0 = time.perf_counter() if _dbg else None
        for loop_i in range(loops):
            for color in colors:
                tc = time.perf_counter() if _dbg else None
                self.set_color_all(color, duration_ms=50)
                if _dbg:
                    print(f"[DBG] flash_colors loop={loop_i} set_color_all: {(time.perf_counter()-tc)*1000:.1f}ms", flush=True)
                time.sleep(hold_ms / 1000)
        if _dbg:
            print(f"[DBG] flash_colors total ({loops} loops, hold={hold_ms}ms): {(time.perf_counter()-t0)*1000:.1f}ms", flush=True)


# ============================================================
# F1 PACKET PARSING
# ============================================================
def parse_fastest_lap_details(data, header_size):
    """
    FTLP event details:
      uint8 vehicleIdx
      float lapTime
    """
    event_details_offset = header_size + 4
    if len(data) < event_details_offset + 5:
        return None

    vehicle_idx = data[event_details_offset]
    lap_time = struct.unpack_from("<f", data, event_details_offset + 1)[0]

    return {
        "vehicle_idx": vehicle_idx,
        "lap_time": lap_time,
    }

def parse_session_highest_marshal_flag(data, header_size):
    """
    Reads marshal zone flags from Session packet.

    MarshalZone:
      float m_zoneStart
      int8  m_zoneFlag

    m_zoneFlag:
      -1 = invalid/unknown
       0 = none
       1 = green
       2 = blue
       3 = yellow
    """
    num_marshal_zones_offset = header_size + 18
    marshal_zones_offset = header_size + 19

    if len(data) <= num_marshal_zones_offset:
        return None

    num_zones = data[num_marshal_zones_offset]
    num_zones = max(0, min(21, num_zones))

    flags_seen = set()

    for i in range(num_zones):
        flag_offset = (
            marshal_zones_offset
            + (i * MARSHAL_ZONE_SIZE)
            + MARSHAL_ZONE_FLAG_OFFSET
        )

        if len(data) <= flag_offset:
            continue

        flag = struct.unpack_from("<b", data, flag_offset)[0]

        if flag >= 0:
            flags_seen.add(flag)

    # Priority: yellow beats blue, blue beats green, green beats none.
    if FIA_FLAG_YELLOW in flags_seen:
        return FIA_FLAG_YELLOW

    if FIA_FLAG_BLUE in flags_seen:
        return FIA_FLAG_BLUE

    if FIA_FLAG_GREEN in flags_seen:
        return FIA_FLAG_GREEN

    if FIA_FLAG_NONE in flags_seen:
        return FIA_FLAG_NONE

    return None


# Marshal-flag priority for resolving the dominant flag in a track region.
_MARSHAL_FLAG_PRIORITY = {
    FIA_FLAG_NONE: 0,
    FIA_FLAG_GREEN: 1,
    FIA_FLAG_BLUE: 2,
    FIA_FLAG_YELLOW: 3,
}


def parse_session_sector_flags(data, header_size):
    """Map the Session packet's marshal-zone flags onto the three track sectors.

    Each MarshalZone is `float m_zoneStart (0.0–1.0 lap fraction)` + `int8 m_zoneFlag`.
    Zones are grouped into equal thirds by `m_zoneStart`; each sector takes the
    highest-priority flag among its zones. Returns ``[s1, s2, s3]`` (each a
    ``FIA_FLAG_*`` value), or ``None`` when the packet carries no usable zone data.

    Note: F1 telemetry exposes no true sector-boundary positions, so equal thirds of
    the lap fraction are used as the sector mapping — a deliberate approximation.
    """
    num_marshal_zones_offset = header_size + 18
    marshal_zones_offset = header_size + 19

    if len(data) <= num_marshal_zones_offset:
        return None

    num_zones = max(0, min(21, data[num_marshal_zones_offset]))
    if num_zones == 0:
        return None

    sectors = [FIA_FLAG_NONE, FIA_FLAG_NONE, FIA_FLAG_NONE]
    saw_zone = False

    for i in range(num_zones):
        base = marshal_zones_offset + (i * MARSHAL_ZONE_SIZE)
        if len(data) < base + MARSHAL_ZONE_SIZE:
            break

        zone_start = struct.unpack_from("<f", data, base)[0]
        flag = struct.unpack_from("<b", data, base + MARSHAL_ZONE_FLAG_OFFSET)[0]
        saw_zone = True
        if flag < 0:
            continue

        zs = min(max(zone_start, 0.0), 0.999999)
        idx = 0 if zs < (1 / 3) else (1 if zs < (2 / 3) else 2)
        if _MARSHAL_FLAG_PRIORITY.get(flag, 0) > _MARSHAL_FLAG_PRIORITY.get(sectors[idx], 0):
            sectors[idx] = flag

    return sectors if saw_zone else None

def parse_header(data):
    if len(data) < 2:
        return None

    packet_format = struct.unpack_from("<H", data, 0)[0]

    if packet_format in {2024, 2025}:
        sz = struct.calcsize(_HEADER_FORMAT_2425)
        if len(data) < sz:
            return None
        v = struct.unpack_from(_HEADER_FORMAT_2425, data, 0)
        return PacketHeader(
            packet_format=v[0], game_year=v[1], game_major_version=v[2],
            game_minor_version=v[3], packet_version=v[4], packet_id=v[5],
            session_uid=v[6], session_time=v[7], frame_identifier=v[8],
            overall_frame_identifier=v[9], player_car_index=v[10],
            secondary_player_car_index=v[11], header_size=sz,
        )

    if packet_format == 2023:
        sz = struct.calcsize(_HEADER_FORMAT_23)
        if len(data) < sz:
            return None
        v = struct.unpack_from(_HEADER_FORMAT_23, data, 0)
        return PacketHeader(
            packet_format=v[0], game_year=0, game_major_version=v[1],
            game_minor_version=v[2], packet_version=v[3], packet_id=v[4],
            session_uid=v[5], session_time=v[6], frame_identifier=v[7],
            overall_frame_identifier=v[8], player_car_index=v[9],
            secondary_player_car_index=v[10], header_size=sz,
        )

    if packet_format in {2021, 2022}:
        sz = struct.calcsize(_HEADER_FORMAT_2122)
        if len(data) < sz:
            return None
        v = struct.unpack_from(_HEADER_FORMAT_2122, data, 0)
        return PacketHeader(
            packet_format=v[0], game_year=0, game_major_version=v[1],
            game_minor_version=v[2], packet_version=v[3], packet_id=v[4],
            session_uid=v[5], session_time=v[6], frame_identifier=v[7],
            overall_frame_identifier=0, player_car_index=v[8],
            secondary_player_car_index=v[9], header_size=sz,
        )

    return None


def parse_event_code(data, header_size):
    event_details_offset = header_size + 4
    if len(data) < event_details_offset:
        return None

    raw_code = data[header_size:header_size + 4]

    try:
        return raw_code.decode("ascii")
    except UnicodeDecodeError:
        return None


def parse_start_lights_count(data, header_size):
    # STLG event detail is uint8 numLights, immediately after the 4-byte event code.
    event_details_offset = header_size + 4
    if len(data) <= event_details_offset:
        return 0

    return data[event_details_offset]

def parse_player_fia_flag(data, header):
    """
    Reads m_vehicleFiaFlags from the player's CarStatusData.

    Car Status packet:
      header = 29 bytes
      CarStatusData = 55 bytes each
      m_vehicleFiaFlags offset within CarStatusData = 28
    """
    player_index = header.player_car_index

    if player_index < 0 or player_index >= 22:
        return None

    offset = header.header_size + (player_index * CAR_STATUS_DATA_SIZE) + VEHICLE_FIA_FLAG_OFFSET_IN_CAR_STATUS

    if len(data) <= offset:
        return None

    return struct.unpack_from("<b", data, offset)[0]


def parse_penalty_details(data, header_size):
    """
    PENA event details:
      uint8 penaltyType
      uint8 infringementType
      uint8 vehicleIdx
      uint8 otherVehicleIdx
      uint8 time
      uint8 lapNum
      uint8 placesGained
    """
    event_details_offset = header_size + 4
    if len(data) < event_details_offset + 7:
        return None

    return {
        "penalty_type": data[event_details_offset],
        "infringement_type": data[event_details_offset + 1],
        "vehicle_idx": data[event_details_offset + 2],
        "other_vehicle_idx": data[event_details_offset + 3],
        "time": data[event_details_offset + 4],
        "lap_num": data[event_details_offset + 5],
        "places_gained": data[event_details_offset + 6],
    }


def parse_retirement_details(data, header_size):
    """
    RTMT event details:
      uint8 vehicleIdx
      uint8 reason

    reason 6 = black flagged
    reason 7 = red flagged
    """
    event_details_offset = header_size + 4
    if len(data) < event_details_offset + 2:
        return None

    return {
        "vehicle_idx": data[event_details_offset],
        "reason": data[event_details_offset + 1],
    }

# IP ranges that belong to VPNs / overlay networks, not the local LAN.
# Tailscale uses the CGNAT block 100.64.0.0/10.
_VPN_RANGES = [
    ipaddress.ip_network('100.64.0.0/10'),   # Tailscale
]

# Adapter-name fragments that indicate a virtual / non-LAN interface. Used only
# to *deprioritise* (try last), never to exclude — some users do run LIFX over
# bridged virtual adapters.
_VIRTUAL_ADAPTER_HINTS = (
    "hyper-v", "virtual", "vethernet", "vmware", "loopback", "wsl",
    "tailscale", "wireguard", "zerotier", "tun", "tap",
)


def _lan_source_ips():
    """Local IPv4 source addresses for LIFX discovery, physical-NIC first.

    Excludes loopback, APIPA (169.254/16), and known VPN/overlay ranges so the
    discovery broadcast egresses a real LAN NIC rather than a tunnel. Virtual
    adapters are sorted last so a physical NIC is tried first. Uses ifaddr (a
    lifxlan dependency, always present) with a hostname-resolution fallback.
    """
    candidates = []  # [(ip, is_virtual)]
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            nice = (adapter.nice_name or "").lower()
            is_virtual = any(hint in nice for hint in _VIRTUAL_ADAPTER_HINTS)
            for a in adapter.ips:
                if not a.is_IPv4:
                    continue
                ip = a.ip
                try:
                    addr = ipaddress.ip_address(ip)
                except ValueError:
                    continue
                if addr.is_loopback or ip.startswith("169.254."):
                    continue
                if any(addr in net for net in _VPN_RANGES):
                    print(f"[LIFX] Skipping VPN/overlay interface: {ip} ({adapter.nice_name})",
                          flush=True)
                    continue
                candidates.append((ip, is_virtual))
    except Exception:
        # Fallback: hostname resolution (no adapter names → no virtual detection).
        try:
            for *_, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = sockaddr[0]
                addr = ipaddress.ip_address(ip)
                if addr.is_loopback or ip.startswith("169.254."):
                    continue
                if any(addr in net for net in _VPN_RANGES):
                    continue
                candidates.append((ip, False))
        except Exception as exc:
            print(f"[LIFX] Interface enumeration error: {exc}", flush=True)

    seen, physical, virtual = set(), [], []
    for ip, is_virtual in candidates:
        if ip in seen:
            continue
        seen.add(ip)
        (virtual if is_virtual else physical).append(ip)
    return physical + virtual


class _BoundLifxLAN(LifxLAN):
    """LifxLAN whose discovery/broadcast socket is bound to a specific source IP.

    Stock lifxlan binds to INADDR_ANY, letting the OS pick the egress interface by
    route metric. On multi-homed hosts (Tailscale/VPN, Hyper-V, WSL) it can pick a
    tunnel or virtual adapter, so LIFX broadcasts never reach the LAN — and on
    Windows a broadcast to a VPN interface can return WinError 10054, aborting
    discovery entirely (issue #1). Binding the source IP forces the real LAN NIC.
    """

    def __init__(self, num_lights=None, verbose=False, source_ip=None):
        super().__init__(num_lights, verbose)
        self._source_ip = source_ip

    def initialize_socket(self, timeout):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        s.bind((self._source_ip or "", 0))
        self.sock = s


class F1LifxBridgeCore:
    def __init__(
        self,
        udp_ip="0.0.0.0",
        udp_port=20777,
        bulb_count=40,
        dry_run=False,
        log_callback=None,
    ):
        self.udp_ip = udp_ip
        self.udp_port = udp_port
        self.bulb_count = bulb_count
        self.dry_run = dry_run
        self.log_callback = log_callback

        self.lifx = None
        self.nanoleaf = None  # NanoleafController, set externally by BridgeRunner
        self.hue = None       # HueController, set externally by BridgeRunner
        self.sock = None
        self.running = False

        # Bridge-level effect loop — one loop drives both LIFX and Nanoleaf
        # so they stay perfectly in sync instead of drifting independently.
        self._bridge_effect: str | None = None
        self._bridge_effect_lock = threading.Lock()

        self.last_start_light_count = None
        self.last_lights_out_time = 0.0
        self.last_fia_flag = None
        self.last_marshal_flag = None
        self.race_started = False
        self.total_packets = 0
        self.event_packets = 0

        # Last [s1,s2,s3] sector flags painted, so we only re-paint on change.
        self._last_sector_flags = None

        # None = all events enabled. Set to a frozenset of string keys to restrict.
        self.enabled_events = None

        # UDP forwarding
        self.forward_enabled = False
        self.forward_host = "127.0.0.1"
        self.forward_port = 20778
        self._fwd_sock = None

    def is_event_enabled(self, name: str) -> bool:
        return self.enabled_events is None or name in self.enabled_events

    def _sector_status_active(self) -> bool:
        """Live sector status is opt-in: only active when explicitly enabled.

        Unlike other events (on by default when enabled_events is None), this one
        must be deliberately switched on, because it replaces the yellow/blue-flag
        flash on multizone strips rather than adding to it.
        """
        return self.enabled_events is not None and "sector_status" in self.enabled_events

    def log(self, message):
        print(message)
        if self.log_callback:
            self.log_callback(message)

    def _fire(self, method: str, *args):
        """Call a named effect method on every active controller (LIFX + Nanoleaf).

        Both controllers are started in parallel so time-based animations
        (sleep loops in lights_out, fastest_lap, etc.) run simultaneously.
        """
        threads = []
        if self.lifx is not None:
            def _lifx_call(ctrl=self.lifx):
                try:
                    getattr(ctrl, method)(*args)
                except Exception as exc:
                    self.log(f"[LIFX ERROR] {method}: {exc}")
            t = threading.Thread(target=_lifx_call, daemon=True)
            t.start()
            threads.append(t)
        if self.nanoleaf is not None:
            def _nl_call(ctrl=self.nanoleaf):
                try:
                    getattr(ctrl, method)(*args)
                except Exception as exc:
                    self.log(f"[NANOLEAF ERROR] {method}: {exc}")
            threading.Thread(target=_nl_call, daemon=True).start()
        if self.hue is not None:
            def _hue_call(ctrl=self.hue):
                try:
                    getattr(ctrl, method)(*args)
                except Exception as exc:
                    self.log(f"[HUE ERROR] {method}: {exc}")
            threading.Thread(target=_hue_call, daemon=True).start()
        # Wait for LIFX to finish so the caller's timing is preserved.
        for t in threads:
            t.join()

    # ── Bridge-level synchronized effect loops ───────────────────────────────
    # These drive LIFX + Nanoleaf from a single timing loop so both controllers
    # receive set_color_all() calls at the same instant instead of running
    # independent loops that drift over time.

    def _set_bridge_effect(self, name: str):
        with self._bridge_effect_lock:
            self._bridge_effect = name

    def _clear_bridge_effect(self):
        with self._bridge_effect_lock:
            self._bridge_effect = None

    def _is_bridge_effect(self, name: str) -> bool:
        with self._bridge_effect_lock:
            return self._bridge_effect == name

    def yellow_flag_bridge(self):
        self._clear_bridge_effect()
        self._fire("clear_active_effect")
        self._fire("set_current_effect_key", "yellow_flag")
        self._set_bridge_effect("yellow_flash")
        threading.Thread(target=self._yellow_flash_bridge_loop, daemon=True).start()

    def _yellow_flash_bridge_loop(self):
        yellow = [10922, 65535, 65535, 3500]
        # Keep same hue/sat as yellow — only brightness changes.
        # Nanoleaf's brightness has duration=0 so the flash is instant.
        # LIFX at brightness 200/65535 (~0.3%) is effectively off.
        dark   = [10922, 65535, 200, 3500]
        while self._is_bridge_effect("yellow_flash"):
            self._fire("set_color_all", yellow, 40, False)
            time.sleep(0.45)
            if not self._is_bridge_effect("yellow_flash"):
                break
            self._fire("set_color_all", dark, 40, False)
            time.sleep(0.45)

    def blue_flag_bridge(self):
        self._clear_bridge_effect()
        self._fire("clear_active_effect")
        self._fire("set_current_effect_key", "blue_flag")
        self._set_bridge_effect("blue_pulse")
        threading.Thread(target=self._blue_pulse_bridge_loop, daemon=True).start()

    def _blue_pulse_bridge_loop(self):
        bright = [43690, 65535, 65535, 3500]
        dim    = [43690, 65535, 8000, 3500]
        while self._is_bridge_effect("blue_pulse"):
            self._fire("set_color_all", bright, 600, False)
            for _ in range(7):
                if not self._is_bridge_effect("blue_pulse"):
                    return
                time.sleep(0.1)
            self._fire("set_color_all", dim, 600, False)
            for _ in range(7):
                if not self._is_bridge_effect("blue_pulse"):
                    return
                time.sleep(0.1)

    def red_flag_bridge(self):
        self._clear_bridge_effect()
        self._fire("clear_active_effect")
        self._fire("set_current_effect_key", "red_flag")
        self._set_bridge_effect("red_pulse")
        threading.Thread(target=self._red_pulse_bridge_loop, daemon=True).start()

    def _red_pulse_bridge_loop(self):
        bright = [0, 65535, 65535, 3500]
        dim    = [0, 65535, 8000, 3500]
        while self._is_bridge_effect("red_pulse"):
            self._fire("set_color_all", bright, 600, False)
            for _ in range(7):
                if not self._is_bridge_effect("red_pulse"):
                    return
                time.sleep(0.1)
            self._fire("set_color_all", dim, 600, False)
            for _ in range(7):
                if not self._is_bridge_effect("red_pulse"):
                    return
                time.sleep(0.1)

    def neutral_bridge(self):
        """Stop any active bridge loop and return all lights to idle."""
        self._clear_bridge_effect()
        self._fire("neutral")

    def discover_lights(self):
        self.lifx = LocalLifxController(
            bulb_count=self.bulb_count,
            select_in_console=False,
            use_saved_groups=False,
            dry_run=self.dry_run,
            log_callback=self.log_callback,
        )
        return self.lifx.lights

    def set_lights(self, lights):
        if self.lifx is None:
            self.lifx = LocalLifxController(
                bulb_count=self.bulb_count,
                select_in_console=False,
                use_saved_groups=False,
                dry_run=self.dry_run,
                log_callback=self.log_callback,
            )

        self.lifx.lights = lights

    def start(self):
        if self.running:
            self.log("[BRIDGE] Already running.")
            return

        if self.lifx is None:
            self.discover_lights()

        self.running = True
        self.listener_loop()

    def stop(self):
        self.running = False

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None

        if self._fwd_sock:
            try:
                self._fwd_sock.close()
            except Exception:
                pass
        self._fwd_sock = None

        self.log("[BRIDGE] Stopped.")

    def listener_loop(self):
        self.log("===================================================")
        self.log("GridGlow")
        self.log("===================================================")
        self.log(f"UDP listener: {self.udp_ip}:{self.udp_port}")
        self.log(f"DRY_RUN: {self.dry_run}")
        self.log(f"LIFX_BULB_COUNT: {self.bulb_count}")
        self.log(f"F1 header size: {HEADER_SIZE} bytes")
        self.log("===================================================")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.udp_ip, self.udp_port))
        self.sock.settimeout(0.5)

        self._fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.log("Waiting for F1 25 UDP packets...")

        while self.running:
            try:
                data, sender = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break

            if self.forward_enabled and self._fwd_sock:
                try:
                    self._fwd_sock.sendto(data, (self.forward_host, self.forward_port))
                except Exception:
                    pass

            self.handle_packet(data)

        self.log("[BRIDGE] Listener loop ended.")

    def handle_packet(self, data):
        self.total_packets += 1

        header = parse_header(data)
        if header is None:
            return

        if header.packet_format not in SUPPORTED_PACKET_FORMATS:
            return

        if self.total_packets % 250 == 0:
            self.log(
                f"[HEARTBEAT] packets={self.total_packets}, "
                f"last_packet_id={header.packet_id}, "
                f"session_time={header.session_time:.2f}"
            )

        if header.packet_id == PACKET_ID_SESSION:
            self.handle_session_packet(data, header)
            return

        if header.packet_id == PACKET_ID_CAR_STATUS:
            self.handle_car_status_packet(data, header)
            return

        if header.packet_id == PACKET_ID_EVENT:
            self.handle_event_packet(data, header)
            return

    def handle_session_packet(self, data, header):
        # Live sector status (opt-in): paint the three sectors from the Session
        # packet's per-zone flags, re-painting only when they change. Multizone
        # strips are reserved for this (LocalLifxController.sector_mode); the
        # normal flag flash below still drives every non-multizone light.
        active = self._sector_status_active()
        if self.lifx is not None:
            self.lifx.sector_mode = active   # keep the strip reservation in sync

        if active:
            flags = parse_session_sector_flags(data, header.header_size)
            if flags is not None and flags != self._last_sector_flags:
                self.log(f"[SECTOR STATUS] {flags}")
                self._last_sector_flags = flags
                self._fire("sector_status", flags)

        marshal_flag = parse_session_highest_marshal_flag(data, header.header_size)

        if (
            self.race_started
            and marshal_flag is not None
            and marshal_flag != self.last_marshal_flag
        ):
            self.log(f"[MARSHAL FLAG] {FIA_FLAG_NAMES.get(marshal_flag, marshal_flag)}")

            if marshal_flag == FIA_FLAG_YELLOW:
                if self.is_event_enabled("yellow_flag"):
                    self.yellow_flag_bridge()

            elif marshal_flag == FIA_FLAG_BLUE:
                if self.is_event_enabled("blue_flag"):
                    self.blue_flag_bridge()

            elif marshal_flag in {FIA_FLAG_GREEN, FIA_FLAG_NONE}:
                if self.is_event_enabled("neutral"):
                    self.neutral_bridge()

            self.last_marshal_flag = marshal_flag

    def handle_car_status_packet(self, data, header):
        # The player-flag flash keeps running for non-multizone lights even while
        # sector status is active — multizone strips are protected by sector_mode.
        fia_flag = parse_player_fia_flag(data, header)

        if fia_flag is not None and fia_flag != self.last_fia_flag:
            self.log(f"[FIA FLAG] {FIA_FLAG_NAMES.get(fia_flag, fia_flag)}")

            if fia_flag == FIA_FLAG_YELLOW:
                if self.is_event_enabled("yellow_flag"):
                    self.yellow_flag_bridge()
            elif fia_flag == FIA_FLAG_BLUE:
                if self.is_event_enabled("blue_flag"):
                    self.blue_flag_bridge()
            elif fia_flag in {FIA_FLAG_NONE, FIA_FLAG_GREEN}:
                if self.is_event_enabled("neutral"):
                    self.neutral_bridge()

            self.last_fia_flag = fia_flag

    def handle_event_packet(self, data, header):
        self.event_packets += 1

        event_code = parse_event_code(data, header.header_size)
        if event_code is None:
            return

        interesting_events = {
            EVENT_START_LIGHTS,
            EVENT_LIGHTS_OUT,
            EVENT_RED_FLAG,
            EVENT_CHEQUERED_FLAG,
            EVENT_PENALTY,
            EVENT_RETIREMENT,
            EVENT_FASTEST_LAP,
            "SSTA",
            "SEND",
        }

        if event_code in interesting_events:
            self.log(
                f"[EVENT] code={event_code}, "
                f"session_time={header.session_time:.3f}, "
                f"frame={header.frame_identifier}"
            )

        if event_code == EVENT_START_LIGHTS:
            num_lights = parse_start_lights_count(data, header.header_size)

            if num_lights != self.last_start_light_count:
                if self.is_event_enabled("start_lights"):
                    self._clear_bridge_effect()
                    self._fire("start_lights", num_lights)
                self.last_start_light_count = num_lights

        elif event_code == EVENT_LIGHTS_OUT:
            now = time.time()

            if now - self.last_lights_out_time > 3.0:
                if self.is_event_enabled("lights_out"):
                    self._clear_bridge_effect()
                    self._fire("lights_out")
                self.last_lights_out_time = now
                self.last_start_light_count = None
                self.last_marshal_flag = None
                self.race_started = True

        elif event_code == "SSTA":
            self.last_start_light_count = None
            self.last_marshal_flag = None
            self.last_fia_flag = None
            self._last_sector_flags = None
            self.race_started = False
            if self.is_event_enabled("neutral"):
                self.neutral_bridge()

        elif event_code == "SEND":
            self.last_start_light_count = None
            self.last_marshal_flag = None
            self.last_fia_flag = None
            self._last_sector_flags = None
            self.race_started = False
            if self.is_event_enabled("neutral"):
                self.neutral_bridge()

        elif event_code == EVENT_RED_FLAG:
            self.last_start_light_count = None
            if self.is_event_enabled("red_flag"):
                self.red_flag_bridge()

        elif event_code == EVENT_CHEQUERED_FLAG:
            self.last_start_light_count = None
            self.race_started = False
            if self.is_event_enabled("chequered_flag"):
                self._clear_bridge_effect()
                self._fire("chequered_flag")

        elif event_code == EVENT_FASTEST_LAP:
            fastest_lap = parse_fastest_lap_details(data, header.header_size)

            if fastest_lap is not None:
                vehicle_idx = fastest_lap["vehicle_idx"]
                lap_time = fastest_lap["lap_time"]
                player_idx = header.player_car_index

                self.log(
                    f"[FASTEST LAP] vehicle={vehicle_idx}, "
                    f"player={player_idx}, "
                    f"lap_time={lap_time:.3f}s"
                )

                if vehicle_idx == player_idx:
                    if self.is_event_enabled("fastest_lap"):
                        self._clear_bridge_effect()
                        self._fire("fastest_lap")
                else:
                    self.log("[FASTEST LAP] Ignored - not player")

        elif event_code == EVENT_PENALTY:
            penalty = parse_penalty_details(data, header.header_size)

            if penalty is not None:
                infringement = penalty["infringement_type"]
                vehicle_idx = penalty["vehicle_idx"]

                self.log(
                    f"[PENALTY] vehicle={vehicle_idx}, "
                    f"infringement={infringement}, "
                    f"lap={penalty['lap_num']}"
                )

                if infringement in BLACK_FLAG_INFRINGEMENTS:
                    if self.is_event_enabled("black_flag"):
                        self._clear_bridge_effect()
                        self._fire("black_flag")

                elif infringement in WHITE_WARNING_INFRINGEMENTS:
                    if self.is_event_enabled("white_warning"):
                        self._clear_bridge_effect()
                        self._fire("white_warning")

        elif event_code == EVENT_RETIREMENT:
            retirement = parse_retirement_details(data, header.header_size)

            if retirement is not None:
                reason = retirement["reason"]
                vehicle_idx = retirement["vehicle_idx"]

                self.log(f"[RETIREMENT] vehicle={vehicle_idx}, reason={reason}")

                if reason == 6:
                    if self.is_event_enabled("black_flag"):
                        self._clear_bridge_effect()
                        self._fire("black_flag")
                elif reason == 7:
                    if self.is_event_enabled("red_flag"):
                        self.red_flag_bridge()

# ============================================================
# MAIN LOOP
# ============================================================

def main():
    bridge = F1LifxBridgeCore(
        udp_ip=UDP_IP,
        udp_port=UDP_PORT,
        bulb_count=LIFX_BULB_COUNT,
        dry_run=DRY_RUN,
    )

    try:
        bridge.start()
    except KeyboardInterrupt:
        bridge.stop()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:")
        print(exc)
        input("Press Enter to close...")
