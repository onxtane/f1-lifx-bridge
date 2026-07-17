"""Assetto Corsa Competizione shared-memory listener (#79).

ACC uses the same three memory-mapped files as AC and the same physics/static
layout, so this reuses the entire AC bridge — the reader, the poll loop, race
start, personal best, crash, the RPM meter, the status gate, the priming that
stops a stale session firing on attach. All of that is inherited unchanged.

Two things are ACC's own:

  1. A much larger graphics struct. Up to normalizedCarPosition (offset 252) it
     is byte-identical to AC, which is why the inherited dispatch — all of which
     reads only those early fields — works without change. Then ACC diverges
     hard: where AC has one carCoordinates[3], ACC has activeCars +
     carCoordinates[60][3] + carID[60], pushing `flag` out to ~1224 and the
     global flags out past ~1300.

  2. Flags. ACC doesn't rely on AC's single `flag` enum for race control; it
     exposes globalYellow / globalWhite / globalChequered / globalRed as their
     own fields, plus per-sector globalYellow1/2/3 — which map onto Sector
     Status, making ACC the second title (after F1) able to light a strip per
     sector. `flag` is still read, but only for the blue flag.

BIG CAVEAT: this struct is documentation-derived and **cannot be checked here**
— nobody has ACC, and there is no captured log to confirm against, unlike AC
(#49) which got a real run. The global flags sit ~90 fields deep behind a
720-byte car array, and one wrong field size before them throws every flag
offset off. `[ACC] layout` logs the values so a user with the game can confirm
them in a single paste; until someone does, treat the flag offsets as unproven.
Ships help wanted for that reason.
"""
import ctypes

from ac_bridge import (
    ACBridgeCore, AC_BLUE_FLAG, _parse, ACStatic,
)


class ACCGraphics(ctypes.Structure):
    """ACC's SPageFileGraphic, declared to the depth GridGlow reads.

    The head (through normalizedCarPosition) is identical to AC's ACGraphics;
    the inherited dispatch reads only that far. Everything past activeCars is
    ACC-only and exists to put the global flags at the right offset. Declared
    with ctypes, not hand-computed offsets, for the same reason AC is — the
    wchar arrays and the 60-car matrix make manual arithmetic a coin toss.
    """
    _fields_ = [
        # ── identical to AC through here (offset 0..251) ──────────────────────
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
        # ── ACC diverges here: the 60-car block AC doesn't have ───────────────
        ("activeCars",           ctypes.c_int),
        ("carCoordinates",       (ctypes.c_float * 3) * 60),
        ("carID",                ctypes.c_int * 60),
        ("playerCarID",          ctypes.c_int),
        ("penaltyTime",          ctypes.c_float),
        ("flag",                 ctypes.c_int),      # still the blue-flag source
        ("penalty",              ctypes.c_int),
        ("idealLineOn",          ctypes.c_int),
        ("isInPitLane",          ctypes.c_int),
        ("surfaceGrip",          ctypes.c_float),
        ("mandatoryPitDone",     ctypes.c_int),
        ("windSpeed",            ctypes.c_float),
        ("windDirection",        ctypes.c_float),
        ("isSetupMenuVisible",   ctypes.c_int),
        ("mainDisplayIndex",     ctypes.c_int),
        ("secondaryDisplayIndex", ctypes.c_int),
        ("TC",                   ctypes.c_int),
        ("TCCut",                ctypes.c_int),
        ("EngineMap",            ctypes.c_int),
        ("ABS",                  ctypes.c_int),
        ("fuelXLap",             ctypes.c_float),
        ("rainLights",           ctypes.c_int),
        ("flashingLights",       ctypes.c_int),
        ("lightsStage",          ctypes.c_int),
        ("exhaustTemperature",   ctypes.c_float),
        ("wiperLV",              ctypes.c_int),
        ("driverStintTotalTimeLeft", ctypes.c_int),
        ("driverStintTimeLeft",  ctypes.c_int),
        ("rainTyres",            ctypes.c_int),
        ("sessionIndex",         ctypes.c_int),
        ("usedFuel",             ctypes.c_float),
        ("deltaLapTime",         ctypes.c_wchar * 15),
        ("iDeltaLapTime",        ctypes.c_int),
        ("estimatedLapTime",     ctypes.c_wchar * 15),
        ("iEstimatedLapTime",    ctypes.c_int),
        ("isDeltaPositive",      ctypes.c_int),
        ("iSplit",               ctypes.c_int),
        ("isValidLap",           ctypes.c_int),
        ("fuelEstimatedLaps",    ctypes.c_float),
        ("trackStatus",          ctypes.c_wchar * 33),
        ("missingMandatoryPits", ctypes.c_int),
        ("clock",                ctypes.c_float),
        ("directionLightsLeft",  ctypes.c_int),
        ("directionLightsRight", ctypes.c_int),
        # ── the fields this bridge exists to read ─────────────────────────────
        ("globalYellow",         ctypes.c_int),
        ("globalYellow1",        ctypes.c_int),
        ("globalYellow2",        ctypes.c_int),
        ("globalYellow3",        ctypes.c_int),
        ("globalWhite",          ctypes.c_int),
        ("globalGreen",          ctypes.c_int),
        ("globalChequered",      ctypes.c_int),
        ("globalRed",            ctypes.c_int),
    ]


