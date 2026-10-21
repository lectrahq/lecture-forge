#!/usr/bin/env bash
# Browser checks against a copy of the live site.
#
#   bash tests/run_ui_check.sh
#
# Starts the real built website on localhost:8099 without the password (the suite has
# the password hash, not the password), runs the checks in a real browser, writes
# screenshots to tests/screenshots/, then tears everything down.
set -u
cd "$(dirname "$0")/.." || exit 1

PROJECT=$(basename "$PWD")
NETWORK="${PROJECT}_default"
SHOTS="$PWD/tests/screenshots"
PLAYWRIGHT_IMAGE="${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.47.0-jammy}"

cleanup() {
    docker rm -f ui-check-site >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "$SHOTS"
cleanup

echo "=== starting a copy of the site on localhost:8099 (no password)"
docker run -d --name ui-check-site --network "$NETWORK" -p 127.0.0.1:8099:8099 \
    -v "$PWD/tests/test-Caddyfile:/etc/caddy/Caddyfile:ro" \
    "${PROJECT}-caddy:latest" >/dev/null || {
        echo "could not start the test site - is the caddy image built?"; exit 1; }
sleep 4

if ! curl -sf --max-time 10 http://127.0.0.1:8099/ >/dev/null; then
    echo "the test site is not answering on 8099"
    docker logs ui-check-site 2>&1 | tail -20
    exit 1
fi
echo "    up"

echo "=== running the browser checks"
# The image ships the browsers but not the Python package, so install it here,
# pinned to the same version as the bundled browser build.
docker run --rm --network host \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    -v "$PWD/tests:/tests:ro" -v "$SHOTS:/out" \
    "$PLAYWRIGHT_IMAGE" \
    sh -c 'pip install --quiet playwright==1.47.0 && python /tests/ui_check.py'
STATUS=$?

echo
echo "screenshots: $SHOTS"
exit $STATUS
