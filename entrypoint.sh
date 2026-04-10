#!/bin/sh
set -e

# Fix ownership of /app/data so the nonroot user (999) can write to it.
# Needed because `docker cp` and fresh volume mounts create root-owned files.
chown -R nonroot:nonroot /app/data 2>/dev/null || true

# Drop privileges and run the command as nonroot
exec setpriv --reuid=999 --regid=999 --init-groups "$@"
