import socket
import struct
import time

from bridge_core import F1LifxBridgeCore

# Forza "Data Out" UDP telemetry (little-endian). The Sled section (bytes 0–231)
# is byte-for-byte identical across Forza Horizon 5, Forza Horizon 6, and Forza
# Motorsport. FH6 inserts 3 fields at bytes 232–243, shifting the Dash by +12.
#
# Enable in-game: Settings → HUD and Gameplay → DATA OUT = ON, port 5300.

_FORZA_PORT = 5300

# ── Sled offsets (shared by all Forza titles) ────────────────────────────────
_F_IS_RACE_ON     = 0    # s32 — 1 while actively driving, 0 in menus/paused/replay
_F_ENGINE_MAX_RPM = 8    # f32
_F_CURRENT_RPM    = 16   # f32

# ── FH6-only Dash-prefix fields (bytes 232–243) ──────────────────────────────
_F_FH6_SMASH_VELDIFF = 236  # f32 — collision velocity delta (m/s); spikes on impact

# ── Packet-size detection ────────────────────────────────────────────────────
_SLED_SIZE = 232    # minimum valid packet (Sled only)
_FH6_MIN   = 323    # FH6 packets are 323–339 bytes (some builds pad to 324)

# Crash detection (FH6 SmashableVelDiff)
_CRASH_VELDIFF_THRESHOLD = 8.0   # m/s collision delta to count as a crash impact
_CRASH_COOLDOWN_S        = 3.0


class ForzaBridgeCore(F1LifxBridgeCore):
    """UDP listener for Forza 'Data Out' telemetry (FH5 / FH6 / Forza Motorsport).

    Inherits all controller management and bridge-loop infrastructure from
    F1LifxBridgeCore; only the listener loop and packet handler are replaced.

    Forza emits no flags/start-lights, so effects are physics/telemetry-driven:

    Race start (IsRaceOn 0 → 1)        → lights_out  (green "go")
    Crash      (FH6 SmashableVelDiff)  → crash flash
    Race end   (IsRaceOn 1 → 0)        → neutral
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Forza's default Data Out port is 5300; only override the F1 default so a
        # user-set port is still respected.
        if self.udp_port == 20777:
            self.udp_port = _FORZA_PORT
        self._forza_race_on = False
        self._forza_crash_cooldown = 0.0

    def listener_loop(self):
        self.log("===================================================")
        self.log("GridGlow — Forza (Data Out)")
        self.log("===================================================")
        self.log(f"UDP listener: {self.udp_ip}:{self.udp_port}")
        self.log("DRY_RUN: " + str(self.dry_run))
        self.log("In-game: Settings → HUD and Gameplay → DATA OUT = ON")
        self.log(f"         IP: this PC's LAN IP, Port: {self.udp_port}")
        self.log("===================================================")
        self.log("Waiting for Forza UDP packets...")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.udp_ip, self.udp_port))
        self.sock.settimeout(0.5)

        self._fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        while self.running:
            try:
                data, _ = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break

            if self.forward_enabled and self._fwd_sock:
                try:
                    self._fwd_sock.sendto(data, (self.forward_host, self.forward_port))
                except Exception:
                    pass

            self._handle_forza(data)

        self.log("[FORZA] Listener loop ended.")

    def _handle_forza(self, data: bytes):
        if len(data) < _SLED_SIZE:
            return

        self.total_packets += 1

        try:
            is_race_on = struct.unpack_from('<i', data, _F_IS_RACE_ON)[0]
        except struct.error:
            return

        race_on = is_race_on != 0

        if self.total_packets % 500 == 0:
            try:
                rpm = struct.unpack_from('<f', data, _F_CURRENT_RPM)[0]
            except struct.error:
                rpm = 0.0
            self.log(
                f"[FORZA HEARTBEAT] packets={self.total_packets}, "
                f"race_on={int(race_on)}, rpm={rpm:.0f}"
            )

        # ── Race start ───────────────────────────────────────────────────────
        if race_on and not self._forza_race_on:
            self.log("[FORZA] Race on")
            if self.is_event_enabled("lights_out"):
                self._clear_bridge_effect()
                self._fire("lights_out")

        # ── Race end / return to menus ───────────────────────────────────────
        elif not race_on and self._forza_race_on:
            self.log("[FORZA] Race off")
            if self.is_event_enabled("neutral"):
                self.neutral_bridge()

        self._forza_race_on = race_on

        # ── Crash impact (FH6 only — needs the SmashableVelDiff field) ───────
        if race_on and len(data) >= _FH6_MIN:
            try:
                veldiff = struct.unpack_from('<f', data, _F_FH6_SMASH_VELDIFF)[0]
            except struct.error:
                veldiff = 0.0
            now = time.time()
            if (veldiff > _CRASH_VELDIFF_THRESHOLD
                    and now - self._forza_crash_cooldown > _CRASH_COOLDOWN_S):
                self.log(f"[FORZA] Crash — ΔV={veldiff:.1f} m/s")
                self._forza_crash_cooldown = now
                if self.is_event_enabled("crash"):
                    self._clear_bridge_effect()
                    self._fire("crash")
