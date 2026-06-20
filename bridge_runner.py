import importlib.util
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

if getattr(sys, 'frozen', False):
    # Settings/groups files live next to the EXE, not inside the bundle.
    _BASE_DIR = Path(sys.executable).parent
else:
    _BASE_DIR = Path(__file__).resolve().parent
GROUPS_FILE = str(_BASE_DIR / "lifx_groups.json")
GUI_SETTINGS_FILE = str(_BASE_DIR / "f1lifx_gui_settings.json")

try:
    from nanoleaf_controller import (
        NanoleafController,
        discover_nanoleaf,
        load_nanoleaf_settings,
        save_nanoleaf_settings,
    )
    _NANOLEAF_AVAILABLE = True
except ImportError:
    NanoleafController = None
    _NANOLEAF_AVAILABLE = False

    def discover_nanoleaf(*_a, **_kw):
        return []

    def load_nanoleaf_settings():
        return {}

    def save_nanoleaf_settings(_data):
        pass


class BridgeRunner:
    """
    Runs bridge_core.F1LifxBridgeCore in-process, on a background thread,
    instead of as a subprocess. bridge_core.py already accepts a
    log_callback - that's the hook this uses, so there's no text
    protocol to invent: Quick Effects and stats are direct Python calls
    and attribute reads.

    bridge_core.py is imported lazily, by file path, the first time it's
    needed. That way a missing file, or 'lifxlan' not being installed,
    shows up as a log line in the GUI instead of crashing the app before
    the window even opens.
    """

    # Looping effects use bridge-level methods so LIFX and Nanoleaf are driven
    # by a single shared loop (no drift).  One-shot effects call _clear_bridge_effect
    # so any running loop stops before the one-shot plays.
    BRIDGE_EFFECT_METHODS = {
        "Yellow Flag": lambda bridge: bridge.yellow_flag_bridge(),
        "Blue Flag":   lambda bridge: bridge.blue_flag_bridge(),
        "Red Flag":    lambda bridge: bridge.red_flag_bridge(),
        "Neutral":     lambda bridge: bridge.neutral_bridge(),
    }
    LIFX_EFFECT_METHODS = {
        "Start Lights":   lambda lifx: lifx.start_lights(5),
        "Lights Out":     lambda lifx: lifx.lights_out(),
        "Fastest Lap":    lambda lifx: lifx.fastest_lap(),
        "Chequered Flag": lambda lifx: lifx.chequered_flag(),
        "White Warning":  lambda lifx: lifx.white_warning(),
        "Black Flag":     lambda lifx: lifx.black_flag(),
    }

    def __init__(self,
                 on_log=None, on_state_change=None, on_status_text=None,
                 on_stat=None, on_lights_discovered=None, on_discovering=None,
                 on_selection_changed=None):

        self.on_log = on_log or (lambda line: None)
        self.on_state_change = on_state_change or (lambda running: None)
        self.on_status_text = on_status_text or (lambda text: None)
        self.on_stat = on_stat or (lambda key, value: None)
        self.on_lights_discovered = on_lights_discovered or (lambda lights: None)
        self.on_discovering = on_discovering or (lambda active: None)
        self.on_selection_changed = on_selection_changed or (lambda labels: None)

        self.bridge = None   # F1LifxBridgeCore instance, created on first use
        self._module = None
        self._active_group_name = None
        self._pending_enabled_events = None  # applied to bridge on first construction
        self._pending_brightness = None     # (min_b, max_b) in HSBK units
        self._pending_stagger = None        # (enabled, ms)
        self._pending_idle = None           # (hsbk, pulse)
        self._pending_forwarding = None       # (enabled, host, port)
        self._pending_listen = None           # (ip, port)
        self._pending_mz_startlights = None  # (direction, mode)

        self._light_assignments = {}  # {label: None | [effect_keys]}

        self._nanoleaf_settings: dict = load_nanoleaf_settings()

        self._stat_stop = threading.Event()
        self._stat_thread = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self.bridge is not None and self.bridge.running

    # ---- module / bridge bootstrapping ----

    def _ensure_bridge(self) -> bool:
        if self.bridge is not None:
            # Bridge exists but may have been stopped. Apply any pending listen
            # address change so listener_loop() binds to the updated IP/port.
            if self._pending_listen is not None:
                self.bridge.udp_ip, self.bridge.udp_port = self._pending_listen
            return True

        if self._module is None:
            try:
                import bridge_core as _bc
                self._module = _bc
            except ImportError as exc:
                tb = traceback.format_exc()
                self.on_log(f"ERROR: bridge_core could not be imported: {exc}")
                for line in tb.splitlines():
                    self.on_log(line)
                self.on_status_text("Import Error")
                return False
            except (Exception, SystemExit) as exc:
                tb = traceback.format_exc()
                self.on_log(f"ERROR: bridge_core failed to load: {exc}")
                for line in tb.splitlines():
                    self.on_log(line)
                self.on_status_text("Import Error")
                return False

        listen_ip, listen_port = self._pending_listen or (self._module.UDP_IP, self._module.UDP_PORT)
        try:
            self.bridge = self._module.F1LifxBridgeCore(
                udp_ip=listen_ip,
                udp_port=listen_port,
                bulb_count=self._module.LIFX_BULB_COUNT,
                dry_run=self._module.DRY_RUN,
                log_callback=self.on_log,
            )
        except Exception as exc:
            self.on_log(f"ERROR: could not construct bridge: {exc}")
            self.on_status_text("Error")
            return False

        if self._pending_enabled_events is not None:
            self.bridge.enabled_events = self._pending_enabled_events

        if self._pending_stagger is not None and self.bridge.lifx is not None:
            enabled, ms = self._pending_stagger
            self.bridge.lifx.stagger_ms = ms if enabled else 0

        if self._pending_idle is not None and self.bridge.lifx is not None:
            hsbk, pulse = self._pending_idle
            self.bridge.lifx.idle_hsbk = hsbk
            self.bridge.lifx.idle_pulse = pulse

        if self._pending_forwarding is not None:
            enabled, host, port = self._pending_forwarding
            self.bridge.forward_enabled = enabled
            self.bridge.forward_host = host
            self.bridge.forward_port = port

        if self._pending_mz_startlights is not None:
            direction, mode = self._pending_mz_startlights
            if self.bridge.lifx is not None:
                self.bridge.lifx.mz_startlights_direction = direction
                self.bridge.lifx.mz_startlights_mode = mode
            if self.bridge.nanoleaf is not None:
                self.bridge.nanoleaf.mz_startlights_direction = direction
                self.bridge.nanoleaf.mz_startlights_mode = mode

        if self._light_assignments and self.bridge.lifx is not None:
            self.bridge.lifx.light_assignments = self._light_assignments

        self._connect_nanoleaf_if_configured()

        return True

    # ---- GUI-facing actions ----

    def start(self):
        with self._lock:
            if self.is_running():
                self.on_log("Bridge is already running.")
                return
            if not self._ensure_bridge():
                self.on_state_change(False)
                return

        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        if not self.is_running():
            self.on_log("Bridge is already stopped.")
            return
        self.bridge.stop()

    def discover_lights(self):
        threading.Thread(target=self._discover_worker, daemon=True).start()

    def test_selected(self):
        threading.Thread(target=self._test_worker, daemon=True).start()

    def identify_light(self, label: str):
        threading.Thread(target=self._identify_worker, args=(label,), daemon=True).start()

    def test_multizone(self):
        threading.Thread(target=self._test_mz_worker, daemon=True).start()

    def trigger_effect(self, name: str):
        threading.Thread(target=self._effect_worker, args=(name,), daemon=True).start()

    def get_discovered_lights(self) -> list[dict]:
        """Return [{label, ip, zones, type}, ...] for every discovered device.
        zones > 0 means the device is a multizone strip.
        type is 'lifx' or 'nanoleaf'."""
        nanoleaf_ip = self._nanoleaf_settings.get('ip', '').strip()
        # Also use the live controller IP — covers the case where settings
        # weren't loaded at startup (fresh machine, first run).
        if not nanoleaf_ip and self.bridge is not None and self.bridge.nanoleaf is not None:
            nanoleaf_ip = getattr(self.bridge.nanoleaf, 'ip', '') or ''
        result = []
        if self.bridge is not None and self.bridge.lifx is not None:
            for light in self.bridge.lifx.discovered_lights:
                try:
                    ip = light.get_ip_addr() or ""
                except Exception:
                    ip = ""
                # Exclude the Nanoleaf device if lifxlan happens to pick it up.
                if nanoleaf_ip and ip == nanoleaf_ip:
                    continue
                try:
                    label = light.get_label() or "Unknown LIFX"
                except Exception:
                    label = "Unknown LIFX"
                zones = self.bridge.lifx.get_zone_count(light)
                result.append({"label": label, "ip": ip, "zones": zones, "type": "lifx"})
        # Append connected Nanoleaf as a virtual always-active entry.
        if self.bridge is not None and self.bridge.nanoleaf is not None:
            nl = self.bridge.nanoleaf
            info = nl.device_info
            name = info.get('name', 'Nanoleaf') if info else 'Nanoleaf'
            model = info.get('model_name', '') if info else ''
            label = f"{name} ({model})" if model and model.lower() not in name.lower() else name
            result.append({"label": label, "ip": nl.ip, "zones": 0, "type": "nanoleaf"})
        return result

    def set_selected_lights(self, labels: list[str], group_name: str | None = None):
        """Set active lights to those matching the given label list."""
        if self.bridge is None or self.bridge.lifx is None:
            self.on_log("[GUI] No lights discovered yet — cannot apply selection.")
            return
        wanted = {l.lower() for l in labels}
        selected = [
            light for light in self.bridge.lifx.discovered_lights
            if self._safe_label(light).lower() in wanted
        ]
        self.bridge.lifx.lights = selected
        self._active_group_name = group_name
        self.on_log(f"[GUI] Applied {len(selected)} selected light(s) to bridge.")
        self._push_light_stats()
        self.on_selection_changed(self._get_active_labels())

    # ---- group persistence ----

    def set_idle_state(self, color_hex: str, pulse: bool):
        hsbk = self._hex_to_hsbk(color_hex)
        if self.bridge is not None and self.bridge.lifx is not None:
            self.bridge.lifx.idle_hsbk = hsbk
            self.bridge.lifx.idle_pulse = pulse
            if not pulse and self.bridge.lifx.is_effect_active("idle_pulse"):
                self.bridge.lifx.clear_active_effect()
        self._pending_idle = (hsbk, pulse)

    @staticmethod
    def _hex_to_hsbk(hex_color: str) -> list:
        hex_color = hex_color.lstrip('#')
        r, g, b = (int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))
        max_c, min_c = max(r, g, b), min(r, g, b)
        delta = max_c - min_c
        if delta == 0:
            h = 0.0
        elif max_c == r:
            h = ((g - b) / delta) % 6
        elif max_c == g:
            h = (b - r) / delta + 2
        else:
            h = (r - g) / delta + 4
        h = (h / 6.0) % 1.0
        s = 0.0 if max_c == 0 else delta / max_c
        return [int(h * 65535), int(s * 65535), int(max_c * 65535), 3500]

    def set_mz_startlights(self, direction: str, mode: str):
        """direction: 'ltr' | 'rtl'.  mode: 'sweep' | 'solid'."""
        self._pending_mz_startlights = (direction, mode)
        if self.bridge is not None and self.bridge.lifx is not None:
            self.bridge.lifx.mz_startlights_direction = direction
            self.bridge.lifx.mz_startlights_mode = mode
        if self.bridge is not None and self.bridge.nanoleaf is not None:
            self.bridge.nanoleaf.mz_startlights_direction = direction
            self.bridge.nanoleaf.mz_startlights_mode = mode

    def set_debug_timing(self, enabled: bool):
        if self.bridge is not None and self.bridge.lifx is not None:
            self.bridge.lifx.debug_timing = enabled

    # ---- Nanoleaf management ----

    def _connect_nanoleaf_if_configured(self):
        """Auto-connect Nanoleaf if settings are present and enabled."""
        if not _NANOLEAF_AVAILABLE or self.bridge is None:
            return
        cfg = self._nanoleaf_settings
        if not cfg.get("enabled"):
            return
        ip = cfg.get("ip", "")
        token = cfg.get("auth_token", "")
        if not ip or not token:
            return
        ctrl = NanoleafController.try_connect(ip, token, log_callback=self.on_log)
        self.bridge.nanoleaf = ctrl  # None if connection failed
        if ctrl is not None:
            # Propagate multizone start-lights settings.
            if self._pending_mz_startlights is not None:
                direction, mode = self._pending_mz_startlights
                ctrl.mz_startlights_direction = direction
                ctrl.mz_startlights_mode = mode
            # Apply custom panel order for sweep effects.
            layout = self.get_nanoleaf_layout()
            if layout and layout.get("panels"):
                ctrl.update_panel_order(layout["panels"])
            if ctrl.device_info:
                cfg["device_info"] = ctrl.device_info
                self._nanoleaf_settings = cfg
                save_nanoleaf_settings(cfg)

    def get_nanoleaf_settings(self) -> dict:
        return dict(self._nanoleaf_settings)

    def get_nanoleaf_layout(self) -> dict | None:
        """Return panel layout for the UI.

        Fetches the raw device layout from the live controller, then overlays
        any saved custom positions from nanoleaf_settings.json.
        Falls back to the cached device_layout in settings if the device is
        not currently connected.
        """
        layout = None
        if self.bridge is not None and self.bridge.nanoleaf is not None:
            layout = self.bridge.nanoleaf.get_panel_layout()
            if layout and layout.get("panels"):
                self._nanoleaf_settings["device_layout"] = layout
                save_nanoleaf_settings(self._nanoleaf_settings)

        if not layout or not layout.get("panels"):
            layout = self._nanoleaf_settings.get("device_layout")

        if not layout or not layout.get("panels"):
            return None

        custom = self._nanoleaf_settings.get("custom_layout")
        if custom:
            custom_map = {p["panelId"]: p for p in custom}
            panels = []
            for p in layout["panels"]:
                cp = custom_map.get(p["panelId"])
                if cp:
                    panels.append({**p, "x": cp.get("x", p["x"]), "y": cp.get("y", p["y"])})
                else:
                    panels.append(dict(p))
            layout = {**layout, "panels": panels, "hasCustom": True}

        return layout

    def save_nanoleaf_layout(self, panels: list):
        """Persist user-arranged panel positions."""
        self._nanoleaf_settings["custom_layout"] = panels
        save_nanoleaf_settings(self._nanoleaf_settings)
        if self.bridge is not None and self.bridge.nanoleaf is not None:
            self.bridge.nanoleaf.update_panel_order(panels)

    def reset_nanoleaf_layout(self):
        """Discard custom layout and return to device default."""
        self._nanoleaf_settings.pop("custom_layout", None)
        save_nanoleaf_settings(self._nanoleaf_settings)
        return self.get_nanoleaf_layout()

    def get_nanoleaf_device_info(self) -> dict:
        """Return detected device info (name, model, model_name, firmware, num_panels)."""
        if self.bridge is not None and self.bridge.nanoleaf is not None:
            return dict(self.bridge.nanoleaf.device_info)
        return {}

    def save_nanoleaf_settings_data(self, data: dict):
        self._nanoleaf_settings = data
        save_nanoleaf_settings(data)

    def pair_nanoleaf(self, ip: str) -> dict:
        """
        Request a new auth token from the Nanoleaf at ip.
        The user must hold the power button for 5-7 s first.
        Returns {ok, token} or {ok, error}.
        """
        if not _NANOLEAF_AVAILABLE:
            return {"ok": False, "error": "nanoleafapi is not installed."}
        token = NanoleafController.pair(ip)
        if token:
            cfg = {"ip": ip, "auth_token": token, "enabled": True}
            self._nanoleaf_settings = cfg
            save_nanoleaf_settings(cfg)
            if self.bridge is not None:
                ctrl = NanoleafController.try_connect(ip, token, log_callback=self.on_log)
                self.bridge.nanoleaf = ctrl
                if ctrl is not None and ctrl.device_info:
                    cfg["device_info"] = ctrl.device_info
                    self._nanoleaf_settings = cfg
                    save_nanoleaf_settings(cfg)
            # Re-push lights so the Nanoleaf entry appears immediately in the UI.
            self.on_lights_discovered(self.get_discovered_lights())
            return {"ok": True, "token": token, "device_info": cfg.get("device_info", {})}
        return {"ok": False, "error": "Pairing failed. Hold the power button for 5-7 s and try again."}

    def discover_nanoleaf_devices(self, timeout: int = 5) -> list:
        return discover_nanoleaf(timeout)

    def set_nanoleaf_enabled(self, enabled: bool):
        self._nanoleaf_settings["enabled"] = enabled
        save_nanoleaf_settings(self._nanoleaf_settings)
        if self.bridge is not None:
            if not enabled:
                self.bridge.nanoleaf = None
                self.on_log("[NANOLEAF] Disabled.")
            elif self.bridge.nanoleaf is None:
                self._connect_nanoleaf_if_configured()

    def set_listen_address(self, ip: str, port: int):
        self._pending_listen = (ip, int(port))
        if self.is_running():
            threading.Thread(target=self._restart_for_listen, daemon=True).start()

    def _restart_for_listen(self):
        self.bridge.stop()
        # Wait for listener_loop to exit so the port is released
        for _ in range(30):
            if not self.bridge.running:
                break
            time.sleep(0.1)
        self.bridge = None
        self.start()

    def set_forwarding(self, enabled: bool, host: str, port: int):
        if self.bridge is not None:
            self.bridge.forward_enabled = enabled
            self.bridge.forward_host = host
            self.bridge.forward_port = int(port)
        self._pending_forwarding = (enabled, host, int(port))

    def set_stagger(self, enabled: bool, ms: int):
        self._pending_stagger = (enabled, int(ms))
        if self.bridge is not None and self.bridge.lifx is not None:
            self.bridge.lifx.stagger_ms = int(ms) if enabled else 0

    def set_brightness_range(self, min_pct: int, max_pct: int):
        """Set master brightness range. min_pct and max_pct are 0–100."""
        min_b = int(min_pct / 100 * 65535)
        max_b = int(max_pct / 100 * 65535)
        self._pending_brightness = (min_b, max_b)
        if self.bridge is not None and self.bridge.lifx is not None:
            self.bridge.lifx.brightness_min = min_b
            self.bridge.lifx.brightness_max = max_b
        if self.bridge is not None and self.bridge.nanoleaf is not None:
            self.bridge.nanoleaf.brightness_min = min_b
            self.bridge.nanoleaf.brightness_max = max_b

    def set_light_assignments(self, assignments: dict):
        """assignments: {label: None | [effect_keys]}.  None = all effects."""
        self._light_assignments = assignments or {}
        if self.bridge is not None and self.bridge.lifx is not None:
            self.bridge.lifx.light_assignments = self._light_assignments

    def set_enabled_events(self, names: list[str] | None):
        """Pass None to enable everything, or a list of event key strings to restrict."""
        enabled = frozenset(names) if names is not None else None
        if self.bridge is not None:
            self.bridge.enabled_events = enabled
        # Store so it can be applied when the bridge is created later.
        self._pending_enabled_events = enabled

    def get_groups(self) -> dict:
        return self._read_json(GROUPS_FILE, {})

    def save_group(self, name: str, labels: list[str]):
        groups = self.get_groups()
        groups[name] = labels
        self._write_json(GROUPS_FILE, groups)
        self.on_log(f"[GROUP] Saved group '{name}' ({len(labels)} light(s)).")

    def delete_group(self, name: str):
        groups = self.get_groups()
        if name in groups:
            del groups[name]
            self._write_json(GROUPS_FILE, groups)
            self.on_log(f"[GROUP] Deleted group '{name}'.")

    def load_group(self, name: str):
        """Apply a saved group to the bridge and persist it as the last-used group."""
        groups = self.get_groups()
        if name not in groups:
            self.on_log(f"[GROUP] Group '{name}' not found.")
            return
        labels = groups[name]
        self.set_selected_lights(labels, group_name=name)
        self.save_gui_settings({"last_group": name})
        self.on_log(f"[GROUP] Loaded group: {name} ({len(labels)} light(s))")

    # ---- GUI settings persistence ----

    def get_gui_settings(self) -> dict:
        return self._read_json(GUI_SETTINGS_FILE, {})

    def save_gui_settings(self, data: dict):
        current = self.get_gui_settings()
        current.update(data)
        self._write_json(GUI_SETTINGS_FILE, current)

    # ---- background workers ----

    def _run(self):
        if self.bridge.lifx is None:
            try:
                self._do_discover()
            except Exception as exc:
                self.on_log(f"ERROR during discovery: {exc}")
                self.on_state_change(False)
                self.on_status_text("Error")
                return
        else:
            # Lights already discovered — still auto-apply last group if none active.
            self._maybe_apply_last_group()
            self._push_light_stats()
            self.on_lights_discovered(self.get_discovered_lights())

        self.on_state_change(True)
        self.on_status_text("Running")

        self._stat_stop.clear()
        self._stat_thread = threading.Thread(target=self._stat_loop, daemon=True)
        self._stat_thread.start()

        try:
            self.bridge.start()  # blocks until bridge.stop()
        except Exception as exc:
            self.on_log(f"ERROR: bridge crashed: {exc}")
        finally:
            self._stat_stop.set()
            self.on_state_change(False)
            self.on_status_text("Stopped")

    def _discover_worker(self):
        if not self._ensure_bridge():
            return
        try:
            self._do_discover()
        except Exception as exc:
            self.on_log(f"ERROR during discovery: {exc}")
        finally:
            self.on_discovering(False)

    def _maybe_apply_last_group(self):
        """Apply the last saved group if no group is currently active."""
        if self._active_group_name is not None:
            return
        settings = self.get_gui_settings()
        last_group = settings.get("last_group")
        groups = self.get_groups()
        if last_group and last_group in groups:
            labels = groups[last_group]
            wanted = {l.lower() for l in labels}
            selected = [
                light for light in self.bridge.lifx.discovered_lights
                if self._safe_label(light).lower() in wanted
            ]
            if selected:
                self.bridge.lifx.lights = selected
                self._active_group_name = last_group
                self.on_log(f"[GROUP] Auto-loaded last group: {last_group} ({len(selected)} light(s))")
                self.on_selection_changed(self._get_active_labels())

    def _do_discover(self):
        """Discover lights, auto-apply last saved group, then push to UI."""
        self.on_discovering(True)
        self.on_status_text("Discovering lights...")
        self.bridge.discover_lights()

        settings = self.get_gui_settings()
        last_group = settings.get("last_group")
        groups = self.get_groups()

        if last_group and last_group in groups:
            labels = groups[last_group]
            wanted = {l.lower() for l in labels}
            selected = [
                light for light in self.bridge.lifx.discovered_lights
                if self._safe_label(light).lower() in wanted
            ]
            if selected:
                self.bridge.lifx.lights = selected
                self._active_group_name = last_group
                self.on_log(f"[GROUP] Auto-loaded last group: {last_group} ({len(selected)} light(s))")

        if self._pending_brightness is not None and self.bridge.lifx is not None:
            self.bridge.lifx.brightness_min, self.bridge.lifx.brightness_max = self._pending_brightness
        if self._pending_brightness is not None and self.bridge.nanoleaf is not None:
            self.bridge.nanoleaf.brightness_min, self.bridge.nanoleaf.brightness_max = self._pending_brightness

        if self._light_assignments and self.bridge.lifx is not None:
            self.bridge.lifx.light_assignments = self._light_assignments

        self._push_light_stats()
        self.on_lights_discovered(self.get_discovered_lights())
        self.on_selection_changed(self._get_active_labels())
        self.on_discovering(False)
        self.on_status_text("Stopped")

    def _identify_worker(self, label: str):
        if self.bridge is None or self.bridge.lifx is None:
            self.on_log("No lights discovered yet — click Discover Lights first.")
            return
        try:
            self.bridge.lifx.identify_light(label)
        except Exception as exc:
            self.on_log(f"ERROR during identify: {exc}")

    def _test_worker(self):
        if self.bridge is None or (self.bridge.lifx is None and self.bridge.nanoleaf is None):
            self.on_log("No lights discovered yet - click Discover Lights first.")
            return
        try:
            for num_lights in range(0, 6):
                self.bridge._fire("start_lights", num_lights)
                time.sleep(0.5)
        except Exception as exc:
            self.on_log(f"ERROR during test: {exc}")

    def _test_mz_worker(self):
        if self.bridge is None or (self.bridge.lifx is None and self.bridge.nanoleaf is None):
            self.on_log("No lights discovered yet - click Discover Lights first.")
            return
        threads = []
        if self.bridge.lifx is not None:
            def _lifx_mz():
                try:
                    self.bridge.lifx.multizone_color_test()
                except Exception as exc:
                    self.on_log(f"ERROR during multizone test (LIFX): {exc}")
            t = threading.Thread(target=_lifx_mz, daemon=True)
            t.start()
            threads.append(t)
        if self.bridge.nanoleaf is not None:
            def _nl_mz():
                try:
                    self.bridge.nanoleaf.multizone_color_test()
                except Exception as exc:
                    self.on_log(f"ERROR during multizone test (Nanoleaf): {exc}")
            t = threading.Thread(target=_nl_mz, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def _effect_worker(self, name):
        if self.bridge is None or self.bridge.lifx is None:
            self.on_log(f"Cannot run '{name}' - no lights discovered yet. Click Discover Lights first.")
            return

        # Looping effects (yellow/blue/red flag, neutral) go through the bridge
        # so LIFX and Nanoleaf are driven by one shared loop.
        bridge_action = self.BRIDGE_EFFECT_METHODS.get(name)
        if bridge_action is not None:
            try:
                bridge_action(self.bridge)
            except Exception as exc:
                self.on_log(f"ERROR running '{name}': {exc}")
            return

        # One-shot effects: stop any running loop, then fire on both controllers.
        lifx_action = self.LIFX_EFFECT_METHODS.get(name)
        if lifx_action is None:
            self.on_log(f"Unknown effect: {name}")
            return

        self.bridge._clear_bridge_effect()

        def _run_lifx():
            try:
                lifx_action(self.bridge.lifx)
            except Exception as exc:
                self.on_log(f"ERROR running '{name}': {exc}")

        def _run_nanoleaf():
            try:
                lifx_action(self.bridge.nanoleaf)
            except Exception as exc:
                self.on_log(f"[NANOLEAF ERROR] running '{name}': {exc}")

        t_lifx = threading.Thread(target=_run_lifx, daemon=True)
        t_lifx.start()
        if self.bridge.nanoleaf is not None:
            threading.Thread(target=_run_nanoleaf, daemon=True).start()
        t_lifx.join()

    def _stat_loop(self):
        last_packets = -1
        while not self._stat_stop.is_set():
            if self.bridge is not None and self.bridge.total_packets != last_packets:
                last_packets = self.bridge.total_packets
                self.on_stat("f1_packets", str(last_packets))
            time.sleep(1.0)

    def _push_light_stats(self):
        if self.bridge is not None and self.bridge.lifx is not None:
            found = len(self.bridge.lifx.discovered_lights)
            selected = len(self.bridge.lifx.lights)
            self.on_stat("lifx_lights", f"{found} found")
            if self._active_group_name:
                self.on_stat("active_group", self._active_group_name)
            else:
                self.on_stat("active_group", f"{selected} selected")

    def _get_active_labels(self) -> list[str]:
        if self.bridge is None or self.bridge.lifx is None:
            return []
        return [self._safe_label(l) for l in self.bridge.lifx.lights]

    def _safe_label(self, light) -> str:
        try:
            return light.get_label() or "Unknown LIFX"
        except Exception:
            return "Unknown LIFX"

    # ---- JSON helpers ----

    def _read_json(self, path: str, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, type(default)) else default
        except Exception as exc:
            self.on_log(f"[WARNING] Could not read {path}: {exc}")
            return default

    def _write_json(self, path: str, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as exc:
            self.on_log(f"[ERROR] Could not write {path}: {exc}")
