#!/usr/bin/env bash
# Install or update the hosted Red Ten server on an Ubuntu machine.
# See docs/hosting.md. Safe to run again: it updates the code and restarts.
#
#   sudo REDTEN_PASSCODE=some-passcode bash install.sh
#
# Optional: REPO_URL, REPO_BRANCH. The passcode is only needed the first time;
# later it is read from /etc/redten.env.
set -euo pipefail

# Everything runs inside main() so the whole file is read before any of it
# runs: an update replaces this script while it is running.
main() {
  REPO_URL="${REPO_URL:-https://github.com/yszhangit/red-ten.git}"
  REPO_BRANCH="${REPO_BRANCH:-hosted-play}"
  APP_USER=redten
  APP_HOME=/opt/redten
  APP_DIR="$APP_HOME/app"
  ENV_FILE=/etc/redten.env

  if [ "$(id -u)" -ne 0 ]; then
    echo "Run this as root (sudo)." >&2
    exit 1
  fi

  if [ ! -f "$ENV_FILE" ]; then
    if ! printf '%s' "${REDTEN_PASSCODE:-}" | grep -Eq '^[A-Za-z0-9._-]{6,64}$'; then
      echo "Set REDTEN_PASSCODE: 6-64 letters, digits, or - _ ." >&2
      exit 1
    fi
  fi

  export DEBIAN_FRONTEND=noninteractive
  # On first boot the system's own updater may hold the package lock for a while.
  apt-get -o DPkg::Lock::Timeout=300 update -y
  apt-get -o DPkg::Lock::Timeout=300 install -y git python3 python3-venv ca-certificates

  # The smallest servers have 0.5 GB of memory; a little swap keeps installs
  # and updates from being killed.
  if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ] && [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi

  if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
  fi

  # Only the server and the web client are checked out (under 1 MB); the models,
  # training data and logs in the repository are never downloaded.
  if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git clone --depth 1 --filter=blob:none --sparse \
      --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
    sudo -u "$APP_USER" git -C "$APP_DIR" sparse-checkout set src web deploy
  else
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch --depth 1 origin "$REPO_BRANCH"
    sudo -u "$APP_USER" git -C "$APP_DIR" checkout -B "$REPO_BRANCH" FETCH_HEAD
  fi

  if [ ! -x "$APP_HOME/venv/bin/python" ]; then
    sudo -u "$APP_USER" python3 -m venv "$APP_HOME/venv"
  fi
  sudo -u "$APP_USER" "$APP_HOME/venv/bin/pip" install --quiet --upgrade pip
  sudo -u "$APP_USER" "$APP_HOME/venv/bin/pip" install --quiet -r "$APP_DIR/requirements-server.txt"

  if [ ! -f "$ENV_FILE" ]; then
    sed "s/^REDTEN_PASSCODE=.*/REDTEN_PASSCODE=${REDTEN_PASSCODE}/" \
      "$APP_DIR/deploy/redten.env.example" > "$ENV_FILE"
  fi
  chown root:root "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  # Game logs for the Flag button: keep a month.
  cat > /etc/cron.daily/redten-logs <<'CRON'
#!/bin/sh
find /opt/redten/app/debug_logs -type f -mtime +30 -delete 2>/dev/null || true
CRON
  chmod 755 /etc/cron.daily/redten-logs

  install -m 644 "$APP_DIR/deploy/redten.service" /etc/systemd/system/redten.service
  systemctl daemon-reload
  systemctl enable redten >/dev/null
  systemctl restart redten

  sleep 2
  if systemctl is-active --quiet redten; then
    echo "Red Ten is running on port 80. Logs: journalctl -u redten -f"
  else
    echo "Red Ten did not start. See: journalctl -u redten -n 50" >&2
    exit 1
  fi
}

main "$@"
