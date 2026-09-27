#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCT="Network Log Manager"
PRODUCT_ID="netlog-manager"
CONFIG_DIR="/etc/netlog-manager"
STATE_DIR="/var/lib/netlog-manager"
BACKUP_DIR="/var/backups/netlog-manager"
INSTALL_STATE="$STATE_DIR/install.env"

log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die(){ log "ERROR: $*"; exit 1; }
require_root(){ [[ $EUID -eq 0 ]] || die "Run as root."; }
command_exists(){ command -v "$1" >/dev/null 2>&1; }
prompt_default(){ local __v=$1 __p=$2 __d=$3 __r; read -r -p "$__p [$__d]: " __r; printf -v "$__v" '%s' "${__r:-$__d}"; }
prompt_secret(){ local __v=$1 __p=$2 __a __b; while :; do read -r -s -p "$__p: " __a; echo; read -r -s -p "Confirm $__p: " __b; echo; [[ -n "$__a" && "$__a" == "$__b" ]] && break; echo "Values do not match."; done; printf -v "$__v" '%s' "$__a"; }
random_secret(){ openssl rand -hex "${1:-32}"; }
save_install_state(){ install -d -m 0750 "$STATE_DIR"; cat >"$INSTALL_STATE"; chmod 0600 "$INSTALL_STATE"; }
load_install_state(){ [[ -r "$INSTALL_STATE" ]] || die "Installation state not found: $INSTALL_STATE"; # shellcheck disable=SC1090
 source "$INSTALL_STATE"; }
