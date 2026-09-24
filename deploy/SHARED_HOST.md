# Shared Host Isolation: IMS + BIST

This configuration protects the existing IMS production service by constraining
BIST. IMS files, units and deploy workflows are not modified.

## Runtime topology

```text
Host
├─ IMS services (unchanged)
│  ├─ ims-performance-manager.service
│  ├─ ims-import-worker.service
│  └─ ims-report-worker.service
│
└─ bist-trading.slice
   ├─ bist-trading-web.service
   └─ bist-trading-worker.service
```

BIST web and worker are independent processes. The web process serves the
dashboard; the worker owns Yahoo/KAP scanning and signal calculations.

## BIST hard limits

The BIST slice has:
- total CPU cap: 80% of one CPU core
- CPU weight: 10
- memory soft pressure: 30% of host RAM
- memory hard cap: 40% of host RAM
- swap cap: 5% of host RAM
- I/O weight: 10

The scanner worker is additionally:
- Nice=12
- CPUQuota=70%
- CPUWeight=10
- MemoryHigh=25%
- MemoryMax=32%
- IOSchedulingClass=idle
- OOMScoreAdjust=800
- BLAS/OpenMP/NumExpr thread count fixed to 1

The dashboard web service is intentionally small:
- one Gunicorn worker
- two gthread threads
- CPUQuota=20%
- MemoryMax=12%

The parent BIST slice is the final cap even if child limits overlap.

## Cooperative backpressure

Before a new heavy market-data/universe evaluation, BIST checks:
- MemAvailable
- available-memory ratio
- 1-minute load average per CPU

Defaults:
- minimum available memory: 512 MiB
- minimum available ratio: 15%
- maximum load/CPU: 0.90
- pressure backoff: 15 seconds

Under pressure the BIST scanner skips the heavy Yahoo/279-symbol cycle and yields
to the host. Lightweight KAP polling and heartbeat work remain above that gate.

## Network/port separation

IMS Gunicorn remains on its existing port 8000.

BIST Gunicorn preserves the historical BIST port but binds only to loopback:

```text
127.0.0.1:5000
```

The installer refuses to kill anything on port 5000 unless that process has the
BIST repository as its working directory.

## Safe installation

Run from the BIST machine/project context:

```bash
bash deploy/install_shared_host_services.sh /absolute/path/to/bisthissetarayici-v2
```

The installer:
- refuses IMS-like target paths
- installs only `bist-trading*` systemd units
- creates/stabilizes a production SECRET_KEY outside Git
- safely migrates an old BIST `python app.py` process on port 5000
- restarts only BIST web/worker services
- performs only read-only IMS status checks

It never restarts, stops, enables or edits an IMS service.

## Verification

After installation:

```bash
bash scripts/check_shared_host_isolation.sh
```

During a real IMS import, also observe:

```bash
systemd-cgtop
systemctl status bist-trading-worker
systemctl status ims-performance-manager
```

Expected behavior:
- IMS web remains responsive.
- BIST scanning may slow or skip a cycle under contention.
- BIST does not consume more than its slice CPU/RAM boundary.
- BIST worker is a preferred OOM victim before IMS if host pressure becomes extreme.

This design intentionally favors IMS latency over BIST scan cadence.
