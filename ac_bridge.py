"""Assetto Corsa shared-memory listener (#49).

The first bridge that isn't a UDP listener. AC broadcasts nothing — it writes
three memory-mapped files whenever it's running, so instead of binding a socket
and waiting for packets, this attaches to those maps and polls them.

Why shared memory and not AC's UDP port 9996: the UDP interface carries car
physics only — no flags. The `flag` field is what almost every GridGlow effect
keys off, and it lives in shared memory, which also carries everything UDP has.
The one thing UDP would buy is running GridGlow on a different PC to the game;
shared memory is same-machine only, making AC the first title with that
constraint. See #49 for the fallback plan if anyone ever needs it.

The structs are declared with ctypes rather than hand-computed offsets on
purpose. The graphics layout has wchar_t[15] and wchar_t[33] arrays in it, so
the C compiler inserts padding that naive offset arithmetic misses — `flag`
lands at 268, not the 266 you'd get by adding field sizes up. Letting ctypes
mirror the SDK's struct removes that whole class of bug, which is exactly the
kind that bit the Forza packet layout (#52).

Field layouts follow the AC SDK's SharedMemoryDocumentation. They're
documentation-derived: nobody here owns the game. `[AC] layout` logs what the
maps actually contain so a user with AC can confirm them in one paste.
"""
import ctypes
import math
import time

from bridge_core import F1LifxBridgeCore
from shared_memory import SharedMemoryMap

_PHYSICS_TAG  = "Local\\acpmf_physics"
_GRAPHICS_TAG = "Local\\acpmf_graphics"
_STATIC_TAG   = "Local\\acpmf_static"

# Lap times are milliseconds. Before you set one, AC parks iBestTime on a
# sentinel — reported variously as 0 or a huge int depending on version — so
# rather than guess which, treat anything outside a plausible lap as "no time".
_LAP_TIME_MAX_MS = 60 * 60 * 1000     # an hour; no circuit lap comes close

# Crash detection, mirroring the DiRT Rally thresholds — same idea, and AC's
# accG is in the same units. AC reports speed in km/h where DR2 uses m/s, so
# the drop is converted rather than reused blind.
_CRASH_G_THRESHOLD     = 3.5          # combined lateral + longitudinal G
_CRASH_SPEED_DROP_KMH  = 14.0         # ~4 m/s lost between samples
_CRASH_COOLDOWN_S      = 3.0

# How often to sample. The game writes far faster; the RPM meter only repaints
# on a quantised level change, so 60 Hz is plenty and costs nothing to poll.
_POLL_S = 1.0 / 60
# How often to retry attaching while AC isn't running. A second is responsive
# enough to feel instant when the game starts, without spinning.
_RETRY_S = 1.0


# ── AC_STATUS ────────────────────────────────────────────────────────────────
AC_OFF, AC_REPLAY, AC_LIVE, AC_PAUSE = 0, 1, 2, 3

# ── AC_SESSION_TYPE ──────────────────────────────────────────────────────────
AC_RACE = 2

# ── AC_FLAG_TYPE ─────────────────────────────────────────────────────────────
AC_NO_FLAG        = 0
AC_BLUE_FLAG      = 1
AC_YELLOW_FLAG    = 2
AC_BLACK_FLAG     = 3
AC_WHITE_FLAG     = 4
AC_CHECKERED_FLAG = 5
AC_PENALTY_FLAG   = 6

# Which GridGlow effect each flag drives. AC has a real flag enum, which is why
# this maps so much more directly than Forza or WRC ever could.
_FLAG_EFFECT = {
    AC_YELLOW_FLAG:    "yellow_flag",
    AC_BLUE_FLAG:      "blue_flag",
    AC_WHITE_FLAG:     "white_warning",
    AC_BLACK_FLAG:     "black_flag",
    AC_CHECKERED_FLAG: "chequered_flag",
    AC_PENALTY_FLAG:   "white_warning",
    AC_NO_FLAG:        "neutral",
}


class ACPhysics(ctypes.Structure):
    """SPageFilePhysics — only as deep as GridGlow reads.

    Declared in the SDK's order so ctypes lays it out the way the game wrote it.
    """
    _fields_ = [
        ("packetId",   ctypes.c_int),
        ("gas",        ctypes.c_float),
        ("brake",      ctypes.c_float),
        ("fuel",       ctypes.c_float),
        ("gear",       ctypes.c_int),
        ("rpms",       ctypes.c_int),      # int, not float — unlike Forza
        ("steerAngle", ctypes.c_float),
        ("speedKmh",   ctypes.c_float),
        ("velocity",   ctypes.c_float * 3),
        ("accG",       ctypes.c_float * 3),
    ]


