#!/usr/bin/env python
"""
Fine-tune IndicConformer on the collected lectures.

    python finetune.py --dataset /workspace/data/<id> --out /workspace/results/<run>

What it does and why:

* Starts from the pretrained Malayalam checkpoint, never from scratch. A few
  hours of audio can adjust a trained model; it cannot build one.
* With a small dataset (under SMALL_DATA_HOURS) it deliberately trains less of
  the network: the lower half of the encoder is frozen and the simpler CTC head
  is given more weight. Those lower layers recognise general speech sounds and
  are the part most easily damaged by a small, one-speaker dataset. The choice
  made is written into run_log.json.
* Keeps SpecAugment on. It hides random patches of the audio during training,
  which is one of the few things that genuinely helps when data is scarce.
* Watches the error rate on the validation lectures after every pass and keeps
  the best version, stopping early if it stops improving. Without this, a small
  dataset is simply memorised.
"""

import argparse
import json
import os
import time

import common

# Below this much training audio, train conservatively (see the note above).
SMALL_DATA_HOURS = 10.0


def absolute_manifest(dataset_dir: str, split: str, out_dir: str) -> tuple[str, list[dict]]:
    """NeMo resolves manifest paths against the working directory, not the manifest,
    so write a copy with absolute paths before training."""
    rows = common.load_manifest(os.path.join(dataset_dir, f"{split}.jsonl"))
    path = os.path.join(out_dir, f"{split}_absolute.jsonl")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            row = dict(row)
            row["audio_filepath"] = os.path.join(dataset_dir, row["audio_filepath"])
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path, rows


