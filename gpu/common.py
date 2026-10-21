"""
Shared helpers for the GPU scripts: loading the model, reading manifests and
scoring predictions.

Scoring rule that matters: the reference text and the model's output go through
the SAME clean-up (normalise.py, shared with the website) before they are
compared. Malayalam chillu letters can be written two ways that look identical on
screen, so without this a correct transcription can be counted as wrong.
"""

import json
import os
import re
import sys
import time
from collections import defaultdict

from normalise import normalise_text

# A clip is named <job_id>_0007.wav, so the job (lecture) is the part before the
# final _NNNN. Scores are reported per lecture as well as overall.
CLIP_NAME = re.compile(r"^(.*)_(\d{4})$")

DEFAULT_REPO = "ai4bharat/indicconformer_stt_ml_hybrid_ctc_rnnt_large"
DEFAULT_FILE = "indicconformer_stt_ml_hybrid_rnnt_large.nemo"

# Which language the model should decode as. 'ml' is Malayalam.
LANGUAGE_ID = os.environ.get("LANGUAGE_ID", "ml")


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# --- manifests -----------------------------------------------------------

def load_manifest(path: str) -> list[dict]:
    """Read a NeMo .jsonl manifest. Returns [] when the file is missing or empty."""
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def absolute_audio(rows: list[dict], dataset_dir: str) -> list[str]:
    """Manifest paths are relative to the dataset folder, so the folder can be moved."""
    return [os.path.join(dataset_dir, r["audio_filepath"]) for r in rows]


def job_of(audio_filepath: str) -> str:
    stem = os.path.splitext(os.path.basename(audio_filepath))[0]
    match = CLIP_NAME.match(stem)
    return match.group(1) if match else stem


def total_hours(rows: list[dict]) -> float:
    return round(sum(float(r.get("duration") or 0) for r in rows) / 3600, 3)


# --- scoring -------------------------------------------------------------

def score(references: list[str], predictions: list[str]) -> dict:
    """Word and character error rate over a set of clips.

    Both sides are cleaned up first. Clips whose reference is empty after
    clean-up are skipped: there is nothing to be right or wrong about, and they
    would make the rate undefined.
    """
    import jiwer

    pairs = [(normalise_text(r), normalise_text(p)) for r, p in zip(references, predictions)]
    pairs = [(r, p) for r, p in pairs if r]
    if not pairs:
        return {"wer": None, "cer": None, "clips": 0, "note": "no non-empty references"}
    refs = [r for r, _ in pairs]
    hyps = [p for _, p in pairs]
    return {
        "wer": round(float(jiwer.wer(refs, hyps)), 4),
        "cer": round(float(jiwer.cer(refs, hyps)), 4),
        "clips": len(pairs),
    }


def score_everything(rows: list[dict], predictions: list[str]) -> dict:
    """Overall score plus a score for each lecture."""
    references = [r.get("text", "") for r in rows]
    result = {"overall": score(references, predictions), "hours": total_hours(rows),
              "per_lecture": {}}

    grouped = defaultdict(lambda: ([], []))
    for row, prediction in zip(rows, predictions):
        refs, hyps = grouped[job_of(row["audio_filepath"])]
        refs.append(row.get("text", ""))
        hyps.append(prediction)
    for job_id, (refs, hyps) in sorted(grouped.items()):
        result["per_lecture"][job_id] = score(refs, hyps)
    return result


def predictions_record(rows: list[dict], predictions: list[str]) -> list[dict]:
    """Per-clip detail, so the website can show the three transcripts side by side."""
    out = []
    for row, prediction in zip(rows, predictions):
        reference = row.get("text", "")
        out.append({
            "audio_filepath": row["audio_filepath"],
            "job_id": job_of(row["audio_filepath"]),
            "duration": row.get("duration"),
            "reference": reference,
            "prediction": prediction,
            "scores": score([reference], [prediction]),
        })
    return out


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log(f"wrote {path}")


# --- the model -----------------------------------------------------------

def torch_device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def describe_machine() -> dict:
    import torch
    info = {"torch": torch.__version__, "cuda_available": torch.cuda.is_available()}
    if torch.cuda.is_available():
        properties = torch.cuda.get_device_properties(0)
        info.update({"gpu": properties.name,
                     "gpu_memory_gb": round(properties.total_memory / 1e9, 1),
                     "gpu_count": torch.cuda.device_count(),
                     "bf16": bool(torch.cuda.is_bf16_supported())})
    return info


def checkpoint_path() -> str:
    """Local path of the .nemo checkpoint, downloading it from Hugging Face if needed.

    The checkpoint is gated, so HF_TOKEN must be set in the environment. The token
    is never printed or written anywhere.
    """
    local = os.environ.get("NEMO_MODEL", "").strip()
    if local:
        if not os.path.isfile(local):
            raise SystemExit(f"NEMO_MODEL is set but '{local}' does not exist")
        return local

    from huggingface_hub import hf_hub_download
    repo = os.environ.get("HF_REPO", DEFAULT_REPO)
    filename = os.environ.get("HF_FILE", DEFAULT_FILE)
    token = os.environ.get("HF_TOKEN") or None
    log(f"downloading {filename} from {repo} (token {'set' if token else 'NOT set'})")
    try:
        return hf_hub_download(repo_id=repo, filename=filename, token=token)
    except Exception as e:
        hint = ""
        message = str(e)
        if "not in the authorized list" in message or "awaiting" in message:
            hint = (f"\n  -> Open https://huggingface.co/{repo} while signed in and click "
                    "'Agree and access repository'. Approval is automatic.")
        elif "401" in message or "Invalid" in message:
            hint = "\n  -> HF_TOKEN looks wrong. Create a Read token and try again."
        raise SystemExit(
            f"Could not download the checkpoint ({type(e).__name__}).\n"
            f"  {message.splitlines()[0] if message else ''}{hint}"
        ) from None


