#!/bin/bash
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Hardcoded layout:
#   /data/download  /data/external  /data/cache   ← media (./data:/data)
#   /config                                 ← settings/DB (./config:/config)
# Optional SSD: bind host path over /data/cache (same app path).
LIBRARY_ROOT=/data
DATA_DIR=/data/download
EXTERNAL_DIR=/data/external
CONFIG_DIR=/config
CACHE_DIR=/data/cache

warn_chown() {
    path=$1
    echo "Warning: could not change ownership of '$path' to $PUID:$PGID; continuing because the mount may not support chown." >&2
}

ensure_writable() {
    path=$1

    if ! gosu "$PUID:$PGID" test -w "$path"; then
        echo "Error: '$path' is not writable by $PUID:$PGID. Set PUID/PGID to an account with write access to this mount." >&2
        exit 1
    fi
}

case "$PUID" in
    "" | *[!0-9]*)
        echo "PUID must be a numeric user id, got '$PUID'" >&2
        exit 1
        ;;
esac

case "$PGID" in
    "" | *[!0-9]*)
        echo "PGID must be a numeric group id, got '$PGID'" >&2
        exit 1
        ;;
esac

if [ "$(id -u)" = "0" ]; then
    mkdir -p \
        "$CONFIG_DIR/yubal" \
        "$CONFIG_DIR/ytdlp" \
        "$DATA_DIR" \
        "$EXTERNAL_DIR/raw" \
        "$EXTERNAL_DIR/organized" \
        "$CACHE_DIR"

    chown "$PUID:$PGID" "$DATA_DIR" || warn_chown "$DATA_DIR"
    chown "$PUID:$PGID" "$EXTERNAL_DIR" || warn_chown "$EXTERNAL_DIR"
    chown -R "$PUID:$PGID" "$EXTERNAL_DIR/raw" "$EXTERNAL_DIR/organized" || warn_chown "$EXTERNAL_DIR"
    chown "$PUID:$PGID" "$CACHE_DIR" || warn_chown "$CACHE_DIR"
    chown -R "$PUID:$PGID" "$CONFIG_DIR" || warn_chown "$CONFIG_DIR"
    ensure_writable "$DATA_DIR"

    touch "$DATA_DIR/.yubal-mount" || warn_chown "$DATA_DIR/.yubal-mount"
    touch "$EXTERNAL_DIR/.yubal-mount" || warn_chown "$EXTERNAL_DIR/.yubal-mount"
    chown "$PUID:$PGID" "$DATA_DIR/.yubal-mount" "$EXTERNAL_DIR/.yubal-mount" 2>/dev/null || true

    exec gosu "$PUID:$PGID" "$@"
fi

mkdir -p \
    "$CONFIG_DIR/yubal" \
    "$CONFIG_DIR/ytdlp" \
    "$DATA_DIR" \
    "$EXTERNAL_DIR/raw" \
    "$EXTERNAL_DIR/organized" \
    "$CACHE_DIR" 2>/dev/null || true
touch "$DATA_DIR/.yubal-mount" "$EXTERNAL_DIR/.yubal-mount" 2>/dev/null || true
exec "$@"
