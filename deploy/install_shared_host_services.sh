#!/usr/bin/env bash
set -Eeuo pipefail

bist_path=${1:?BIST application path is required}

web_service="bist-trading-web.service"
worker_service="bist-trading-worker.service"
slice_name="bist-trading.slice"

case "$bist_path" in
  *ims-performance-manager*|*IMS*Performance*|*ims_performance*)
    echo "Refusing to install BIST services into an IMS path: $bist_path" >&2
    exit 1
    ;;
esac

test -d "$bist_path"
test -f "$bist_path/app.py"
test -f "$bist_path/worker.py"
test -f "$bist_path/gunicorn_bist.conf.py"
test -x "$bist_path/venv/bin/python"
test -x "$bist_path/venv/bin/gunicorn"

mkdir -p "$bist_path/data" "$bist_path/logs"
touch "$bist_path/system.db"

bist_user=$(id -un)
bist_group=$(id -gn)
escaped_path=${bist_path//|/\\|}

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

render_unit() {
  local template=$1
  local output=$2
  sed \
    -e "s|@BIST_PATH@|$escaped_path|g" \
    -e "s|@BIST_USER@|$bist_user|g" \
    -e "s|@BIST_GROUP@|$bist_group|g" \
    "$template" > "$output"
}

render_unit "$bist_path/deploy/bist-trading-web.service.in" "$tmp_dir/$web_service"
render_unit "$bist_path/deploy/bist-trading-worker.service.in" "$tmp_dir/$worker_service"
cp "$bist_path/deploy/bist-trading.slice.in" "$tmp_dir/$slice_name"

# Stable production secret, stored outside Git. Preserve any existing BIST env.
runtime_env="$tmp_dir/bist-trading.env"
if sudo test -f /etc/bist-trading.env; then
  sudo cat /etc/bist-trading.env > "$runtime_env"
else
  : > "$runtime_env"
fi

if ! grep -Eq '^[[:space:]]*SECRET_KEY[[:space:]]*=' "$runtime_env"; then
  secret=$("$bist_path/venv/bin/python" - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)
  printf '\nSECRET_KEY=%s\n' "$secret" >> "$runtime_env"
fi

sudo install -o root -g root -m 0600 "$runtime_env" /etc/bist-trading.env
sudo install -o root -g root -m 0644 "$tmp_dir/$slice_name" "/etc/systemd/system/$slice_name"
sudo install -o root -g root -m 0644 "$tmp_dir/$web_service" "/etc/systemd/system/$web_service"
sudo install -o root -g root -m 0644 "$tmp_dir/$worker_service" "/etc/systemd/system/$worker_service"

sudo systemctl daemon-reload
sudo systemctl enable "$web_service" "$worker_service"

# Safely migrate an old "python app.py" BIST process on port 5000.
# Never kill a process outside this BIST working directory.
if ! sudo systemctl is-active --quiet "$web_service"; then
  legacy_pid=$(ss -ltnp 2>/dev/null | sed -n 's/.*:5000.*pid=\([0-9]*\).*/\1/p' | head -1 || true)
  if [ -n "$legacy_pid" ]; then
    legacy_cwd=$(readlink -f "/proc/$legacy_pid/cwd" 2>/dev/null || true)
    legacy_cmd=$(ps -p "$legacy_pid" -o args= || true)

    if [ "$legacy_cwd" = "$bist_path" ] && [[ "$legacy_cmd" =~ (app\.py|gunicorn.*app:app) ]]; then
      echo "Stopping legacy BIST process pid=$legacy_pid"
      kill "$legacy_pid"
      sleep 2
    else
      echo "Port 5000 belongs to an unexpected process. Refusing to stop it." >&2
      echo "cwd=$legacy_cwd" >&2
      echo "cmd=$legacy_cmd" >&2
      exit 1
    fi
  fi
fi

# Only BIST services are restarted. IMS units are intentionally untouched.
sudo systemctl restart "$web_service"
sudo systemctl restart "$worker_service"

echo "BIST_ISOLATION|web=$(sudo systemctl is-active "$web_service")|worker=$(sudo systemctl is-active "$worker_service")"

sudo systemctl --no-pager --full status "$web_service"
sudo systemctl --no-pager --full status "$worker_service"

echo
echo "BIST resource controls:"
sudo systemctl show "$slice_name" \
  -p CPUQuotaPerSecUSec -p CPUWeight -p MemoryHigh -p MemoryMax -p MemorySwapMax -p IOWeight
sudo systemctl show "$web_service" \
  -p CPUQuotaPerSecUSec -p CPUWeight -p MemoryHigh -p MemoryMax -p OOMScoreAdjust
sudo systemctl show "$worker_service" \
  -p CPUQuotaPerSecUSec -p CPUWeight -p MemoryHigh -p MemoryMax -p OOMScoreAdjust

echo
echo "IMS services (read-only status check; no restart/change performed):"
for svc in ims-performance-manager.service ims-import-worker.service ims-report-worker.service; do
  if systemctl list-unit-files "$svc" --no-legend 2>/dev/null | grep -q "$svc"; then
    echo "$svc=$(sudo systemctl is-active "$svc" || true)"
  fi
done
