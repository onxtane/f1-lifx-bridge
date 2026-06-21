import socket
import struct
import time

from bridge_core import F1LifxBridgeCore

# DiRT Rally 2.0 extradata=3 packet: 66 × f32, little-endian, 264 bytes.
_DR2_FORMAT = '<66f'
_DR2_SIZE   = struct.calcsize(_DR2_FORMAT)  # 264

# Field indices (byte offset = index × 4)
_F_RUN_TIME      = 0   # wall-clock time since loading screen (s)
_F_LAP_TIME      = 1   # stage timer; > 0 while stage running (s)
_F_SECTOR        = 48  # current split index (0, 1, 2) as float
_F_LAST_LAP_TIME = 62  # populates when stage finishes; 0 otherwise (s)
_F_RPM           = 37  # engine RPM ÷ 10
_F_MAX_RPM       = 63  # max RPM ÷ 10


class DR2BridgeCore(F1LifxBridgeCore):
    """UDP listener for DiRT Rally 2.0 (264-byte Codemasters telemetry format).

    Inherits all controller management and bridge loop infrastructure from
    F1LifxBridgeCore; only the listener loop and packet handler are replaced.

    Effects mapping
    ---------------
    Stage start        → lights_out  (white flash, "go" signal)
    Split checkpoint   → fastest_lap (purple flash)
    Stage finish       → chequered_flag (white/green celebration)
    Service park/menu  → neutral    (return to idle)
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._dr2_in_stage       = False
        self._dr2_last_sector    = -1
        self._dr2_last_lap_time  = 0.0

    def listener_loop(self):
        self.log("===================================================")
        self.log("GridGlow — DiRT Rally 2.0")
        self.log("===================================================")
        self.log(f"UDP listener: {self.udp_ip}:{self.udp_port}")
        self.log("DRY_RUN: " + str(self.dry_run))
        self.log("In-game: Options → Accessibility → UDP Telemetry → Enabled")
        self.log(f"         IP: 127.0.0.1 (or this PC's LAN IP), Port: {self.udp_port}")
        self.log(f"         extradata = 3")
        self.log("===================================================")
        self.log("Waiting for DiRT Rally 2.0 UDP packets...")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.udp_ip, self.udp_port))
        self.sock.settimeout(0.5)

        self._fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        while self.running:
            try:
                data, _ = self.sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break

            if self.forward_enabled and self._fwd_sock:
                try:
                    self._fwd_sock.sendto(data, (self.forward_host, self.forward_port))
                except Exception:
                    pass

            self._handle_dr2(data)

        self.log("[DR2] Listener loop ended.")

    def _handle_dr2(self, data: bytes):
        if len(data) < _DR2_SIZE:
            return

        self.total_packets += 1

        try:
            f = struct.unpack_from(_DR2_FORMAT, data, 0)
        except struct.error:
            return

        if self.total_packets % 500 == 0:
            self.log(
                f"[DR2 HEARTBEAT] packets={self.total_packets}, "
                f"run_time={f[_F_RUN_TIME]:.1f}s, "
                f"lap_time={f[_F_LAP_TIME]:.2f}s"
            )

        lap_time      = f[_F_LAP_TIME]
        sector        = int(f[_F_SECTOR])
        last_lap_time = f[_F_LAST_LAP_TIME]

        # lap_time > 0 means the stage timer is running.
        in_stage = lap_time > 0.05

        # ── Stage start ─────────────────────────────────────────────────────
        if in_stage and not self._dr2_in_stage:
            self.log(f"[DR2] Stage started (lap_time={lap_time:.2f}s)")
            self._dr2_last_sector   = sector
            self._dr2_last_lap_time = 0.0
            if self.is_event_enabled("lights_out"):
                self._clear_bridge_effect()
                self._fire("lights_out")

        if in_stage:
            # ── Stage finish ─────────────────────────────────────────────────
            # last_lap_time populates (was 0, now > 0) when the stage timer stops.
            if last_lap_time > 0.0 and self._dr2_last_lap_time <= 0.0:
                self.log(f"[DR2] Stage finished — time={last_lap_time:.3f}s")
                self._dr2_last_lap_time = last_lap_time
                if self.is_event_enabled("chequered_flag"):
                    self._clear_bridge_effect()
                    self._fire("chequered_flag")

            # ── Split checkpoint ──────────────────────────────────────────────
            # sector increments 0 → 1 → 2 as splits are crossed.
            elif sector > self._dr2_last_sector >= 0 and self._dr2_last_lap_time <= 0.0:
                self.log(f"[DR2] Split {sector} crossed")
                self._dr2_last_sector = sector
                if self.is_event_enabled("fastest_lap"):
                    self._clear_bridge_effect()
                    self._fire("fastest_lap")

        # ── Service park / menus ─────────────────────────────────────────────
        if not in_stage and self._dr2_in_stage:
            self.log("[DR2] Returned to service park / menus")
            if self.is_event_enabled("neutral"):
                self.neutral_bridge()

        self._dr2_in_stage = in_stage
