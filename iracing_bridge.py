"""iRacing shared-memory listener (#69).

Unlike every other bridge, iRacing broadcasts no UDP. It exposes live telemetry
through a Windows shared-memory map (``Local\\IRSDKMemMapFileName``) whose layout
is self-describing: a header points at a table of *variable headers*, each naming
a channel with its type and byte offset. Channels are therefore looked up **by
name at runtime** rather than by a fixed struct offset the way the AC / Forza /
F1 bridges hard-code them.

Two things make iRacing different from the AC family (the other shared-memory
titles), and both are where the bugs live:

  * **The var-header table.** There's no fixed ctypes struct to mirror — we parse
    the table once per connect and read channels by name from whichever telemetry
    buffer is newest.
  * **``SessionFlags`` is a bitfield.** Several flags are set at once (e.g.
    ``Yellow | Caution``), so dispatch picks the highest-priority bit rather than
    switching on a single value like the AC flag enum.

No in-game setup and no config installer: if iRacing is running the map is there,
so — unlike F1/DiRT/Forza/WRC — there's nothing for the user to switch on.
Windows-only and same-machine only (shared memory), exactly like AC/ACC.

Layouts follow the iRacing SDK (``irsdk_defines.h``). Nobody here owns a
subscription, so this ships help-wanted for live validation (#69): the
``[IR] layout`` line logs what the map actually contains so a subscriber can
confirm the channels resolved in one paste.
"""
import struct
import time

from bridge_core import F1LifxBridgeCore
from shared_memory import SharedMemoryMap

_MEMMAP_TAG = "Local\\IRSDKMemMapFileName"
# The SDK allocates a fixed-size map; this matches the reference reader
# (pyirsdk). If the real map were ever smaller the view simply fails to attach
# and we report "not running" — the safe direction.
_MEMMAP_SIZE = 1164 * 1024

_STATUS_CONNECTED = 1                 # header.status & 1 → the sim is live

# irsdk_VarType → (struct code, byte size). Only the scalar leaders are read;
# array channels (count > 1) are sampled at their first element.
_VAR_TYPE = {
    0: ("c", 1),   # irsdk_char
    1: ("?", 1),   # irsdk_bool
    2: ("i", 4),   # irsdk_int
    3: ("I", 4),   # irsdk_bitField
    4: ("f", 4),   # irsdk_float
    5: ("d", 8),   # irsdk_double
}

# irsdk_Flags — the SessionFlags bitfield (irsdk_defines.h).
F_CHECKERED        = 0x00000001
F_WHITE            = 0x00000002       # one lap to go — NOT a penalty (see below)
F_GREEN            = 0x00000004
F_YELLOW           = 0x00000008
F_RED              = 0x00000010
F_BLUE             = 0x00000020
F_DEBRIS           = 0x00000040
F_CROSSED          = 0x00000080
F_YELLOW_WAVING    = 0x00000100
F_ONE_LAP_TO_GREEN = 0x00000200
F_GREEN_HELD       = 0x00000400
F_TEN_TO_GO        = 0x00000800
F_FIVE_TO_GO       = 0x00001000
F_RANDOM_WAVING    = 0x00002000
F_CAUTION          = 0x00004000
F_CAUTION_WAVING   = 0x00008000
F_BLACK            = 0x00010000
F_DISQUALIFY       = 0x00020000
F_SERVICEABLE      = 0x00040000
F_FURLED           = 0x00080000
F_REPAIR           = 0x00100000
F_START_HIDDEN     = 0x10000000
F_START_READY      = 0x20000000
F_START_SET        = 0x40000000
F_START_GO         = 0x80000000

_YELLOW_ANY = F_YELLOW | F_YELLOW_WAVING | F_CAUTION | F_CAUTION_WAVING
_BLACK_ANY  = F_BLACK | F_DISQUALIFY
# Furled/repair are the "serviceable warning" (meatball / furled black) — a real
# penalty warning, which is what white_warning is for. iRacing's actual White
# flag (0x2) means "one lap to go", so it is deliberately NOT mapped here: same
# name, opposite meaning (see #69's open question).
_WHITE_WARN = F_FURLED | F_REPAIR

# iRacing parks an unset best lap on a negative sentinel; anything outside a
# plausible lap is "no time yet".
_LAP_TIME_MAX_S = 60 * 60             # an hour; no circuit lap comes close

# Channel names GridGlow reads out of the var table. Exact SDK telemetry names.
_CHANNELS = ("SessionFlags", "SessionState", "ShiftIndicatorPct", "RPM",
             "Gear", "Speed", "IsOnTrack", "IsInGarage", "LapBestLapTime")

