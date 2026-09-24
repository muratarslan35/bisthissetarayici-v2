"""Low-footprint Gunicorn config for the BIST dashboard on a shared IMS host."""

import os


def _positive_int(name, default):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Preserve the historical BIST port while binding only to loopback.
bind = os.getenv("BIST_GUNICORN_BIND", "127.0.0.1:5000")

# Dashboard is light and DB-backed. One threaded worker avoids duplicating
# pandas/import state and keeps BIST memory usage predictable beside IMS.
workers = _positive_int("BIST_GUNICORN_WORKERS", 1)
worker_class = "gthread"
threads = _positive_int("BIST_GUNICORN_THREADS", 2)

timeout = _positive_int("BIST_GUNICORN_TIMEOUT", 120)
graceful_timeout = _positive_int("BIST_GUNICORN_GRACEFUL_TIMEOUT", 30)
keepalive = _positive_int("BIST_GUNICORN_KEEPALIVE", 5)

max_requests = _positive_int("BIST_GUNICORN_MAX_REQUESTS", 1500)
max_requests_jitter = _positive_int("BIST_GUNICORN_MAX_REQUESTS_JITTER", 100)

preload_app = False
accesslog = "-"
errorlog = "-"
capture_output = True
loglevel = os.getenv("BIST_GUNICORN_LOG_LEVEL", "info")