class ACCBridgeCore(ACBridgeCore):
    """Shared-memory listener for Assetto Corsa Competizione.

    Everything about lap/race/crash/RPM/status is inherited from ACBridgeCore
    unchanged — those read only the head of the struct, which ACC shares. Only
    the flag handling is ACC's own, because ACC exposes global flag states as
    dedicated fields rather than a single enum.

    Effects mapping
    ---------------
    globalYellow (+ per-sector globalYellow1/2/3) -> yellow_flag / sector_status
    globalRed                                     -> red_flag
    globalWhite                                   -> white_warning
    globalChequered                               -> chequered_flag
    flag == AC_BLUE_FLAG                           -> blue_flag
    penaltyTime > 0                               -> white_warning
    (race start, personal best, crash, RPM, status gate: inherited from AC)

    Unlike AC, ACC HAS a red flag (globalRed) — AC's enum has none.
    """

    _GRAPHICS_STRUCT = ACCGraphics
    _TAG = "ACC"
    _GAME_NAME = "Assetto Corsa Competizione"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # The last global-flag effect we fired, so we act on the change only —
        # the globals are set in every sample, not just when they flip.
        self._acc_last_global = None
        self._acc_last_blue = None

    # ── ACC-specific flag handling ───────────────────────────────────────────

    @staticmethod
    def _global_effect(graphics):
        """The single most important global flag, by priority, or None.

        Several can be set at once (a red under a chequered never happens, but
        yellow + white can), so this picks the one that should own the lights.
        Order matches how serious each is.
        """
        if graphics.globalRed:
            return "red_flag"
        if graphics.globalChequered:
            return "chequered_flag"
        if graphics.globalWhite:
            return "white_warning"
        if graphics.globalYellow or graphics.globalYellow1 \
                or graphics.globalYellow2 or graphics.globalYellow3:
            return "yellow_flag"
        return None

    def _layout_flags(self, graphics):
        return (f"flag={graphics.flag} gY={graphics.globalYellow}"
                f"[{graphics.globalYellow1}{graphics.globalYellow2}{graphics.globalYellow3}]"
                f" gW={graphics.globalWhite} gR={graphics.globalRed}"
                f" gChq={graphics.globalChequered}")

    def _seed_flags(self, graphics):
        # Adopt whatever's flying now without firing for it (join-in-progress).
        self._acc_last_global = self._global_effect(graphics)
        self._acc_last_blue = (graphics.flag == AC_BLUE_FLAG)

    def _session_finished(self, graphics) -> bool:
        # Disarms the crash flash at race end — ACC's chequered is its own field.
        return bool(graphics.globalChequered)

    def _ac_flags(self, graphics):
        """Replaces AC's single-enum handler with ACC's global flags.

        Global flags drive the race-control effects; the blue flag still comes
        from the per-car `flag` enum, since being lapped is about your car, not
        the session.
        """
        effect = self._global_effect(graphics)
        if effect != self._acc_last_global:
            self._acc_last_global = effect
            # Falling back to no global flag returns the strip to idle.
            target = effect or "neutral"
            if self.is_event_enabled(target):
                self.log(f"[{self._TAG}] Flag -> {target}")
                if target == "neutral":
                    self.neutral_bridge()
                else:
                    self._clear_bridge_effect()
                    self._fire(target)

        blue = (graphics.flag == AC_BLUE_FLAG)
        if blue and not self._acc_last_blue:
            if self.is_event_enabled("blue_flag"):
                self.log(f"[{self._TAG}] Flag -> blue_flag")
                self._clear_bridge_effect()
                self._fire("blue_flag")
        self._acc_last_blue = blue