# The game writes at 60 Hz; polling faster buys nothing and the RPM meter only
# repaints on a quantised change. Retry attaching once a second while it's down.
_POLL_S  = 1.0 / 60
_RETRY_S = 1.0


class IRSDKReader:
    """Parses the iRacing header + var-header table and reads named channels
    from the newest telemetry buffer.

    Pure parsing over a ``bytes`` snapshot — no Windows calls — so the layout
    logic is testable off a synthetic map without a subscription.
    """
    _HEADER = struct.Struct("<12i")               # ver..bufLen + 2 pad = 48 B
    _VARBUF = struct.Struct("<4i")                # tickCount, bufOffset, 2 pad
    _VARHDR = struct.Struct("<3i i 32s 64s 32s")  # type, offset, count, countAsTime, name, desc, unit
    _VARHDR_SIZE = 144

    def __init__(self):
        self._vars = {}                # name -> (type, offset, count)
        self._parsed_for = None        # (numVars, varHeaderOffset) the table was built for

    def connected(self, data) -> bool:
        """True once the sim is running and the map holds valid data."""
        if data is None or len(data) < self._HEADER.size:
            return False
        return bool(self._HEADER.unpack_from(data, 0)[1] & _STATUS_CONNECTED)

    def _build_table(self, data, num_vars, var_header_offset):
        """(Re)build the name → (type, offset, count) index. Cheap, and only run
        when the header says the table moved or grew (i.e. a new connect)."""
        table = {}
        for i in range(num_vars):
            base = var_header_offset + i * self._VARHDR_SIZE
            if base + self._VARHDR_SIZE > len(data):
                break
            vtype, offset, count, _cat, name, _desc, _unit = self._VARHDR.unpack_from(data, base)
            table[name.split(b"\x00", 1)[0].decode("latin-1")] = (vtype, offset, count)
        self._vars = table
        self._parsed_for = (num_vars, var_header_offset)

    def read(self, data):
        """Return ``{name: value}`` for the channels we care about, or ``None``
        if this isn't a connected iRacing session yet."""
        if not self.connected(data):
            return None
        hdr = self._HEADER.unpack_from(data, 0)
        num_vars, var_header_offset, num_buf = hdr[6], hdr[7], hdr[8]

        # Pick the freshest of the (double/triple-buffered) telemetry buffers by
        # tick count — reading the newest avoids a half-written frame.
        best_tick, buf_off = -1, 0
        base = self._HEADER.size
        for i in range(min(num_buf, 4)):
            tick, off = self._VARBUF.unpack_from(data, base + i * self._VARBUF.size)[:2]
            if tick > best_tick:
                best_tick, buf_off = tick, off

        if self._parsed_for != (num_vars, var_header_offset) or not self._vars:
            self._build_table(data, num_vars, var_header_offset)

        out = {}
        for name in _CHANNELS:
            entry = self._vars.get(name)
            if entry is None:
                continue
            vtype, offset, _count = entry
            code_size = _VAR_TYPE.get(vtype)
            if code_size is None:
                continue
            code, size = code_size
            pos = buf_off + offset
            if pos + size > len(data):
                continue
            out[name] = struct.unpack_from("<" + code, data, pos)[0]
        return out


