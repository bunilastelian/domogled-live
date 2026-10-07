#!/usr/bin/env bash
#
# publish.sh - publica starea colectata pe GitHub Pages.
#
# Ruleaza dupa fiecare ciclu de colectare (apelat de systemd timer).
# Comite DOAR fisierele de stare si imaginile produse de colector - niciodata
# cod, niciodata .env. Daca nu s-a schimbat nimic, nu face commit.
#
# Conflicte: inainte de push face `pull --rebase` cu autostash, deci daca
# GitHub Actions (sau altcineva) a comis intre timp, istoricul se reaseaza
# curat in loc sa dea "rejected".
#
# Log: logs/publish.log

set -uo pipefail

REPO="/home/lili/projects/domogled-live"
LOG="$REPO/logs/publish.log"
LOCK="$REPO/logs/.publish.lock"

cd "$REPO" || exit 1
mkdir -p "$REPO/logs"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*" >> "$LOG"; }

# Un singur publish odata. Daca rularea anterioara inca tine lock-ul, iesim.
if [[ -f "$LOCK" ]]; then
  # lock mai vechi de 10 minute = proces mort, il ignoram
  if [[ -n "$(find "$LOCK" -mmin +10 2>/dev/null)" ]]; then
    log "lock vechi gasit, il sterg"
    rm -f "$LOCK"
  else
    log "alt publish ruleaza, ies"
    exit 0
  fi
fi
trap 'rm -f "$LOCK"' EXIT
: > "$LOCK"

# Token-ul sta in ~/.hermes/.env, nu in repo.
if [[ -f "$HOME/.hermes/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$HOME/.hermes/.env" 2>/dev/null
  set +a
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  log "FARA GITHUB_TOKEN - nu pot publica"
  exit 1
fi

REMOTE="https://${GITHUB_TOKEN}@github.com/bunilastelian/domogled-live.git"

# Doar starea si ce produce colectorul. Nimic altceva nu intra in commit.
git add -f \
  live/state/state.json \
  live/web/state/state.json \
  live/web/state/harness.json \
  live/web/img \
  live/web/domogled.geojson \
  live/web/portiledefier.geojson 2>/dev/null

if git diff --staged --quiet; then
  log "nimic nou"
  exit 0
fi

UPD=$(python3 -c "
import json
try:
    d=json.load(open('live/state/state.json'))
    print(d.get('updated','?'))
except Exception:
    print('?')
" 2>/dev/null)

git -c user.name="domogled-local" \
    -c user.email="bot@users.noreply.github.com" \
    commit -q -m "date live ${UPD:0:16}" 2>>"$LOG" || { log "commit a esuat"; exit 1; }

# Rebase inainte de push, ca sa nu pierdem commit-uri facute de altcineva.
git pull --rebase --autostash --quiet "$REMOTE" main >>"$LOG" 2>&1
if ! git push --quiet "$REMOTE" HEAD:main >>"$LOG" 2>&1; then
  log "PUSH A ESUAT pentru $UPD"
  exit 1
fi

log "publicat: $UPD"
exit 0
