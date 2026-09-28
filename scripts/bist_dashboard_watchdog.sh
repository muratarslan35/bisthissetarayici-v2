#!/usr/bin/env bash
set -Eeuo pipefail

service_name="bist-trading-web.service"
health_url="http://127.0.0.1:5000/health"

if systemctl is-active --quiet "$service_name" && \
   curl --fail --silent --show-error --max-time 5 "$health_url" >/dev/null 2>&1; then
  exit 0
fi

logger -t bist-dashboard-watchdog \
  "dashboard unhealthy; state=$(systemctl is-active "$service_name" 2>/dev/null || true); restarting"

systemctl reset-failed "$service_name" || true
systemctl restart "$service_name"

for _ in 1 2 3 4 5 6; do
  if systemctl is-active --quiet "$service_name" && \
     curl --fail --silent --show-error --max-time 5 "$health_url" >/dev/null 2>&1; then
    logger -t bist-dashboard-watchdog "dashboard recovery succeeded"
    exit 0
  fi
  sleep 2
done

logger -t bist-dashboard-watchdog "dashboard recovery failed"
exit 1