class IRacingBridgeCore(F1LifxBridgeCore):
    """Shared-memory listener for iRacing.

    Inherits controller management and the bridge loop from F1LifxBridgeCore;
    only listener_loop and the dispatch are replaced, and stop() is unchanged —
    it guards its socket close, so clearing `running` ends the poll.

    Effects mapping
    ---------------
    Start sequence (StartReady → StartSet → StartGo)  -> start_lights build → lights_out
    Red flag                                          -> red_flag
    Black / Disqualify                                -> black_flag
    Yellow / YellowWaving / Caution / CautionWaving   -> yellow_flag
    Blue                                              -> blue_flag
    Furled / Repair (serviceable warning)             -> white_warning
    Chequered                                         -> chequered_flag
    No relevant flag (incl. plain Green)              -> neutral
    ShiftIndicatorPct                                 -> rpm_meter / redline
    LapBestLapTime improves                           -> fastest_lap

    Nothing fires unless the driver is on track (IsOnTrack and not IsInGarage):
    the map stays populated in the garage, menus and replays, so without that
    gate a replay would drive the lights — the same trap the AC bridge guards
    with its LIVE-status check.
    """

    _TAG = "IR"                       # log prefix: [IR] ...
    _GAME_NAME = "iRacing"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._map = SharedMemoryMap(_MEMMAP_TAG, _MEMMAP_SIZE)
        self._reader = IRSDKReader()
        self._ir_attached = False
        self._ir_on_track = False
        self._ir_primed = False
        self._ir_last_effect = None    # last flag-derived effect (edge detect)
        self._ir_start_phase = None    # None / 'ready' / 'set' / 'go'
        self._ir_best_time = None
        self._ir_logged_layout = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ir_detach(self):
        self._map.close()
        self._ir_attached = False
        # Forget everything: the next attach could be a different session, and
        # stale edges would fire effects for things that happened while we
        # weren't watching.
        self._ir_on_track = False
        self._ir_primed = False
        self._ir_last_effect = None
        self._ir_start_phase = None
        self._ir_best_time = None
        self._ir_logged_layout = False

    def listener_loop(self):
        self.log("===================================================")
        self.log(f"GridGlow — {self._GAME_NAME}")
        self.log("===================================================")
        self.log("Reading shared memory (no in-game setup needed).")
        self.log("GridGlow must run on the same PC as iRacing.")
        self.log("===================================================")
        self.log(f"Waiting for {self._GAME_NAME}...")

        while self.running:
            if not self._ir_attached:
                if not self._map.open():
                    time.sleep(_RETRY_S)          # sim isn't up; keep waiting
                    continue
                self._ir_attached = True
                self.log(f"[{self._TAG}] Attached to {self._GAME_NAME}.")

            data = self._map.read()
            if data is None:
                # The map vanished — iRacing exited and unmapped its side.
                self.log(f"[{self._TAG}] Lost {self._GAME_NAME} — waiting for it to come back.")
                self._ir_detach()
                continue

            tel = self._reader.read(data)
            if tel is None:
                # Attached, but the sim hasn't marked the data valid yet
                # (launching, at the menu). Wait without spinning.
                time.sleep(_RETRY_S)
                continue

            self._handle_ir(tel)
            time.sleep(_POLL_S)

        self._ir_detach()
        self.log(f"[{self._TAG}] Listener loop ended.")

    # ── dispatch ─────────────────────────────────────────────────────────────

    def _handle_ir(self, tel):
        self.total_packets += 1

        # The map stays live in the garage, menus and replays. Only drive the
        # lights when the player is actually on track.
        on_track = bool(tel.get("IsOnTrack")) and not bool(tel.get("IsInGarage"))
        if not on_track:
            if self._ir_on_track:
                self.log(f"[{self._TAG}] Left track — returning to idle.")
                if self.is_event_enabled("neutral"):
                    self.neutral_bridge()
                # Re-prime on the next on-track sample: a fresh session can start
                # with flags already set (formation caution), and that has to be
                # adopted silently rather than announced.
                self._ir_primed = False
                self._ir_last_effect = None
                self._ir_start_phase = None
            self._ir_on_track = False
            return
        self._ir_on_track = True

        if not self._ir_logged_layout:
            self._ir_logged_layout = True
            self._ir_log_layout(tel)

        # The map holds whatever the game was already doing before we attached —
        # a caution from before GridGlow started, a best lap from an earlier
        # session. The first on-track sample learns that state instead of firing.
        if not self._ir_primed:
            self._ir_primed = True
            self._ir_seed(tel)
            return

        if self.total_packets % 600 == 0:
            self.log(f"[{self._TAG} HEARTBEAT] samples={self.total_packets}, "
                     f"flags=0x{int(tel.get('SessionFlags', 0)) & 0xFFFFFFFF:08X}, "
                     f"rpm={tel.get('RPM', 0):.0f}")

        flags = int(tel.get("SessionFlags", 0)) & 0xFFFFFFFF
        # The start sequence owns the strip while it's running; only once it's
        # done does the ordinary flag dispatch take back over.
        if not self._ir_start(flags):
            self._ir_flags(flags)
        self._ir_fastest_lap(tel)
        self._ir_rpm(tel)

    # ── start sequence ───────────────────────────────────────────────────────

    @staticmethod
    def _start_phase(flags):
        if flags & F_START_GO:
            return "go"
        if flags & F_START_SET:
            return "set"
        if flags & F_START_READY:
            return "ready"
        return None

    def _ir_start(self, flags):
        """Drive the start-light build-up. iRacing gives three discrete states
        (Ready → Set → Go) rather than F1's 1–5 count, so the build is
        synthesised: Ready lights part of the strip, Set fills it, Go is lights
        out. Returns True while a start state is active so the caller skips the
        ordinary flag dispatch."""
        phase = self._start_phase(flags)
        if phase == self._ir_start_phase:
            return phase is not None
        prev = self._ir_start_phase
        self._ir_start_phase = phase

        if phase == "ready":
            self._ir_fire_start(3)
        elif phase == "set":
            self._ir_fire_start(5)
        elif phase == "go":
            self.log(f"[{self._TAG}] Start — GO")
            if self.is_event_enabled("lights_out"):
                self._clear_bridge_effect()
                self._fire("lights_out")
        elif phase is None and prev in ("ready", "set", "go"):
            # Start finished. Pretend the last flag effect was already neutral so
            # the flag dispatch that runs next frame (Green → neutral) doesn't
            # stomp the lights_out flash with a redundant clear.
            self._ir_last_effect = "neutral"
        return phase is not None

    def _ir_fire_start(self, n):
        self.log(f"[{self._TAG}] Start lights ({n})")
        if self.is_event_enabled("start_lights"):
            self._clear_bridge_effect()
            self._fire("start_lights", n)

    # ── flags ────────────────────────────────────────────────────────────────

    @staticmethod
    def _effect_from_flags(flags):
        """Collapse the bitfield to a single effect by priority — the most
        urgent set bit wins. Plain Green (and any flag we don't map) is neutral."""
        if flags & F_RED:
            return "red_flag"
        if flags & _BLACK_ANY:
            return "black_flag"
        if flags & _YELLOW_ANY:
            return "yellow_flag"
        if flags & F_BLUE:
            return "blue_flag"
        if flags & _WHITE_WARN:
            return "white_warning"
        if flags & F_CHECKERED:
            return "chequered_flag"
        return "neutral"

    def _ir_flags(self, flags):
        """Fire on the edge only — the bitfield holds its value every frame."""
        effect = self._effect_from_flags(flags)
        if effect == self._ir_last_effect:
            return
        self._ir_last_effect = effect
        self.log(f"[{self._TAG}] Flag -> {effect}")
        if not self.is_event_enabled(effect):
            return
        if effect == "neutral":
            self.neutral_bridge()
        else:
            self._clear_bridge_effect()
            self._fire(effect)

    # ── other events ─────────────────────────────────────────────────────────

    def _ir_fastest_lap(self, tel):
        """LapBestLapTime improving is a personal best — iRacing's analogue of
        F1's FTLP, and the same edge the AC bridge fires on."""
        best = tel.get("LapBestLapTime")
        if best is None or not (0 < best < _LAP_TIME_MAX_S):
            return                                   # no valid lap yet
        if best == self._ir_best_time:
            return
        improved = self._ir_best_time is None or best < self._ir_best_time
        self._ir_best_time = best
        if not improved:
            return                                   # session reset, not a PB
        self.log(f"[{self._TAG}] Personal best - {best:.3f}s")
        if self.is_event_enabled("fastest_lap"):
            self._clear_bridge_effect()
            self._fire("fastest_lap")

    def _ir_rpm(self, tel):
        """ShiftIndicatorPct is 0.0–1.0 — a direct analogue of F1's rev-lights
        percent — so hand it straight to the shared dispatcher, which owns the
        throttle that keeps 60 Hz off the LAN."""
        shift = tel.get("ShiftIndicatorPct")
        if shift is None or not self._rpm_meter_active():
            return
        # A red flag owns the whole strip via the override; let it finish.
        if self.lifx is not None and self.lifx.sector_strip_override:
            return
        pct = max(0, min(100, round(shift * 100)))
        self.dispatch_rpm_percent(pct, tel.get("RPM", 0))

    # ── priming / logging ────────────────────────────────────────────────────

    def _ir_seed(self, tel):
        """Adopt the game's current state without firing anything for it — every
        handler here is an edge detector, and an edge against None reads every
        pre-existing value as though it just happened."""
        flags = int(tel.get("SessionFlags", 0)) & 0xFFFFFFFF
        self._ir_last_effect = self._effect_from_flags(flags)
        self._ir_start_phase = self._start_phase(flags)
        best = tel.get("LapBestLapTime")
        self._ir_best_time = best if (best is not None and 0 < best < _LAP_TIME_MAX_S) else None
        self.log(f"[{self._TAG}] Joined: flags=0x{flags:08X}"
                 + (f" best={self._ir_best_time:.3f}s" if self._ir_best_time else ""))

    def _ir_log_layout(self, tel):
        """One line a subscriber can paste to confirm the channels resolved —
        this bridge ships unvalidated, so any missing name shows up here (#69)."""
        missing = [c for c in _CHANNELS if c not in tel]
        self.log(f"[{self._TAG}] layout: "
                 f"flags=0x{int(tel.get('SessionFlags', 0)) & 0xFFFFFFFF:08X} "
                 f"state={tel.get('SessionState')} "
                 f"shiftPct={tel.get('ShiftIndicatorPct')} "
                 f"rpm={tel.get('RPM')} gear={tel.get('Gear')} "
                 f"speed={tel.get('Speed')} onTrack={tel.get('IsOnTrack')} "
                 f"inGarage={tel.get('IsInGarage')} "
                 f"missing={missing or 'none'}")
