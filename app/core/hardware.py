"""What this machine has: cores and memory, read once and said plainly (ROAD_TO_10 6.1).

Nothing in `app/` read the core count or the RAM, so `job_workers = 2` was a number somebody
typed and every later decision about parallelism (6.2) or admission (6.3) would have been a
guess about a machine nobody had looked at. This is the looking, and **only** the looking: it
decides nothing and imports nothing from the rest of the application, so every layer may ask it.

**Three facts that are easy to get wrong, each of which gives a plausible number:**

1. **`os.cpu_count()` is the host's, not the container's.** In a container limited to two CPUs on
   a 64-core host it says 64, and a worker count derived from it oversubscribes by thirty times.
   The usable count is the *affinity mask* where the platform has one, capped by a cgroup CPU
   quota where one is set. Memory is the same: `MemTotal` is the host's, and a cgroup limit below
   it is the real ceiling, so it wins.
2. **Logical is not physical.** A BLAS solve on a hyperthreaded core gains little from the
   sibling, so planning by logical cores doubles the figure. The physical count is `None` where
   the platform does not say (a container's `/proc/cpuinfo` may omit `core id`), and `None` is
   what is reported — the caller states its assumption instead of this module inventing one.
3. **Available is not free.** `MemAvailable` counts page cache the kernel will give back, which is
   the figure an allocation can actually draw on; `MemFree` would call a healthy machine full.
   Windows' `ullAvailPhys` is the same notion. Available changes by the second, so it is asked
   for live (`available_ram_mb`) and never stored beside the static facts.

Standard library only. `psutil` would do all of this and would be a new dependency on a path
every install runs; the three platform reads below are small enough to own.

**What is measured and what is not.** The Linux reads run in the tests against recorded text and
against this machine. The Windows reads (`GlobalMemoryStatusEx`, `GetLogicalProcessorInformation`)
have their *parsers* tested against a buffer built to the documented layout and have **never run
on Windows** — a row in THE QUEUE, because a `ctypes` structure that is a byte too long reads
plausible nonsense rather than failing.
"""

from __future__ import annotations

import ctypes
import math
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: Reads a text file, or None where it does not exist. Injected so every parser is tested on
#: recorded text rather than on whichever machine runs the suite.
ReadText = Callable[[str], str | None]

_MB = 1024 * 1024

#: cgroup v1 reports "no limit" as a number just under 2**63; anything above this is "none".
_CGROUP_V1_UNLIMITED = 1 << 60


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


@dataclass(frozen=True)
class Hardware:
    """The facts about this machine that do not change while the process runs."""

    #: Cores this process may run on: the affinity mask, capped by a CPU quota. At least 1.
    logical_cores: int
    #: Physical cores, or None where the platform does not say. Never guessed here.
    physical_cores: int | None
    #: Memory this process may use: the machine's, or the container limit if that is lower.
    total_ram_mb: int | None
    #: Where each figure came from, so "None" is explained rather than blank.
    source: str
    #: Anything that made a figure smaller than the machine's own, in words.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_cores": self.logical_cores,
            "physical_cores": self.physical_cores,
            "total_ram_mb": self.total_ram_mb,
            "source": self.source,
            "notes": list(self.notes),
        }


# -- parsers (pure) -------------------------------------------------------------------------


_MEMINFO_RE = re.compile(r"^(MemTotal|MemAvailable):\s+(\d+)\s*kB", re.MULTILINE)


def parse_meminfo(text: str) -> tuple[int | None, int | None]:
    """`(total_mb, available_mb)` from `/proc/meminfo`, either None if the line is absent."""
    found = {name: int(value) for name, value in _MEMINFO_RE.findall(text)}
    total = found.get("MemTotal")
    available = found.get("MemAvailable")
    return (
        total // 1024 if total is not None else None,
        available // 1024 if available is not None else None,
    )


def parse_physical_cores(cpuinfo: str) -> int | None:
    """Distinct `(physical id, core id)` pairs in `/proc/cpuinfo`, or None if it does not say.

    A machine that lists processors with no `core id` (some virtual machines, some ARM boards)
    gives no basis for a count, so the answer is None and not the number of processors.
    """
    pairs: set[tuple[str, str]] = set()
    physical = "0"
    for line in cpuinfo.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "physical id":
            physical = value.strip()
        elif key == "core id":
            pairs.add((physical, value.strip()))
        elif key == "" and not line.strip():
            physical = "0"
    return len(pairs) or None


def parse_cpu_quota(cpu_max: str) -> float | None:
    """Cores a cgroup v2 `cpu.max` allows (`"200000 100000"` → 2.0), None for `max`."""
    parts = cpu_max.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return quota / period if period > 0 else None


def parse_cgroup_limit(text: str | None) -> int | None:
    """Bytes from a cgroup memory limit file; None for `max`, an absent file or v1's 'no limit'."""
    if text is None:
        return None
    value = text.strip()
    if not value or value == "max":
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return None if number >= _CGROUP_V1_UNLIMITED else number


