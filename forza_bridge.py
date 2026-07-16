import socket
import struct
import time

from bridge_core import F1LifxBridgeCore

# Forza "Data Out" UDP telemetry (little-endian). The Sled section (bytes 0–231)
# is byte-for-byte identical across Forza Horizon 5, Forza Horizon 6, and Forza
# Motorsport, so race start / return-to-menus work on all three. FH6 inserts 3
# fields at bytes 232–243, shifting the Dash by +12, and only FH6 carries the
# collision field the crash effect needs.
#
# Enable in-game: Settings → HUD and Gameplay → DATA OUT = ON, port 5300.
#
# CAVEAT (#52 / #54): the sizes below come from the published spec, not from a
# packet anyone here has actually seen. If FH5 turns out to emit a 324-byte Dash
# (the Horizon family is documented elsewhere as inserting its 12 bytes too),
# it would land inside the FH6 window and offset 236 would be read as a
# collision delta when it is really a position coordinate. The heartbeat logs
# the observed size for exactly this reason — anyone running a Forza title can
# report what their game really sends. _looks_like_impact() keeps the failure
# survivable in the meantime.

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
_FH6_MAX   = 339    # anything larger isn't a layout we know; don't guess at it

# Crash detection (FH6 SmashableVelDiff)
_CRASH_VELDIFF_THRESHOLD = 8.0   # m/s collision delta to count as a crash impact
_CRASH_COOLDOWN_S        = 3.0
_CRASH_VELDIFF_SANE_MAX  = 200.0  # ~720 km/h of delta: not a collision, a misread


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
        self._forza_prev_veldiff = 0.0
        self._forza_logged_size = None

    def listener_loop(self):
        self.log("===================================================")
        self.log("GridGlow — Forza (Data Out)")
        self.log("===================================================")
        self.log(f"UDP listener: {self.udp_ip}:{self.udp_port}")
        self.log("DRY_RUN: " + str(self.dry_run))
        self.log("In-game: Settings -> HUD and Gameplay -> DATA OUT = ON")
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

        # Which Forza layout is actually on the wire. Logged once per size seen,
        # because the FH5 / FH6 split is decided purely by length and nobody has
        # yet confirmed it against a running game (#52 / #54) — a user reporting
        # this line is what settles it.
        if len(data) != self._forza_logged_size:
            self._forza_logged_size = len(data)
            self.log(f"[FORZA] Packet size {len(data)} bytes -> "
                     f"{self._describe_layout(len(data))}")

        if self.total_packets % 500 == 0:
            try:
                rpm = struct.unpack_from('<f', data, _F_CURRENT_RPM)[0]
            except struct.error:
                rpm = 0.0
            self.log(
                f"[FORZA HEARTBEAT] packets={self.total_packets}, "
                f"race_on={int(race_on)}, rpm={rpm:.0f}, bytes={len(data)}"
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
        if race_on and _FH6_MIN <= len(data) <= _FH6_MAX:
            try:
                veldiff = struct.unpack_from('<f', data, _F_FH6_SMASH_VELDIFF)[0]
            except struct.error:
                veldiff = 0.0
            if self._looks_like_impact(veldiff):
                self.log(f"[FORZA] Crash - dV={veldiff:.1f} m/s")
                self._forza_crash_cooldown = time.time()
                if self.is_event_enabled("crash"):
                    self._clear_bridge_effect()
                    self._fire("crash")
            self._forza_prev_veldiff = veldiff

    @staticmethod
    def _describe_layout(size: int) -> str:
        """Name the layout a packet size implies, in the user's terms."""
        if size == _SLED_SIZE:
            return "Sled (all Forza titles) - race start/end only"
        if size < _FH6_MIN:
            return "Car Dash (Horizon 5 / Motorsport) - race start/end only"
        if size <= _FH6_MAX:
            return "Horizon 6 Car Dash - race start/end + crash"
        return "unrecognised - please report this size (#52)"

    def _looks_like_impact(self, veldiff: float) -> bool:
        """True if this reads like a real collision rather than a misread field.

        A collision delta rests at ~0 and spikes for a frame; a coordinate sits
        persistently high. Requiring the *previous* sample to be below the
        threshold turns this into an edge detector, so if we ever have the
        layout wrong and 236 is really a position, a car sitting 50 m up a hill
        flashes once rather than every cooldown forever (#52).
        """
        if not (_CRASH_VELDIFF_THRESHOLD < veldiff < _CRASH_VELDIFF_SANE_MAX):
            return False                                   # noise, NaN, or nonsense
        if self._forza_prev_veldiff > _CRASH_VELDIFF_THRESHOLD:
            return False                                   # already high: not an edge
        return time.time() - self._forza_crash_cooldown > _CRASH_COOLDOWN_S
