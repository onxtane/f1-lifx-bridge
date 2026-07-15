import socket
import struct

from bridge_core import F1LifxBridgeCore

# EA SPORTS WRC "session_update" packet (GridGlow structure, data schema 3).
# Little-endian, channels serialised in the order defined by assets/wrc/gridglow.json.
# Offsets/types confirmed against the community parser
# (github.com/arttusalminen/WRC-Telemetry). GridGlow only reads a few channels;
# the packet is ~126 bytes with the full channel list.
_WRC_4CC = b"SESU"                 # packet_4cc tag for session_update

_F_SPEED         = 45              # vehicle_speed              float32 (m/s)
_F_RPM_MAX       = 53              # vehicle_engine_rpm_max     float32
_F_RPM_CURRENT   = 61              # vehicle_engine_rpm_current float32
_F_STAGE_TIME    = 85              # stage_current_time         float32 (s)
_F_STAGE_STATUS  = 101             # stage_result_status        uint8  (0 running, 1 finished)
_F_STAGE_PROGRESS = 102            # stage_progress             float32 (0..1)

# Deepest field GridGlow reads ends at 106; require at least that many bytes.
_WRC_MIN_SIZE = 106

_STAGE_STATUS_FINISHED = 1
_STAGE_TIME_EPS = 0.05             # stage timer running above this


class WRCBridgeCore(F1LifxBridgeCore):
    """UDP listener for EA SPORTS WRC (configurable JSON telemetry).

    Inherits all controller management and bridge-loop infrastructure from
    F1LifxBridgeCore; only the listener loop and packet handler are replaced.
    Requires the GridGlow telemetry structure installed in-game — see
    assets/wrc/README.md.

    Effects mapping
    ---------------
    Stage start        -> lights_out     (white flash, "go" signal)
    Split checkpoint   -> fastest_lap    (purple flash, at each third of the stage)
    Stage finish       -> chequered_flag (celebration)
    Return to service  -> neutral        (return to idle)

    Crash and redline effects are deferred: the shipped structure carries no
    acceleration channel, and speed-drop alone false-positives on braking.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wrc_in_stage      = False
        self._wrc_last_third    = 0      # 0..2 — which third of the stage we last reported
        self._wrc_finish_fired  = False

    def listener_loop(self):
        self.log("===================================================")
        self.log("GridGlow — EA SPORTS WRC")
        self.log("===================================================")
        self.log(f"UDP listener: {self.udp_ip}:{self.udp_port}")
        self.log("DRY_RUN: " + str(self.dry_run))
        self.log("Install the GridGlow telemetry structure first:")
        self.log("  Documents\\My Games\\WRC\\telemetry\\udp\\gridglow.json")
        self.log(f"  then enable it in config.json on port {self.udp_port}.")
        self.log("===================================================")
        self.log("Waiting for EA SPORTS WRC UDP packets...")

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

            self._handle_wrc(data)

        self.log("[WRC] Listener loop ended.")

    def _handle_wrc(self, data: bytes):
        # Ignore anything that isn't the session_update packet we asked for.
        if len(data) < _WRC_MIN_SIZE or data[0:4] != _WRC_4CC:
            return

        self.total_packets += 1

        try:
            stage_time = struct.unpack_from("<f", data, _F_STAGE_TIME)[0]
            status     = struct.unpack_from("<B", data, _F_STAGE_STATUS)[0]
            progress   = struct.unpack_from("<f", data, _F_STAGE_PROGRESS)[0]
        except struct.error:
            return

        if self.total_packets % 500 == 0:
            self.log(
                f"[WRC HEARTBEAT] packets={self.total_packets}, "
                f"stage_time={stage_time:.2f}s, progress={progress:.2f}"
            )

        in_stage = stage_time > _STAGE_TIME_EPS

        # ── Stage start ──────────────────────────────────────────────────────
        if in_stage and not self._wrc_in_stage:
            self.log(f"[WRC] Stage started (stage_time={stage_time:.2f}s)")
            self._wrc_in_stage     = True
            self._wrc_last_third   = 0
            self._wrc_finish_fired = False
            if self.is_event_enabled("lights_out"):
                self._clear_bridge_effect()
                self._fire("lights_out")
            return

        if in_stage:
            # ── Stage finish ──────────────────────────────────────────────────
            # stage_result_status flips 0 -> 1 the moment the stage is completed.
            if status == _STAGE_STATUS_FINISHED and not self._wrc_finish_fired:
                self.log("[WRC] Stage finished")
                self._wrc_finish_fired = True
                if self.is_event_enabled("chequered_flag"):
                    self._clear_bridge_effect()
                    self._fire("chequered_flag")

            # ── Split checkpoints ─────────────────────────────────────────────
            # stage_progress runs 0..1; fire as it crosses each third (like DR2 splits).
            elif not self._wrc_finish_fired:
                third = min(2, max(0, int(progress * 3)))
                if third > self._wrc_last_third:
                    self._wrc_last_third = third
                    self.log(f"[WRC] Split {third} crossed (progress={progress:.2f})")
                    if self.is_event_enabled("fastest_lap"):
                        self._clear_bridge_effect()
                        self._fire("fastest_lap")
            return

        # ── Return to service park / menus ───────────────────────────────────
        if not in_stage and self._wrc_in_stage:
            self.log("[WRC] Returned to service park / menus")
            self._wrc_in_stage = False
            if self.is_event_enabled("neutral"):
                self.neutral_bridge()
