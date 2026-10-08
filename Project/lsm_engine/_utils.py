import os
import sys
import time


def fsync_fd(fd: int):
    """Durable sync: F_FULLFSYNC on macOS, standard fsync elsewhere."""
    if sys.platform == 'darwin':
        F_FULLFSYNC = 51
        try:
            import fcntl
            fcntl.fcntl(fd, F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def fsync_file(path: str):
    fd = os.open(path, os.O_RDONLY)
    try:
        fsync_fd(fd)
    finally:
        os.close(fd)


def fsync_dir(path: str):
    """fsync directory so that a preceding rename() becomes durable."""
    fd = os.open(path, os.O_RDONLY)
    try:
        fsync_fd(fd)
    finally:
        os.close(fd)


class RateLimiter:
    """Token-bucket rate limiter for compaction I/O throttling.

    After each call to consume(n_bytes), sleeps if the cumulative bytes
    written are ahead of schedule at the configured rate.  Check granularity
    is per-call, so callers should batch calls (e.g. every N entries) to
    avoid per-entry overhead.

    bytes_per_sec <= 0 means unlimited (all consume() calls are no-ops).
    """

    def __init__(self, bytes_per_sec: float = 0):
        self._rate  = bytes_per_sec
        self._start = time.monotonic()
        self._total = 0

    def consume(self, n_bytes: int):
        if self._rate <= 0 or n_bytes <= 0:
            return
        self._total += n_bytes
        # How long should this many bytes have taken at the target rate?
        allowed = self._total / self._rate
        actual  = time.monotonic() - self._start
        gap = allowed - actual
        if gap > 0.001:   # only sleep if > 1 ms ahead
            time.sleep(gap)

    def reset(self):
        """Reset the clock (e.g. between compaction jobs)."""
        self._start = time.monotonic()
        self._total = 0