class ACGraphics(ctypes.Structure):
    """SPageFileGraphic, up to and including the fields GridGlow reads.

    Every field down to `flag` has to be declared even though most are unused:
    they're what put `flag` at the right offset. The wchar_t arrays are why
    hand-computing that offset goes wrong.
    """
    _fields_ = [
        ("packetId",             ctypes.c_int),
        ("status",               ctypes.c_int),
        ("session",              ctypes.c_int),
        ("currentTime",          ctypes.c_wchar * 15),
        ("lastTime",             ctypes.c_wchar * 15),
        ("bestTime",             ctypes.c_wchar * 15),
        ("split",                ctypes.c_wchar * 15),
        ("completedLaps",        ctypes.c_int),
        ("position",             ctypes.c_int),
        ("iCurrentTime",         ctypes.c_int),
        ("iLastTime",            ctypes.c_int),
        ("iBestTime",            ctypes.c_int),
        ("sessionTimeLeft",      ctypes.c_float),
        ("distanceTraveled",     ctypes.c_float),
        ("isInPit",              ctypes.c_int),
        ("currentSectorIndex",   ctypes.c_int),
        ("lastSectorTime",       ctypes.c_int),
        ("numberOfLaps",         ctypes.c_int),
        ("tyreCompound",         ctypes.c_wchar * 33),
        ("replayTimeMultiplier", ctypes.c_float),
        ("normalizedCarPosition", ctypes.c_float),
        ("carCoordinates",       ctypes.c_float * 3),
        ("penaltyTime",          ctypes.c_float),
        ("flag",                 ctypes.c_int),
        ("idealLineOn",          ctypes.c_int),
        ("isInPitLane",          ctypes.c_int),
        ("surfaceGrip",          ctypes.c_float),
        ("mandatoryPitDone",     ctypes.c_int),
    ]


class ACStatic(ctypes.Structure):
    """SPageFileStatic, down to maxRpm — the RPM meter's ceiling."""
    _fields_ = [
        ("smVersion",        ctypes.c_wchar * 15),
        ("acVersion",        ctypes.c_wchar * 15),
        ("numberOfSessions", ctypes.c_int),
        ("numCars",          ctypes.c_int),
        ("carModel",         ctypes.c_wchar * 33),
        ("track",            ctypes.c_wchar * 33),
        ("playerName",       ctypes.c_wchar * 33),
        ("playerSurname",    ctypes.c_wchar * 33),
        ("playerNick",       ctypes.c_wchar * 33),
        ("sectorCount",      ctypes.c_int),
        ("maxTorque",        ctypes.c_float),
        ("maxPower",         ctypes.c_float),
        ("maxRpm",           ctypes.c_int),
        ("maxFuel",          ctypes.c_float),
    ]


def _parse(struct_type, data):
    """Bytes -> struct, or None if the map was shorter than the struct."""
    if data is None or len(data) < ctypes.sizeof(struct_type):
        return None
    return struct_type.from_buffer_copy(data[:ctypes.sizeof(struct_type)])


