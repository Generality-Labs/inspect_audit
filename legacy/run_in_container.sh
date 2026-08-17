#!/usr/bin/env bash
# Start an isolated audit container for a cell. The cell dir is mounted at /audit, so the
# auditor works on the live cell inside the container and its outputs persist to the host.
#
# Usage: ./run_in_container.sh cells/sqav-892     -> prints the container name to exec into
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cell="$(cd "$1" && pwd)"
name="audit-$(basename "$cell")"

docker build -q -t bench-auditor:latest "$here/docker" >/dev/null
docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --name "$name" -v "$cell:/audit" bench-auditor:latest >/dev/null

echo "$name"          # the auditor runs everything as:  docker exec $name bash -lc '...'
