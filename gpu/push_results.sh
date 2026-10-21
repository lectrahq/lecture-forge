#!/usr/bin/env bash
# Send one run's results back to the VPS, where the website reads them.
#
#   VPS_HOST=... VPS_USER=... RUN_ID=2026-11-02_run1 ./push_results.sh
#
# Sends predictions, metrics, logs and the best checkpoint. The checkpoint is
# large (about 500 MB), so skip it with KEEP_CHECKPOINT=0 if the VPS is short of
# space.
set -euo pipefail

: "${VPS_HOST:?set VPS_HOST}"
: "${VPS_USER:?set VPS_USER}"
: "${RUN_ID:?set RUN_ID}"

VPS_PORT="${VPS_PORT:-22}"
VPS_DATA="${VPS_DATA:-/opt/docker/lecture-transcriber/data}"
SSH_KEY="${SSH_KEY:-/workspace/ssh/id_gpu}"
RESULTS="${RESULTS_ROOT:-/workspace/results}/$RUN_ID"
KEEP_CHECKPOINT="${KEEP_CHECKPOINT:-1}"

[ -d "$RESULTS" ] || { echo "No results folder at $RESULTS" >&2; exit 1; }
chmod 600 "$SSH_KEY"

EXCLUDES=(--exclude '*_absolute.jsonl' --exclude 'checkpoints/*.ckpt')
if [ "$KEEP_CHECKPOINT" != "1" ]; then
    EXCLUDES+=(--exclude '*.nemo')
    echo "Not sending the checkpoint (KEEP_CHECKPOINT=0)."
fi

SSH_CMD="ssh -i $SSH_KEY -p $VPS_PORT -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$(dirname "$SSH_KEY")/known_hosts"
$SSH_CMD "$VPS_USER@$VPS_HOST" "mkdir -p '$VPS_DATA/results/$RUN_ID'"

echo "Sending results for run '$RUN_ID' to $VPS_USER@$VPS_HOST ..."
rsync -a --info=progress2 "${EXCLUDES[@]}" -e "$SSH_CMD" \
    "$RESULTS/" "$VPS_USER@$VPS_HOST:$VPS_DATA/results/$RUN_ID/"

echo
echo "Done. The website reads it from data/results/$RUN_ID."