class ACBridgeCore(F1LifxBridgeCore):
    """Shared-memory listener for Assetto Corsa.

    Inherits controller management and the bridge loop from F1LifxBridgeCore;
    only listener_loop and the dispatch are replaced. stop() needs no changes —
    it guards its socket close, so clearing `running` is enough to end the poll.

    Effects mapping
    ---------------
    Yellow / blue / white / black / chequered flag -> the matching effect
    Penalty (flag or penaltyTime)                  -> white_warning
    Race start (session RACE, first lap)           -> lights_out
    Personal best (iBestTime improves)             -> fastest_lap
    Hard impact (G spike + speed lost)             -> crash
    Flag cleared                                   -> neutral
    Engine revs vs maxRpm                          -> rpm_meter / redline

    No red flag and no start-light sequence: AC's flag enum has neither, so
    those two effects have no source here.

    Nothing fires unless status is AC_LIVE: the maps stay populated in menus,
    replays and pause, so without that gate a replay would drive the lights.

    AC exposes no start-light sequence, so start_lights has no source here;
    lights_out fires off the race/lap transition instead.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._physics  = SharedMemoryMap(_PHYSICS_TAG,  ctypes.sizeof(ACPhysics))
        self._graphics = SharedMemoryMap(_GRAPHICS_TAG, ctypes.sizeof(ACGraphics))
        self._static   = SharedMemoryMap(_STATIC_TAG,   ctypes.sizeof(ACStatic))
        self._ac_attached   = False
        self._ac_max_rpm    = 0
        self._ac_last_flag  = None
        self._ac_last_status = None
        self._ac_best_time  = None
        self._ac_last_speed = None
        self._ac_race_started = False
        self._ac_crash_cooldown = 0.0
        self._ac_logged_layout = False
        self._ac_primed = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ac_attach(self) -> bool:
        """Attach to all three maps, or none. Partial attachment is useless."""
        if self._physics.open() and self._graphics.open() and self._static.open():
            return True
        self._ac_detach()
        return False

    def _ac_detach(self):
        for m in (self._physics, self._graphics, self._static):
            m.close()
        self._ac_attached = False
        # Forget everything: on the next attach the game may be in a completely
        # different session, and stale state would fire effects for things that
        # happened while we weren't watching.
        self._ac_last_flag = self._ac_last_status = None
        self._ac_best_time = self._ac_last_speed = None
        self._ac_race_started = False
        self._ac_logged_layout = False
        self._ac_primed = False

    def listener_loop(self):
        self.log("===================================================")
        self.log("GridGlow — Assetto Corsa")
        self.log("===================================================")
        self.log("Reading shared memory (no in-game setup needed).")
        self.log("GridGlow must run on the same PC as the game.")
        self.log("===================================================")
        self.log("Waiting for Assetto Corsa...")

        while self.running:
            if not self._ac_attached:
                if not self._ac_attach():
                    time.sleep(_RETRY_S)      # game isn't up; keep waiting
                    continue
                self._ac_attached = True
                self.log("[AC] Attached to Assetto Corsa.")

            physics  = _parse(ACPhysics,  self._physics.read())
            graphics = _parse(ACGraphics, self._graphics.read())
            if physics is None or graphics is None:
                self.log("[AC] Lost Assetto Corsa — waiting for it to come back.")
                self._ac_detach()
                continue

            self._handle_ac(physics, graphics)
            time.sleep(_POLL_S)

        self._ac_detach()
        self.log("[AC] Listener loop ended.")

    # ── dispatch ─────────────────────────────────────────────────────────────

    def _handle_ac(self, physics, graphics):
        self.total_packets += 1

        # Documentation-derived offsets: log once what the maps actually hold so
        # a user with the game can confirm the layout in a single paste (#49).
        if not self._ac_logged_layout:
            self._ac_logged_layout = True
            static = _parse(ACStatic, self._static.read())
            self._ac_max_rpm = static.maxRpm if static else 0
            self.log(f"[AC] layout: status={graphics.status} session={graphics.session} "
                     f"flag={graphics.flag} rpms={physics.rpms} maxRpm={self._ac_max_rpm} "
                     f"speed={physics.speedKmh:.0f}km/h "
                     f"car={static.carModel if static else '?'} "
                     f"track={static.track if static else '?'}")

        # The maps stay live in menus, replays and pause. Without this gate a
        # replay would drive the lights.
        if graphics.status != AC_LIVE:
            if self._ac_last_status == AC_LIVE:
                self.log("[AC] Session left — returning to idle.")
                if self.is_event_enabled("neutral"):
                    self.neutral_bridge()
            self._ac_last_status = graphics.status
            return
        self._ac_last_status = AC_LIVE

        # The maps hold whatever the game was already doing before we attached:
        # a lap time from an earlier session, a chequered flag from a race that
        # finished before GridGlow even started. The first live sample learns
        # that state instead of announcing it (#49).
        if not self._ac_primed:
            self._ac_primed = True
            self._ac_seed(physics, graphics)
            return

        if self.total_packets % 600 == 0:
            self.log(f"[AC HEARTBEAT] samples={self.total_packets}, "
                     f"flag={graphics.flag}, lap={graphics.completedLaps}, "
                     f"rpm={physics.rpms}")

        self._ac_race_start(graphics)
        self._ac_fastest_lap(graphics)
        self._ac_flags(graphics)
        self._ac_crash(physics, graphics)
        self._ac_rpm(physics)

    def _ac_race_start(self, graphics):
        """Lights out = the lap timer starting on lap one of a race.

        This used to fire on the first *completed* lap, which is the end of lap
        one — a whole lap late, and it read as firing every time you crossed
        the line. AC exposes no start-light sequence, but iCurrentTime sits at
        0 through the grid countdown and starts the moment you're released,
        which is the same signal WRC's stage start uses.
        """
        on_lap_one = graphics.session == AC_RACE and graphics.completedLaps == 0
        if on_lap_one and graphics.iCurrentTime > 0:
            if not self._ac_race_started:
                self._ac_race_started = True
                self.log("[AC] Race start")
                if self.is_event_enabled("lights_out"):
                    self._clear_bridge_effect()
                    self._fire("lights_out")
        elif on_lap_one:
            # Back on the grid with the timer at zero: arm for the next start.
            self._ac_race_started = False

    def _ac_seed(self, physics, graphics):
        """Adopt the game's current state without firing anything for it.

        Everything here is an edge detector, and an edge against `None` reads
        every pre-existing value as though it just happened.
        """
        self._ac_last_flag = graphics.flag
        self._ac_last_speed = physics.speedKmh
        best = graphics.iBestTime
        self._ac_best_time = best if 0 < best < _LAP_TIME_MAX_MS else None
        # If the timer's already running we joined a race in progress; don't
        # announce a start that happened before we were watching.
        self._ac_race_started = (graphics.session == AC_RACE
                                 and graphics.iCurrentTime > 0)
        self.log(f"[AC] Joined: flag={graphics.flag} lap={graphics.completedLaps}"
                 + (f" best={best / 1000:.3f}s" if self._ac_best_time else ""))

    def _ac_fastest_lap(self, graphics):
        """iBestTime improving is a personal best — AC's equivalent of F1's FTLP.

        AC is single-player at heart, so your best lap *is* the session's best,
        which makes this the same event F1 fires on. The first valid lap counts:
        it's your best by definition, and the sentinel it replaces isn't a time.
        """
        best = graphics.iBestTime
        if not (0 < best < _LAP_TIME_MAX_MS):
            return                                   # no lap set yet
        if best == self._ac_best_time:
            return
        improved = self._ac_best_time is None or best < self._ac_best_time
        self._ac_best_time = best
        if not improved:
            return                                   # session reset, not a PB
        self.log(f"[AC] Personal best - {best / 1000:.3f}s")
        if self.is_event_enabled("fastest_lap"):
            self._clear_bridge_effect()
            self._fire("fastest_lap")

    def _ac_crash(self, physics, graphics):
        """Hard impact: a G-force spike *and* speed actually lost.

        Same two-signal test DiRT Rally uses — G alone fires on kerbs and hard
        cornering, so requiring the car to genuinely lose speed is what keeps a
        fast lap from strobing.
        """
        speed = physics.speedKmh
        last = self._ac_last_speed
        self._ac_last_speed = speed
        if last is None:
            return
        # Once the chequered flag is out the session is over and AC resets the
        # car, which reads as a huge G spike. That isn't a crash — it's the
        # game tidying up, and flashing for it after the race is just noise.
        if graphics.flag == AC_CHECKERED_FLAG:
            return
        # accG is (lateral, vertical, longitudinal); vertical is kerbs and
        # bumps, which is exactly what shouldn't count as a crash.
        g = math.sqrt(physics.accG[0] ** 2 + physics.accG[2] ** 2)
        now = time.time()
        if (g > _CRASH_G_THRESHOLD
                and speed < last - _CRASH_SPEED_DROP_KMH
                and now - self._ac_crash_cooldown > _CRASH_COOLDOWN_S):
            self.log(f"[AC] Crash - G={g:.1f}, dV={last - speed:.1f} km/h")
            self._ac_crash_cooldown = now
            if self.is_event_enabled("crash"):
                self._clear_bridge_effect()
                self._fire("crash")

    def _ac_flags(self, graphics):
        """Fire on the edge only — the flag field holds its value every frame."""
        flag = graphics.flag
        if flag == self._ac_last_flag:
            return
        self._ac_last_flag = flag

        effect = _FLAG_EFFECT.get(flag)
        if effect is None:
            return
        self.log(f"[AC] Flag -> {effect}")
        if not self.is_event_enabled(effect):
            return
        if effect == "neutral":
            self.neutral_bridge()
        else:
            self._clear_bridge_effect()
            self._fire(effect)

    def _ac_rpm(self, physics):
        """AC gives raw revs and a per-car ceiling rather than F1's rev-lights
        percent, so derive the percent and hand it to the shared dispatcher —
        which owns the throttle that keeps 60 Hz off the LAN."""
        if self._ac_max_rpm <= 0 or not self._rpm_meter_active():
            return
        # A red flag owns the whole strip via the override; let it finish.
        if self.lifx is not None and self.lifx.sector_strip_override:
            return
        pct = max(0, min(100, round(physics.rpms / self._ac_max_rpm * 100)))
        self.dispatch_rpm_percent(pct, physics.rpms)