def pick_batch_size() -> int:
    """Fit the batch to the card we actually got; pods vary from 16 GB to 80 GB."""
    import torch
    if not torch.cuda.is_available():
        return 2
    memory_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
    # Conformer-large on clips of up to 25 s needs roughly 3 GB per clip with
    # mixed precision, leaving headroom for the optimiser state.
    return max(2, min(32, int(memory_gb // 3)))


def pick_precision() -> str:
    import torch
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return "bf16-mixed"      # wider range than fp16, no loss scaling needed
    if torch.cuda.is_available():
        return "16-mixed"
    return "32-true"


def freeze_lower_encoder(model, fraction: float) -> int:
    """Freeze the first part of the encoder. Returns how many blocks were frozen."""
    layers = getattr(getattr(model, "encoder", None), "layers", None)
    if not layers:
        common.log("could not find encoder layers; nothing frozen")
        return 0
    how_many = int(len(layers) * fraction)
    for layer in list(layers)[:how_many]:
        for parameter in layer.parameters():
            parameter.requires_grad = False
    common.log(f"froze the first {how_many} of {len(layers)} encoder blocks")
    return how_many


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune IndicConformer.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True, help="run folder for checkpoints and logs")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=None, help="default: fitted to the GPU")
    parser.add_argument("--lr", type=float, default=1e-4, help="conservative by design")
    parser.add_argument("--warmup", type=int, default=500)
    parser.add_argument("--patience", type=int, default=5, help="stop after this many epochs with no gain")
    parser.add_argument("--freeze-fraction", type=float, default=None,
                        help="fraction of encoder blocks to freeze (default: 0.5 for small datasets)")
    parser.add_argument("--monitor", default="val_wer", help="metric used to pick the best model")
    args = parser.parse_args()

    import torch
    from omegaconf import OmegaConf, open_dict
    try:
        import lightning.pytorch as pl
    except ImportError:
        import pytorch_lightning as pl
    from nemo.utils.exp_manager import exp_manager

    os.makedirs(args.out, exist_ok=True)
    started = time.time()
    machine = common.describe_machine()
    common.log(f"machine: {machine}")
    if not machine["cuda_available"]:
        common.log("WARNING: no GPU found. Training on CPU is not practical; "
                   "this will be extremely slow. Use it only to check the pipeline runs.")

    train_manifest, train_rows = absolute_manifest(args.dataset, "train", args.out)
    val_manifest, val_rows = absolute_manifest(args.dataset, "val", args.out)
    if not train_rows:
        raise SystemExit("train.jsonl is empty - nothing to train on")
    if not val_rows:
        raise SystemExit("val.jsonl is empty - there would be no way to tell when to stop")

    hours = common.total_hours(train_rows)
    small = hours < SMALL_DATA_HOURS
    freeze_fraction = args.freeze_fraction if args.freeze_fraction is not None else (0.5 if small else 0.0)
    batch_size = args.batch_size or pick_batch_size()
    precision = pick_precision()

    common.log(f"training audio: {hours} h over {len(train_rows)} clips "
               f"({len(val_rows)} validation clips)")
    if small:
        common.log(f"this is UNDER {SMALL_DATA_HOURS} h, so training conservatively: "
                   f"freezing {freeze_fraction:.0%} of the encoder and leaning on the CTC head")

    model = common.load_model("ctc")
    model.train()

    # --- data and optimiser
    with open_dict(model.cfg):
        model.cfg.train_ds.manifest_filepath = train_manifest
        model.cfg.train_ds.batch_size = batch_size
        model.cfg.train_ds.shuffle = True
        model.cfg.train_ds.num_workers = 4
        model.cfg.train_ds.pin_memory = True
        model.cfg.train_ds.max_duration = 25.0
        model.cfg.train_ds.min_duration = 1.0

        model.cfg.validation_ds.manifest_filepath = val_manifest
        model.cfg.validation_ds.batch_size = batch_size
        model.cfg.validation_ds.shuffle = False
        model.cfg.validation_ds.num_workers = 4

        model.cfg.optim.name = "adamw"
        model.cfg.optim.lr = args.lr
        model.cfg.optim.weight_decay = 1e-3
        model.cfg.optim.sched.name = "CosineAnnealing"
        model.cfg.optim.sched.warmup_steps = args.warmup
        model.cfg.optim.sched.min_lr = args.lr / 100

        # Give the simpler CTC head more of the loss when data is scarce: it has
        # far fewer parameters to adapt than the RNNT head.
        if small and "aux_ctc" in model.cfg:
            model.cfg.aux_ctc.ctc_loss_weight = 0.5

    model.setup_training_data(model.cfg.train_ds)
    model.setup_validation_data(model.cfg.validation_ds)
    model.setup_optimization(model.cfg.optim)

    # SpecAugment stays on - it is one of the few things that reliably helps small datasets.
    spec_augment = "on" if getattr(model, "spec_augmentation", None) is not None else "not present"
    common.log(f"SpecAugment: {spec_augment}")

    frozen_blocks = freeze_lower_encoder(model, freeze_fraction) if freeze_fraction > 0 else 0
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    common.log(f"training {trainable/1e6:.1f}M of {total/1e6:.1f}M parameters")

    # --- trainer
    trainer = pl.Trainer(
        devices=1,
        accelerator="gpu" if machine["cuda_available"] else "cpu",
        max_epochs=args.epochs,
        precision=precision,
        log_every_n_steps=10,
        enable_progress_bar=True,
        check_val_every_n_epoch=1,
        gradient_clip_val=1.0,
    )
    model.set_trainer(trainer)

    exp_manager(trainer, {
        "exp_dir": args.out,
        "name": "finetune",
        "create_checkpoint_callback": True,
        "checkpoint_callback_params": {
            "monitor": args.monitor,
            "mode": "min",
            "save_top_k": 1,            # keep the best only; pods have limited disk
            "always_save_nemo": True,
            "save_best_model": True,
        },
        "create_tensorboard_logger": True,
        "create_wandb_logger": False,
        "resume_if_exists": False,
    })

    from pytorch_lightning.callbacks import EarlyStopping  # noqa: E402
    trainer.callbacks.append(EarlyStopping(monitor=args.monitor, mode="min",
                                           patience=args.patience, verbose=True))

    common.log("training starts")
    trainer.fit(model)
    minutes = (time.time() - started) / 60
    common.log(f"training finished in {minutes:.1f} minutes")

    # --- keep the best model where evaluate.py expects it
    best = os.path.join(args.out, "best.nemo")
    model.save_to(best)
    common.log(f"saved {best}")

    common.write_json(os.path.join(args.out, "run_log.json"), {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": machine,
        "dataset": os.path.basename(args.dataset.rstrip("/")),
        "training_hours": hours,
        "training_clips": len(train_rows),
        "validation_clips": len(val_rows),
        "small_dataset": small,
        "small_dataset_threshold_hours": SMALL_DATA_HOURS,
        "decisions": {
            "frozen_encoder_blocks": frozen_blocks,
            "freeze_fraction": freeze_fraction,
            "ctc_loss_weight": float(model.cfg.get("aux_ctc", {}).get("ctc_loss_weight", 0))
                                if "aux_ctc" in model.cfg else None,
            "why": (f"Only {hours} h of training audio, under the {SMALL_DATA_HOURS} h "
                    "threshold, so the lower encoder blocks were frozen and the CTC head "
                    "was weighted more heavily to avoid damaging general speech features."
                    if small else
                    f"{hours} h of training audio, so the whole encoder was trained."),
        },
        "hyperparameters": {
            "epochs_max": args.epochs, "batch_size": batch_size, "learning_rate": args.lr,
            "warmup_steps": args.warmup, "precision": precision,
            "early_stopping_patience": args.patience, "monitor": args.monitor,
            "spec_augment": spec_augment,
        },
        "trainable_parameters": trainable,
        "total_parameters": total,
        "training_minutes": round(minutes, 1),
        "best_checkpoint": best,
    })


if __name__ == "__main__":
    main()
