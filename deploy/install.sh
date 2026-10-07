#!/usr/bin/env bash
#
# install.sh - instaleaza monitorul pe un VPS Ubuntu/Debian (ARM sau x86).
#
# Testat pe sintaxa cu:  bash -n install.sh
# Rulare, ca root, dupa ce ai clonat repo-ul in /opt/domogled:
#
#     sudo bash deploy/install.sh
#
# Ce face:
#   1. creeaza utilizatorul de sistem `domogled` (fara shell de login)
#   2. instaleaza Python 3.12 si dependintele intr-un venv in /opt/domogled/.venv
#   3. scrie unitatile systemd pentru colector, API si consola Streamlit
#   4. optional, instaleaza Caddy pentru HTTPS automat
#
# Nu porneste serviciile daca lipseste .env cu cheile CDSE — iti spune ce sa pui.

set -euo pipefail

APP_DIR="/opt/domogled"
VENV="$APP_DIR/.venv"
USER_NAME="domogled"
SERVICE_USER_HOME="/var/lib/domogled"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m[!] %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Ruleaza cu sudo."

if [[ ! -f "$APP_DIR/live/collector.py" ]]; then
  die "Nu gasesc $APP_DIR/live/collector.py. Cloneaza repo-ul in $APP_DIR mai intai."
fi

log "1/6 Pachete de sistem"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates

# Python 3.12+ e necesar pentru sintaxa folosita in proiect
PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
log "   Python detectat: $PYVER"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
  || die "E nevoie de Python 3.11 sau mai nou (am gasit $PYVER)."

log "2/6 Utilizatorul de sistem"
if ! id -u "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$SERVICE_USER_HOME" --shell /usr/sbin/nologin "$USER_NAME"
  echo "   creat: $USER_NAME"
else
  echo "   exista deja: $USER_NAME"
fi

log "3/6 Mediul virtual si dependintele"
# venv-ul apartine utilizatorului de serviciu, nu lui root
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$APP_DIR/live/requirements.txt"
echo "   instalate dependintele din live/requirements.txt"

if [[ -f "$APP_DIR/requirements.txt" ]]; then
  "$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
  echo "   instalate dependintele pentru consola Streamlit"
fi

log "4/6 Directoare si permisiuni"
mkdir -p "$APP_DIR/live/state" "$APP_DIR/live/web/img" "$SERVICE_USER_HOME"
chown -R "$USER_NAME:$USER_NAME" "$APP_DIR/live/state" "$APP_DIR/live/web" "$SERVICE_USER_HOME"
# codul rămâne al root-ului, citibil; doar starea e scrisa de serviciu
chmod -R a+rX "$APP_DIR"

log "5/6 Unitatile systemd"
for unit in domogled-collector domogled-api domogled-streamlit; do
  install -m 0644 "$APP_DIR/deploy/$unit.service" "/etc/systemd/system/$unit.service"
  echo "   instalat: $unit.service"
done
systemctl daemon-reload
systemctl enable domogled-collector.service domogled-api.service >/dev/null

log "6/6 Caddy (HTTPS automat, optional)"
if [[ "${INSTALL_CADDY:-1}" == "1" ]] && ! command -v caddy >/dev/null 2>&1; then
  if apt-get install -y -qq caddy 2>/dev/null; then
    echo "   Caddy instalat din depozitul distribuției"
  else
    warn "Caddy nu e in depozit. Instaleaza-l manual si copiaza deploy/Caddyfile."
  fi
  [[ -f "$APP_DIR/deploy/Caddyfile" ]] && install -m 0644 "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile 2>/dev/null || true
fi

# ---------------------------------------------------------------- verificari
if [[ ! -f "$APP_DIR/.env" ]]; then
  warn "Lipseste $APP_DIR/.env — serviciile nu pornesc fara cheile CDSE."
  cat <<'EOF'

    Creeaza fisierul:

        sudo install -m 600 -o domogled -g domogled /dev/null /opt/domogled/.env
        sudo nano /opt/domogled/.env

    si pune in el:

        CDSE_CLIENT_ID=...
        CDSE_CLIENT_SECRET=...

    Apoi porneste:

        sudo systemctl start domogled-collector domogled-api

EOF
  exit 0
fi

chown "$USER_NAME:$USER_NAME" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

log "Pornire"
systemctl restart domogled-collector.service domogled-api.service
sleep 3
systemctl --no-pager --lines=0 status domogled-collector.service domogled-api.service || true

cat <<EOF

  Gata.

    jurnal colector:   journalctl -u domogled-collector -f
    jurnal API:        journalctl -u domogled-api -f
    consola Streamlit: systemctl start domogled-streamlit   (port 8501, doar local)
    stare:             systemctl status domogled-collector

  Verificare rapida:
    curl -s localhost:8777/api/health | head -c 300

EOF
