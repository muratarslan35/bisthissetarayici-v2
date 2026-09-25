#!/usr/bin/env bash
set -Eeuo pipefail

bist_path=${1:?BIST application path is required}
web_service="bist-trading-web.service"
worker_service="bist-trading-worker.service"
slice_name="bist-trading.slice"

case "$bist_path" in
  *ims-performance-manager*|*ims_system*|*IMS*Performance*)
    echo "Refusing BIST install into IMS path: $bist_path" >&2
    exit 1
    ;;
esac

test -d "$bist_path"
test -f "$bist_path/app.py"
test -f "$bist_path/worker.py"
test -x "$bist_path/venv/bin/python"
test -x "$bist_path/venv/bin/gunicorn"

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

render_unit "$bist_path/deploy/bist-dedicated-web.service.in" "$tmp_dir/$web_service"
render_unit "$bist_path/deploy/bist-dedicated-worker.service.in" "$tmp_dir/$worker_service"
cp "$bist_path/deploy/bist-dedicated.slice.in" "$tmp_dir/$slice_name"

runtime_env="$tmp_dir/bist-trading.env"
if sudo test -f /etc/bist-trading.env; then
  sudo cat /etc/bist-trading.env > "$runtime_env"
else
  : > "$runtime_env"
fi

if ! grep -Eq '^[[:space:]]*SECRET_KEY[[:space:]]*=' "$runtime_env"; then
  if grep -Eq '^[[:space:]]*SECRET_KEY[[:space:]]*=' "$bist_path/.env" 2>/dev/null; then
    grep -E '^[[:space:]]*SECRET_KEY[[:space:]]*=' "$bist_path/.env" | tail -1 >> "$runtime_env"
  else
    secret=$("$bist_path/venv/bin/python" - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)
    printf '\nSECRET_KEY=%s\n' "$secret" >> "$runtime_env"
  fi
fi

sudo install -o root -g root -m 0600 "$runtime_env" /etc/bist-trading.env
sudo install -o root -g root -m 0644 "$tmp_dir/$slice_name" "/etc/systemd/system/$slice_name"
sudo install -o root -g root -m 0644 "$tmp_dir/$web_service" "/etc/systemd/system/$web_service"
sudo install -o root -g root -m 0644 "$tmp_dir/$worker_service" "/etc/systemd/system/$worker_service"

sudo systemctl daemon-reload
sudo systemctl enable "$web_service" "$worker_service"
sudo systemctl restart "$web_service"
sudo systemctl restart "$worker_service"

# Public browser access is intentionally limited to the authenticated Flask app
# on the dedicated BIST VM. If UFW is active, open only the dashboard port.
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q '^Status: active'; then
  sudo ufw allow 5000/tcp >/dev/null
fi

sleep 3

echo "BIST_WEB=$(sudo systemctl is-active "$web_service")"
echo "BIST_WORKER=$(sudo systemctl is-active "$worker_service")"

curl --fail --silent --show-error http://127.0.0.1:5000/health >/tmp/bist-health.json
python3 - <<'PY'
import json
with open('/tmp/bist-health.json', encoding='utf-8') as f:
    data=json.load(f)
print("BIST_HEALTH_OK", data.get("status"), data.get("kap_status"))
PY

sudo systemctl show "$slice_name" \
  -p CPUQuotaPerSecUSec -p MemoryHigh -p MemoryMax -p MemorySwapMax