def load_model(decoder: str = "ctc", checkpoint: str | None = None):
    """Load IndicConformer and choose which decoder head to use ('ctc' or 'rnnt')."""
    import nemo.collections.asr as nemo_asr

    device = torch_device()
    path = checkpoint or checkpoint_path()
    try:
        model = nemo_asr.models.ASRModel.restore_from(path, map_location=device)
    except TypeError:
        # ASRModel is an abstract base class. Normally NeMo reads the concrete class
        # name out of the checkpoint's own config, but when that lookup fails it
        # falls back to building the base class and dies with "Can't instantiate
        # abstract class ASRModel". This checkpoint is a hybrid RNNT-CTC BPE model
        # (NeMo logs "EncDecHybridRNNTCTCBPEModel" when it loads), so name it.
        from nemo.collections.asr.models import EncDecHybridRNNTCTCBPEModel
        log("checkpoint config did not name its class; loading it as a hybrid RNNT-CTC model")
        model = EncDecHybridRNNTCTCBPEModel.restore_from(path, map_location=device)
    model.eval()
    set_decoder(model, decoder)
    log(f"model loaded on {device}, decoder={decoder}")
    return model


def set_decoder(model, decoder: str) -> None:
    """Hybrid CTC-RNNT models carry two decoders; pick one before transcribing."""
    if hasattr(model, "cur_decoder"):
        model.cur_decoder = decoder
    elif hasattr(model, "change_decoding_strategy"):
        model.change_decoding_strategy(decoder_type=decoder)


def transcribe(model, audio_paths: list[str], batch_size: int = 8,
               language_id: str | None = None) -> list[str]:
    """Transcribe a list of clips, returning plain strings.

    IndicConformer is one multilingual model whose output layer is masked down to
    a single language, so it must be told which language to use; without it the
    mask lookup fails with KeyError: None. That language_id argument exists only
    in AI4Bharat's fork of NeMo, which is why the fork is required.

    NeMo versions differ in what transcribe() returns (strings, hypothesis
    objects, or a (best, all) pair), so the result is unwrapped defensively.
    """
    import inspect

    parameters = inspect.signature(model.transcribe).parameters
    options = {"batch_size": batch_size}
    if "language_id" in parameters:
        options["language_id"] = language_id or LANGUAGE_ID
    if "num_workers" in parameters:
        # Load audio in this process rather than in worker processes. Workers
        # communicate through shared memory, which is small inside a container and
        # buys nothing here: the work is the model, not reading the files.
        options["num_workers"] = 0
    result = model.transcribe(audio_paths, **options)
    if isinstance(result, tuple):
        result = result[0]
    out = []
    for item in result:
        if isinstance(item, str):
            out.append(item)
        else:
            out.append(getattr(item, "text", str(item)))
    return out


def evaluate_model(dataset_dir: str, splits: list[str], out_dir: str, label: str,
                   checkpoint: str | None = None, decoders: tuple[str, ...] = ("ctc", "rnnt"),
                   batch_size: int = 8, limit: int | None = None) -> dict:
    """Transcribe the given splits with each decoder and write predictions + metrics.

    baseline.py and evaluate.py both call this, so the two runs are directly
    comparable: same clips, same clean-up, same file format.
    """
    predictions_out: dict = {}
    metrics_out: dict = {}
    model = None

    for decoder in decoders:
        if model is None:
            model = load_model(decoder, checkpoint)
        else:
            set_decoder(model, decoder)

        for split in splits:
            rows = load_manifest(os.path.join(dataset_dir, f"{split}.jsonl"))
            if limit:
                rows = rows[:limit]
            if not rows:
                log(f"{label}/{decoder}/{split}: no clips, skipping")
                continue

            log(f"{label}/{decoder}/{split}: transcribing {len(rows)} clips "
                f"({total_hours(rows)} h, language={LANGUAGE_ID}) ...")
            started = time.time()
            predictions = transcribe(model, absolute_audio(rows, dataset_dir), batch_size)
            took = time.time() - started

            predictions_out.setdefault(split, {})[decoder] = predictions_record(rows, predictions)
            measured = score_everything(rows, predictions)
            measured["seconds_taken"] = round(took, 1)
            metrics_out.setdefault(split, {})[decoder] = measured
            overall = measured["overall"]
            log(f"  WER {overall['wer']}  CER {overall['cer']}  "
                f"over {overall['clips']} clips in {took:.0f}s")

    write_json(os.path.join(out_dir, "predictions.json"), predictions_out)
    write_json(os.path.join(out_dir, "metrics.json"), {
        "label": label,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": describe_machine(),
        "checkpoint": checkpoint or os.environ.get("NEMO_MODEL") or DEFAULT_REPO,
        "decoders": list(decoders),
        "splits": metrics_out,
    })
    return metrics_out
