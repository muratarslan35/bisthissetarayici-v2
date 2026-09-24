import os
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PressureState:
    should_yield: bool
    reason: str
    available_mb: float
    available_ratio: float
    load_per_cpu: float
    backoff_seconds: int


def _memory_state():
    total_kb = 0
    available_kb = 0

    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available_kb = int(line.split()[1])
    except Exception:
        return 0.0, 1.0

    if total_kb <= 0:
        return 0.0, 1.0

    available_mb = available_kb / 1024.0
    return available_mb, available_kb / total_kb


def _load_per_cpu():
    try:
        load_1m = os.getloadavg()[0]
        cpus = max(os.cpu_count() or 1, 1)
        return load_1m / cpus
    except Exception:
        return 0.0


def host_pressure_state():
    """
    Cooperative backpressure for the shared IMS/BIST host.

    systemd/cgroups remain the hard resource boundary. This guard adds a softer
    application-level rule: under host pressure the BIST worker yields before
    starting a full Yahoo + 279-symbol calculation cycle.
    """
    available_mb, available_ratio = _memory_state()
    load_per_cpu = _load_per_cpu()

    min_available_mb = max(
        128.0,
        float(os.getenv("BIST_MIN_AVAILABLE_MEMORY_MB", "512")),
    )
    min_available_ratio = min(
        0.50,
        max(0.05, float(os.getenv("BIST_MIN_AVAILABLE_MEMORY_RATIO", "0.15"))),
    )
    max_load_per_cpu = max(
        0.20,
        float(os.getenv("BIST_MAX_LOAD_PER_CPU", "0.90")),
    )
    backoff = max(
        5,
        int(os.getenv("BIST_PRESSURE_BACKOFF_SECONDS", "15")),
    )

    memory_low = (
        available_mb > 0
        and (
            available_mb < min_available_mb
            or available_ratio < min_available_ratio
        )
    )
    cpu_busy = load_per_cpu >= max_load_per_cpu

    if memory_low and cpu_busy:
        reason = "memory+cpu"
    elif memory_low:
        reason = "memory"
    elif cpu_busy:
        reason = "cpu"
    else:
        reason = "ok"

    return PressureState(
        should_yield=memory_low or cpu_busy,
        reason=reason,
        available_mb=round(available_mb, 1),
        available_ratio=round(available_ratio, 4),
        load_per_cpu=round(load_per_cpu, 3),
        backoff_seconds=backoff,
    )


def cooperative_backoff_if_needed(log=print):
    state = host_pressure_state()
    if not state.should_yield:
        return state

    log(
        "BIST RESOURCE YIELD "
        f"reason={state.reason} "
        f"available_mb={state.available_mb} "
        f"available_ratio={state.available_ratio:.1%} "
        f"load_per_cpu={state.load_per_cpu} "
        f"backoff={state.backoff_seconds}s"
    )
    time.sleep(state.backoff_seconds)
    return state
