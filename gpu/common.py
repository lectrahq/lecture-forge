"""
Shared helpers for the GPU scripts: loading the model, reading manifests and
scoring predictions.

Scoring rule that matters: the reference text and the model's output go through
the SAME clean-up (normalise.py, shared with the website) before they are
compared. Malayalam chillu letters can be written two ways that look identical on
screen, so without this a correct transcription can be counted as wrong.
"""

import difflib
import json
import os
import re
import sys
import time
from collections import defaultdict

from normalise import normalise_text, sanitize_malayalam_orthography

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


def load_model(decoder: str = "rnnt", checkpoint: str | None = None):
    """Load IndicConformer and choose which decoder head to use ('rnnt' or 'ctc')."""
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
    if hasattr(model, "change_decoding_strategy"):
        try:
            model.change_decoding_strategy(decoder_type=decoder)
        except Exception:
            pass
    if hasattr(model, "cur_decoder"):
        model.cur_decoder = decoder


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


def decode_ctc_words(ctc_hyp, tokenizer, language_id: str = "ml", frame_dur: float = 0.04) -> tuple[str, list[dict]]:
    """Decode raw CTC alignments into words with exact acoustic start and end timestamps.

    IndicConformer uses 4x subsampling at 10ms frame stride = 40ms (0.04s) per acoustic frame.
    SentencePiece spaces (' ' / U+2581 or ' ') and blank tokens are parsed to determine precise
    word acoustic boundaries without time drift.
    """
    alignments = getattr(ctc_hyp, "alignments", None)
    if alignments is None:
        text = getattr(ctc_hyp, "text", "")
        return sanitize_malayalam_orthography(text), []

    token_ids = alignments.argmax(dim=-1).tolist()
    blank_id = alignments.shape[-1] - 1

    # CTC greedy collapse
    collapsed = []
    prev_tid = None
    for t, tid in enumerate(token_ids):
        if tid != blank_id and tid != prev_tid:
            collapsed.append((t, tid))
        prev_tid = tid

    words = []
    curr_pieces = []
    curr_start_t = None
    last_t = None

    for t, tid in collapsed:
        tok_str = tokenizer.ids_to_text([tid], lang=language_id)
        is_space_token = tok_str.strip() == ""
        starts_with_space = tok_str.startswith(" ") or tok_str.startswith(" ")

        if (is_space_token or starts_with_space) and curr_pieces:
            word_str = sanitize_malayalam_orthography("".join(curr_pieces))
            if word_str:
                words.append({
                    "text": word_str,
                    "start": round(curr_start_t * frame_dur, 2),
                    "end": round((last_t + 1) * frame_dur, 2),
                })
            curr_pieces = []
            curr_start_t = None

        cleaned = tok_str.replace(" ", "").replace(" ", "").strip()
        if cleaned:
            if curr_start_t is None:
                curr_start_t = t
            curr_pieces.append(cleaned)
            last_t = t

    if curr_pieces:
        word_str = sanitize_malayalam_orthography("".join(curr_pieces))
        if word_str:
            words.append({
                "text": word_str,
                "start": round(curr_start_t * frame_dur, 2),
                "end": round((last_t + 1) * frame_dur, 2),
            })

    full_text = getattr(ctc_hyp, "text", " ".join(w["text"] for w in words)).strip()
    return sanitize_malayalam_orthography(full_text), words


