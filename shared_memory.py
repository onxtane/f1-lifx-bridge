"""Read-only access to a Windows named shared-memory map (#49).

Every other GridGlow bridge binds a socket and waits for the game to send. The
Assetto Corsa family doesn't broadcast — it writes memory-mapped files that are
simply there whenever the game is. This is the seam for those titles, and for
iRacing (#69) later, which works the same way.

Why ctypes rather than the stdlib's mmap: `mmap.mmap(-1, size, tagname=...)`
maps to CreateFileMapping, which **creates the map if it doesn't exist**. Used
naively that means GridGlow would invent `acpmf_physics` itself before the game
ever ran, then sit reading its own zeroes — unable to tell "game not running"
from "game running, everything at rest", and possibly holding a wrong-sized map
that the game then collides with. OpenFileMappingW opens existing-only and
fails cleanly when the game isn't there, which is exactly the signal we want.

Windows-only by nature. Import is safe anywhere; open() just reports the map is
absent on other platforms.
"""
import sys

_FILE_MAP_READ = 0x0004
_ERROR_FILE_NOT_FOUND = 2

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenFileMappingW.restype = wintypes.HANDLE
    _k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_size_t]
    _k32.MapViewOfFile.restype = wintypes.LPVOID
    _k32.UnmapViewOfFile.argtypes = [wintypes.LPCVOID]
    _k32.UnmapViewOfFile.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL


class SharedMemoryMap:
    """One named map, opened only if the writing process already made it.

    Not thread-safe; a bridge's listener loop owns its maps.
    """

    def __init__(self, tag: str, size: int):
        self.tag = tag
        self.size = size
        self._handle = None
        self._view = None

    @property
    def attached(self) -> bool:
        return self._view is not None

    def open(self) -> bool:
        """Attach to the map. False if it isn't there — i.e. the game isn't up.

        Safe to call repeatedly; a poll loop uses it as its retry.
        """
        if self._view is not None:
            return True
        if not _IS_WINDOWS:
            return False

        handle = _k32.OpenFileMappingW(_FILE_MAP_READ, False, self.tag)
        if not handle:
            return False
        view = _k32.MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, self.size)
        if not view:
            _k32.CloseHandle(handle)
            return False

        self._handle, self._view = handle, view
        return True

    def read(self):
        """Current bytes, or None if we're not attached or the map vanished.

        The game exiting unmaps its side; reading then can fault, so a failure
        here means "detach and go back to waiting", not "crash the listener".
        """
        if self._view is None:
            return None
        try:
            return ctypes.string_at(self._view, self.size)
        except Exception:
            self.close()
            return None

    def close(self):
        if self._view is not None:
            try:
                _k32.UnmapViewOfFile(self._view)
            except Exception:
                pass
        if self._handle is not None:
            try:
                _k32.CloseHandle(self._handle)
            except Exception:
                pass
        self._view = self._handle = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_exc):
        self.close()
