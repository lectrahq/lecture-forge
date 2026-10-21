#!/usr/bin/env bash
# Every check, in one command. Run after any change.
#
#   bash tests/run_all.sh                # free checks only
#   bash tests/run_all.sh --include-paid # also the ElevenLabs checks (~21 credits)
#
# Exits non-zero if anything fails, so it can gate a deployment.
set -u
cd "$(dirname "$0")/.." || exit 1

PAID="${1:-}"
FAILED=0

banner() { printf '\n\033[1m=== %s\033[0m\n' "$1"; }

banner "0. the running container has the current code"
# This check exists because a fix sitting on disk but not loaded by the running
# process produced two false diagnoses on this project.
for f in main.py db.py normalise.py local_asr.py; do
    if docker compose exec -T backend sh -c "test -f /app/$f" </dev/null; then
        host=$(md5sum "backend/$f" | cut -d' ' -f1)
        cont=$(docker compose exec -T backend md5sum "/app/$f" </dev/null | cut -d' ' -f1)
        if [ "$host" = "$cont" ]; then
            echo "  PASS  backend/$f matches the running container"
        else
            echo "  FAIL  backend/$f differs from the running container - rebuild it"
            FAILED=1
        fi
    fi
done
for f in common.py:gpu/common.py server.py:asr/server.py; do
    name="${f%%:*}"; path="${f##*:}"
    host=$(md5sum "$path" | cut -d' ' -f1)
    cont=$(docker compose exec -T asr md5sum "/app/$name" </dev/null | cut -d' ' -f1)
    if [ "$host" = "$cont" ]; then
        echo "  PASS  $path matches the running asr container"
    else
        echo "  FAIL  $path differs - 'docker compose restart asr' (up -d will not do it)"
        FAILED=1
    fi
done

banner "1. unit tests"
docker compose exec -T backend python -m unittest discover -s /app -p 'test_*.py' </dev/null || FAILED=1

banner "2. API suite"
docker compose exec -T backend python /app/tests/api_suite.py $PAID </dev/null || FAILED=1

banner "3. browser checks"
bash tests/run_ui_check.sh || FAILED=1

banner "result"
if [ "$FAILED" -eq 0 ]; then
    echo "  everything passed"
else
    echo "  SOMETHING FAILED - see above"
fi
exit $FAILED
