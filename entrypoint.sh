#!/bin/sh
# Runs as root (the image's default user) so it can fix ownership of the
# bind-mounted /app/data volume, which Docker creates owned by root on the
# host if it doesn't already exist — appuser can't write into that as-is.
# Then drops to appuser before actually running the app.
set -e

mkdir -p /app/data
chown -R appuser:appuser /app/data

exec su-exec appuser "$@"
