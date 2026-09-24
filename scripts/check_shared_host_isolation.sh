#!/usr/bin/env bash
set -Eeuo pipefail

echo "=== BIST shared-host isolation check ==="

for svc in bist-trading-web.service bist-trading-worker.service; do
  echo
  echo "[$svc]"
  systemctl is-active "$svc" || true
  systemctl show "$svc" \
    -p Slice \
    -p MainPID \
    -p CPUQuotaPerSecUSec \
    -p CPUWeight \
    -p MemoryCurrent \
    -p MemoryHigh \
    -p MemoryMax \
    -p MemorySwapMax \
    -p IOWeight \
    -p OOMScoreAdjust
done

echo
echo "[bist-trading.slice]"
systemctl show bist-trading.slice \
  -p CPUQuotaPerSecUSec \
  -p CPUWeight \
  -p MemoryCurrent \
  -p MemoryHigh \
  -p MemoryMax \
  -p MemorySwapMax \
  -p IOWeight

echo
echo "[ports]"
ss -ltnp | grep -E ':(5000|8000)[[:space:]]' || true

echo
echo "[IMS status - read only]"
for svc in ims-performance-manager.service ims-import-worker.service ims-report-worker.service; do
  if systemctl list-unit-files "$svc" --no-legend 2>/dev/null | grep -q "$svc"; then
    echo "$svc=$(systemctl is-active "$svc" || true)"
  fi
done

echo
echo "[host]"
awk '/MemTotal:|MemAvailable:|SwapTotal:|SwapFree:/ {print}' /proc/meminfo
uptime
