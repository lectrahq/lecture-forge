#!/usr/bin/env bash
# One command for a whole GPU run: fetch the data, measure the pretrained model,
# fine-tune it, measure it again, send everything back.
#
#   export VPS_HOST=your.vps.host VPS_USER=gpupull VPS_PORT=your-ssh-port
#   export DATASET_ID=2026-11-02_1130
#   export HF_TOKEN=...            # the checkpoint is gated on Hugging Face
#   ./run_all.sh
#
# Stops at the first failure, so the pod can be destroyed without wondering
# whether a later step quietly produced nonsense.
set -euo pipefail

: "${DATASET_ID:?set DATASET_ID}"
RUN_ID="${RUN_ID:-$(date +%Y-%m-%d_%H%M)_${DATASET_ID}}"
DATA_ROOT="${DATA_ROOT:-/workspace/data}"
RESULTS_ROOT="${RESULTS_ROOT:-/workspace/results}"
DATASET_DIR="$DATA_ROOT/$DATASET_ID"
RUN_DIR="$RESULTS_ROOT/$RUN_ID"
BATCH_SIZE="${BATCH_SIZE:-8}"
EPOCHS="${EPOCHS:-20}"

export RUN_ID RESULTS_ROOT DATA_ROOT

mkdir -p "$RUN_DIR/logs"
# Everything printed from here on is also kept in the run folder.
exec > >(tee -a "$RUN_DIR/logs/run_all.log") 2>&1

echo "=============================================="
echo " run      : $RUN_ID"
echo " dataset  : $DATASET_ID"
echo " started  : $(date -Is)"
echo "=============================================="
nvidia-smi || echo "(no GPU visible - fine for a dry run, not for training)"

echo
echo "### 1/4  fetching the dataset"
/app/pull_data.sh

if [ ! -f "$DATASET_DIR/train.jsonl" ]; then
    echo "train.jsonl missing in $DATASET_DIR" >&2
    exit 1
fi
python - "$DATASET_DIR" <<'EOF'
import json, sys, os
info = os.path.join(sys.argv[1], "dataset_info.json")
if os.path.isfile(info):
    data = json.load(open(info, encoding="utf-8"))
    if not data.get("usable_for_training", True):
        print("REFUSING: this dataset is marked not usable for training "
              "(a split is empty). Collect more lectures and export again.")
        sys.exit(1)
    print(f"dataset ok: {data['total_hours']} h, {data['total_clips']} clips, "
          f"human-verified test refs: {data.get('human_verified_test_references')}")
EOF

echo
echo "### 2/4  baseline (pretrained model, nothing trained yet)"
python /app/baseline.py --dataset "$DATASET_DIR" --out "$RUN_DIR/baseline" \
    --splits val test --batch-size "$BATCH_SIZE"

echo
echo "### 3/4  fine-tuning"
python /app/finetune.py --dataset "$DATASET_DIR" --out "$RUN_DIR" \
    --epochs "$EPOCHS" ${BATCH_SIZE:+--batch-size "$BATCH_SIZE"}

echo
echo "### 4/4  scoring the fine-tuned model"
python /app/evaluate.py --dataset "$DATASET_DIR" --checkpoint "$RUN_DIR/best.nemo" \
    --out "$RUN_DIR/finetuned" --splits test --batch-size "$BATCH_SIZE"

echo
echo "### sending results back to the VPS"
/app/push_results.sh

echo
echo "=============================================="
echo " finished : $(date -Is)"
echo " results  : $RUN_DIR  (also copied to the VPS)"
echo " You can destroy this pod now."
echo "=============================================="
