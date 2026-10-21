#!/usr/bin/env bash
# Copy a dataset from the VPS onto this GPU machine.
#
#   VPS_HOST=your.vps.host VPS_USER=gpupull VPS_PORT=your-ssh-port \
#   DATASET_ID=2026-11-02_1130 ./pull_data.sh
#
# The SSH key is mounted into the pod; it is never baked into the image.
set -euo pipefail

: "${VPS_HOST:?set VPS_HOST to the VPS hostname}"
: "${VPS_USER:?set VPS_USER, e.g. gpupull}"
: "${DATASET_ID:?set DATASET_ID, the dataset folder name on the VPS}"

VPS_PORT="${VPS_PORT:-22}"
VPS_DATA="${VPS_DATA:-/opt/docker/lecture-transcriber/data}"
SSH_KEY="${SSH_KEY:-/workspace/ssh/id_gpu}"
DEST="${DATA_ROOT:-/workspace/data}"

if [ ! -f "$SSH_KEY" ]; then
    echo "No SSH key at $SSH_KEY. Mount it into the pod (see the README, Part 2)." >&2
    exit 1
fi
chmod 600 "$SSH_KEY"
mkdir -p "$DEST" "$(dirname "$SSH_KEY")"

echo "Pulling dataset '$DATASET_ID' from $VPS_USER@$VPS_HOST:$VPS_PORT ..."
rsync -a --info=progress2 \
    -e "ssh -i $SSH_KEY -p $VPS_PORT -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$(dirname "$SSH_KEY")/known_hosts" \
    "$VPS_USER@$VPS_HOST:$VPS_DATA/datasets/$DATASET_ID/" "$DEST/$DATASET_ID/"

echo
echo "Dataset is at $DEST/$DATASET_ID"
ls -la "$DEST/$DATASET_ID"
if [ -f "$DEST/$DATASET_ID/dataset_info.json" ]; then
    echo
    echo "--- dataset_info.json"
    cat "$DEST/$DATASET_ID/dataset_info.json"
fi
