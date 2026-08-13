from __future__ import annotations

import ctypes
import os
import platform
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


_MB = 1024 * 1024


def _windows_rss_bytes() -> int | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        current_process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            current_process, ctypes.byref(counters), counters.cb
        )
    except (AttributeError, OSError):
        return None
    return int(counters.WorkingSetSize) if ok else None


def _procfs_rss_bytes() -> int | None:
    try:
        fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
        return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def current_rss_bytes() -> int | None:
    """Return current-process resident memory without a non-stdlib dependency."""

    return _windows_rss_bytes() or _procfs_rss_bytes()


def _total_memory_bytes() -> int | None:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
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

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except (AttributeError, OSError):
            return None
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None


def system_profile() -> dict[str, Any]:
    total_memory = _total_memory_bytes()
    return {
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "processor": platform.processor() or platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "total_memory_gb": (
            round(total_memory / (1024**3), 3) if total_memory is not None else None
        ),
        "local_measurement_scope": "current Python process; native in-process solver work included",
        "provider_compute_scope": "OpenAI server-side GPU/FLOPs/energy not exposed by API",
    }


@dataclass
class ResourceMeter:
    """Low-overhead wall/CPU/RSS sampler for one local execution scope."""

    sample_interval_seconds: float = 0.05
    _wall_start: float | None = field(default=None, init=False)
    _cpu_start: float | None = field(default=None, init=False)
    _rss_start: int | None = field(default=None, init=False)
    _rss_peak: int | None = field(default=None, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    metrics: dict[str, Any] | None = field(default=None, init=False)

    def start(self) -> "ResourceMeter":
        if self._wall_start is not None:
            raise RuntimeError("ResourceMeter has already been started")
        self._wall_start = time.perf_counter()
        self._cpu_start = time.process_time()
        self._rss_start = current_rss_bytes()
        self._rss_peak = self._rss_start
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        return self

    def _sample(self) -> None:
        rss = current_rss_bytes()
        if rss is not None and (self._rss_peak is None or rss > self._rss_peak):
            self._rss_peak = rss

    def _sample_loop(self) -> None:
        while not self._stop_event.wait(self.sample_interval_seconds):
            self._sample()

    def stop(self) -> dict[str, Any]:
        if self.metrics is not None:
            return self.metrics
        if self._wall_start is None or self._cpu_start is None:
            raise RuntimeError("ResourceMeter must be started before stop")
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.2, self.sample_interval_seconds * 4))
        self._sample()
        wall_seconds = max(0.0, time.perf_counter() - self._wall_start)
        cpu_seconds = max(0.0, time.process_time() - self._cpu_start)
        rss_end = current_rss_bytes()
        logical_cpus = os.cpu_count() or 1
        average_cpu_cores = cpu_seconds / wall_seconds if wall_seconds > 0 else 0.0
        self.metrics = {
            "wall_seconds": round(wall_seconds, 6),
            "process_cpu_seconds": round(cpu_seconds, 6),
            "average_cpu_cores": round(average_cpu_cores, 6),
            "average_cpu_percent_total_capacity": round(
                100.0 * average_cpu_cores / logical_cpus, 6
            ),
            "logical_cpu_count": logical_cpus,
            "rss_start_mb": (
                round(self._rss_start / _MB, 3) if self._rss_start is not None else None
            ),
            "rss_end_mb": round(rss_end / _MB, 3) if rss_end is not None else None,
            "peak_rss_mb": (
                round(self._rss_peak / _MB, 3) if self._rss_peak is not None else None
            ),
            "peak_rss_delta_mb": (
                round(max(0, self._rss_peak - self._rss_start) / _MB, 3)
                if self._rss_peak is not None and self._rss_start is not None
                else None
            ),
            "memory_sampler_available": self._rss_peak is not None,
        }
        return self.metrics

    def __enter__(self) -> "ResourceMeter":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


def summarize_agent_calls(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    calls = list(rows)

    def total(key: str) -> float:
        return sum(float(row.get(key) or 0) for row in calls)

    return {
        "llm_request_attempts": len(calls),
        "llm_successful_requests": sum(bool(row.get("schema_valid")) for row in calls),
        "llm_failed_attempts": sum(not bool(row.get("schema_valid")) for row in calls),
        "llm_input_tokens": int(total("input_tokens")),
        "llm_cached_input_tokens": int(total("cached_input_tokens")),
        "llm_cache_write_tokens": int(total("cache_write_tokens")),
        "llm_uncached_input_tokens": int(total("uncached_input_tokens")),
        "llm_output_tokens": int(total("output_tokens")),
        "llm_reasoning_tokens": int(total("reasoning_tokens")),
        "llm_total_tokens": int(total("total_tokens")),
        "llm_latency_seconds": round(total("latency_seconds"), 6),
        "llm_approximate_cost_usd": round(total("approximate_cost_usd"), 8),
    }