def align_words_to_ctc(rnnt_words: list[str], ctc_words: list[dict], duration: float | None = None) -> list[dict]:
    """Align grammatically clean RNNT words to acoustic CTC word boundaries.

    Uses dynamic programming sequence matching (difflib.SequenceMatcher) to associate
    the autoregressive text recognition (RNNT) with acoustic frame boundaries (CTC),
    preserving monotonic timestamps and preventing karaoke drift.
    """
    if not rnnt_words:
        return []

    # If CTC produced no words at all, fallback to linear spacing
    if not ctc_words:
        total_time = duration if (duration and duration > 0) else (len(rnnt_words) * 0.4)
        step = total_time / len(rnnt_words)
        return [
            {
                "text": w,
                "start": round(i * step, 2),
                "end": round((i + 1) * step, 2),
            }
            for i, w in enumerate(rnnt_words)
        ]

    # Quick path: exact 1-to-1 length match
    if len(rnnt_words) == len(ctc_words):
        aligned = []
        for w, ctc in zip(rnnt_words, ctc_words):
            aligned.append({
                "text": w,
                "start": ctc["start"],
                "end": max(ctc["end"], round(ctc["start"] + 0.08, 2)),
            })
        return aligned

    # Sequence alignment between CTC word texts and RNNT word texts
    ctc_texts = [w["text"] for w in ctc_words]
    matcher = difflib.SequenceMatcher(None, ctc_texts, rnnt_words)
    aligned_words: list[dict | None] = [None] * len(rnnt_words)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for c_idx, r_idx in zip(range(i1, i2), range(j1, j2)):
                aligned_words[r_idx] = {
                    "text": rnnt_words[r_idx],
                    "start": ctc_words[c_idx]["start"],
                    "end": max(ctc_words[c_idx]["end"], round(ctc_words[c_idx]["start"] + 0.08, 2)),
                }
        elif tag == "replace":
            win_start = ctc_words[i1]["start"]
            win_end = ctc_words[i2 - 1]["end"]
            if win_end <= win_start:
                win_end = win_start + 0.4 * (j2 - j1)
            span = (win_end - win_start) / max(1, (j2 - j1))
            for offset, r_idx in enumerate(range(j1, j2)):
                aligned_words[r_idx] = {
                    "text": rnnt_words[r_idx],
                    "start": round(win_start + offset * span, 2),
                    "end": round(win_start + (offset + 1) * span, 2),
                }
        elif tag == "insert":
            prev_end = ctc_words[i1 - 1]["end"] if i1 > 0 else 0.0
            next_start = ctc_words[i1]["start"] if i1 < len(ctc_words) else (prev_end + 0.4 * (j2 - j1))
            if next_start <= prev_end:
                next_start = prev_end + 0.3 * (j2 - j1)
            span = (next_start - prev_end) / max(1, (j2 - j1))
            for offset, r_idx in enumerate(range(j1, j2)):
                aligned_words[r_idx] = {
                    "text": rnnt_words[r_idx],
                    "start": round(prev_end + offset * span, 2),
                    "end": round(prev_end + (offset + 1) * span, 2),
                }
        elif tag == "delete":
            pass

    # Pass 2: Fill any unassigned holes and enforce strict monotonicity
    last_end = 0.0
    final_words: list[dict] = []
    for idx in range(len(aligned_words)):
        item = aligned_words[idx]
        if item is None:
            w_dict = {
                "text": rnnt_words[idx],
                "start": round(last_end, 2),
                "end": round(last_end + 0.3, 2),
            }
        else:
            w_start = item["start"]
            if w_start < last_end:
                w_start = round(last_end, 2)
            w_end = item["end"]
            if w_end <= w_start:
                w_end = round(w_start + 0.08, 2)
            w_dict = {
                "text": rnnt_words[idx],
                "start": w_start,
                "end": w_end,
            }
        last_end = w_dict["end"]
        final_words.append(w_dict)

    return final_words


def transcribe_with_words(model, audio_paths: list[str], batch_size: int = 4,
                          language_id: str | None = None,
                          decoder: str = "rnnt") -> tuple[list[str], list[list[dict]]]:
    """Transcribe clips with hybrid accuracy: RNNT text recognition + CTC acoustic word alignments."""
    import inspect

    lang = language_id or LANGUAGE_ID
    parameters = inspect.signature(model.transcribe).parameters
    options = {"batch_size": batch_size}
    if "language_id" in parameters:
        options["language_id"] = lang
    if "num_workers" in parameters:
        options["num_workers"] = 0

    if decoder == "rnnt":
        # Pass A: RNNT Text Recognition (autoregressive LM generates valid words & grammar)
        set_decoder(model, "rnnt")
        raw_rnnt_texts = transcribe(model, audio_paths, batch_size=batch_size, language_id=lang)
        clean_texts = [sanitize_malayalam_orthography(t) for t in raw_rnnt_texts]

        # Pass B: CTC Acoustic Word Alignment (CTC frame alignments provide millisecond boundaries)
        set_decoder(model, "ctc")
        ctc_options = dict(options)
        ctc_options["return_hypotheses"] = True
        result = model.transcribe(audio_paths, **ctc_options)

        if isinstance(result, (list, tuple)) and len(result) > 1 and isinstance(result[1], list):
            hyps = result[1]
        elif isinstance(result, (list, tuple)):
            hyps = result[0] if isinstance(result[0], list) else result
        else:
            hyps = [result]

        chunk_words = []
        for i, hyp in enumerate(hyps):
            if isinstance(hyp, list):
                hyp = hyp[0]
            _, ctc_words = decode_ctc_words(hyp, model.tokenizer, language_id=lang)
            rnnt_words = clean_texts[i].split() if i < len(clean_texts) else []
            aligned = align_words_to_ctc(rnnt_words, ctc_words)
            chunk_words.append(aligned)

        # Switch decoder back to default rnnt
        set_decoder(model, "rnnt")
        return clean_texts, chunk_words

    else:
        # Fallback pure CTC decoding
        set_decoder(model, "ctc")
        ctc_options = dict(options)
        ctc_options["return_hypotheses"] = True
        result = model.transcribe(audio_paths, **ctc_options)
        if isinstance(result, (list, tuple)) and len(result) > 1 and isinstance(result[1], list):
            hyps = result[1]
        elif isinstance(result, (list, tuple)):
            hyps = result[0] if isinstance(result[0], list) else result
        else:
            hyps = [result]

        texts = []
        chunk_words = []
        for hyp in hyps:
            if isinstance(hyp, list):
                hyp = hyp[0]
            text, words = decode_ctc_words(hyp, model.tokenizer, language_id=lang)
            texts.append(text)
            chunk_words.append(words)
        return texts, chunk_words


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