def count_windows_cores(buffer: bytes, pointer_size: int) -> int | None:
    """`RelationProcessorCore` entries in a `GetLogicalProcessorInformation` buffer.

    Each `SYSTEM_LOGICAL_PROCESSOR_INFORMATION` is a pointer-sized mask, a 4-byte relationship
    padded to pointer alignment, and 16 bytes of union: 24 bytes on a 32-bit build, 32 on a
    64-bit one. Relationship 0 is `RelationProcessorCore`. A buffer that is not a whole number of
    entries is refused rather than read, because a misaligned read returns plausible nonsense.
    """
    size = 2 * pointer_size + 16
    if not buffer or len(buffer) % size:
        return None
    cores = 0
    for offset in range(0, len(buffer), size):
        relationship = int.from_bytes(
            buffer[offset + pointer_size : offset + pointer_size + 4], "little"
        )
        if relationship == 0:
            cores += 1
    return cores or None


# -- the reads ------------------------------------------------------------------------------


def _usable_cores() -> tuple[int, tuple[str, ...]]:
    """Logical cores this process may use, and why it is not `os.cpu_count()` if it is not."""
    notes: list[str] = []
    host = os.cpu_count() or 1
    count = host
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        allowed = len(affinity(0))
        if 0 < allowed < count:
            notes.append(f"this process is limited to {allowed} of the host's {host} logical cores")
            count = allowed
    return max(count, 1), tuple(notes)


def _linux(read: ReadText) -> Hardware:
    notes: list[str] = []
    logical, core_notes = _usable_cores()
    notes.extend(core_notes)

    quota = parse_cpu_quota(read("/sys/fs/cgroup/cpu.max") or "")
    if quota is not None and 0 < quota < logical:
        capped = max(1, math.ceil(quota))
        notes.append(f"a container CPU quota of {quota:g} caps the {logical} logical cores")
        logical = capped

    cpuinfo = read("/proc/cpuinfo")
    physical = parse_physical_cores(cpuinfo) if cpuinfo else None
    if physical is not None and physical > logical:
        physical = logical

    meminfo = read("/proc/meminfo")
    total = parse_meminfo(meminfo)[0] if meminfo else None
    limit = parse_cgroup_limit(read("/sys/fs/cgroup/memory.max")) or parse_cgroup_limit(
        read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    )
    if limit is not None:
        limit_mb = limit // _MB
        if total is None or limit_mb < total:
            if total is not None:
                notes.append(f"a container memory limit of {limit_mb} MB is below the host's {total} MB")
            total = limit_mb
    if physical is None:
        notes.append("the physical core count is not reported on this machine")
    return Hardware(logical, physical, total, "linux:/proc", tuple(notes))


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_memory() -> tuple[int | None, int | None]:
    if sys.platform != "win32":
        return None, None
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined,unused-ignore]
        return None, None
    return int(status.ullTotalPhys) // _MB, int(status.ullAvailPhys) // _MB


def _windows_physical_cores() -> int | None:
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    needed = ctypes.c_ulong(0)
    kernel32.GetLogicalProcessorInformation(None, ctypes.byref(needed))
    if needed.value == 0:
        return None
    buffer = ctypes.create_string_buffer(needed.value)
    if not kernel32.GetLogicalProcessorInformation(buffer, ctypes.byref(needed)):
        return None
    return count_windows_cores(buffer.raw[: needed.value], ctypes.sizeof(ctypes.c_size_t))


def _windows() -> Hardware:
    logical, notes = _usable_cores()
    physical = _windows_physical_cores()
    total, _ = _windows_memory()
    extra = list(notes)
    if physical is None:
        extra.append("the physical core count could not be read")
    return Hardware(logical, physical, total, "windows:kernel32", tuple(extra))


def _elsewhere() -> Hardware:
    logical, notes = _usable_cores()
    total: int | None = None
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // _MB
    except (ValueError, OSError, AttributeError):
        total = None
    return Hardware(
        logical,
        None,
        total,
        "posix:sysconf",
        (*notes, "no platform-specific reader for this system; the physical core count is unknown"),
    )


def probe(read: ReadText | None = None, *, platform: str | None = None) -> Hardware:
    """Read the machine. `read` and `platform` are injected only by tests."""
    chosen = platform or sys.platform
    if chosen.startswith("linux"):
        return _linux(read or _read)
    if chosen == "win32":
        return _windows()
    return _elsewhere()


@lru_cache(maxsize=1)
def hardware() -> Hardware:
    """The static facts, read once per process."""
    return probe()


def available_ram_mb(read: ReadText | None = None, *, platform: str | None = None) -> int | None:
    """Memory an allocation can draw on right now, or None where it cannot be read.

    Live on every call. Capped by a container limit's headroom (`limit - current`) where there
    is one, because the host's `MemAvailable` is not the container's.
    """
    chosen = platform or sys.platform
    reader = read or _read
    if chosen.startswith("linux"):
        meminfo = reader("/proc/meminfo")
        available = parse_meminfo(meminfo)[1] if meminfo else None
        limit = parse_cgroup_limit(reader("/sys/fs/cgroup/memory.max"))
        current = reader("/sys/fs/cgroup/memory.current")
        if limit is not None and current is not None:
            try:
                headroom = max(limit - int(current.strip()), 0) // _MB
            except ValueError:
                return available
            return headroom if available is None else min(available, headroom)
        return available
    if chosen == "win32":
        return _windows_memory()[1]
    return None


__all__ = [
    "Hardware",
    "available_ram_mb",
    "count_windows_cores",
    "hardware",
    "parse_cgroup_limit",
    "parse_cpu_quota",
    "parse_meminfo",
    "parse_physical_cores",
    "probe",
]
