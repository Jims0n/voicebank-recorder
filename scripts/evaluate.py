#!/usr/bin/env python3
"""
Evaluate a Whisper model on one dataset split and write a results file.

    venv/bin/python scripts/evaluate.py dataset/test.jsonl --model large-v3 --label baseline-large-v3

Outputs, in --out (default results/):
    <label>_<split>.json         run settings, metrics overall and by group, every clip
    <label>_<split>_clips.txt    aligned reference/hypothesis pairs, worst first
    <label>_<split>.hyps.jsonl   raw hypotheses. Reruns with the same model and decoding
                                 settings reuse them, so changing the scoring (or the
                                 amount parser) rescores without decoding again.

Metrics
  WER         Corpus WER over all clips (errors / reference words), split into
              substitutions, deletions and insertions. Both sides go through
              nlu.normalise first, so Whisper writing "5,000" for "five thousand" is
              not counted as an error; "5k" for "five thousand" still is.
  amount      Spoken amount parsed (nlu.amounts) from reference and hypothesis and
              compared as integers. Scored only where entities has an amount, the
              clip is usable_for_intent, and the reference actually contains an
              amount: confirm/cancel replies ("yes", "make you stop") carry the
              prompt's amount in entities but nobody said it, so there is nothing
              for the ASR to get right. "no amount" counts hypotheses with no
              parsable amount at all.
  recipient   entities.recipient found as a word in the hypothesis. Scored under the
              same rules: present in entities, usable_for_intent, and said in the
              reference.
Every metric is reported overall and by spoken_language, gender, first_language and
speaker, with its n.

Models: anything faster-whisper loads, i.e. a size name ("large-v3") or a
CTranslate2 model directory. Convert a fine-tuned Hugging Face checkpoint first:
    ct2-transformers-converter --model <checkpoint> --output_dir <checkpoint>-ct2 \\
        --copy_files tokenizer.json preprocessor_config.json --quantization float16
Compare runs with the same --compute-type (default float32); quantisation alone moves WER.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
import wave
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nlu.amounts import parse_amount  # noqa: E402
from nlu.normalise import normalise_transcript  # noqa: E402

BREAKDOWNS = ("spoken_language", "gender", "first_language", "speaker")


# ---------------------------------------------------------------- alignment / WER

def align(ref, hyp):
    """Levenshtein alignment of two word lists. Returns [(op, ref_word, hyp_word)]
    with op in "=", "S", "D", "I"; the missing side of D/I is None."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]),
                          d[i - 1][j] + 1, d[i][j - 1] + 1)
    ops, i, j = [], n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            ops.append(("=" if ref[i - 1] == hyp[j - 1] else "S", ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            ops.append(("D", ref[i - 1], None))
            i -= 1
        else:
            ops.append(("I", None, hyp[j - 1]))
            j -= 1
    return ops[::-1]


def error_counts(ops):
    return {k: sum(op == c for op, _, _ in ops) for k, c in
            (("sub", "S"), ("del", "D"), ("ins", "I"))}


# ---------------------------------------------------------------- clip scoring

def _words(text):
    return [re.sub(r"'s$", "", w) for w in text.split()]


def score_amount(row, ref, hyp):
    entity = (row.get("entities") or {}).get("amount")
    out = {"entity": entity, "reference": None, "hypothesis": None, "scored": False,
           "correct": None, "missing": None, "skip": None}
    if entity is None:
        out["skip"] = "no amount in entities"
        return out
    out["reference"], out["hypothesis"] = parse_amount(ref), parse_amount(hyp)
    if not row.get("usable_for_intent", True):
        out["skip"] = "instruction read"
    elif out["reference"] is None:
        out["skip"] = "amount not spoken"
    else:
        out.update(scored=True, correct=out["hypothesis"] == out["reference"],
                   missing=out["hypothesis"] is None)
    return out


def score_recipient(row, ref, hyp):
    name = (row.get("entities") or {}).get("recipient")
    out = {"entity": name, "scored": False, "correct": None, "skip": None}
    if not name:
        out["skip"] = "no recipient in entities"
        return out
    name = name.lower()
    if not row.get("usable_for_intent", True):
        out["skip"] = "instruction read"
    elif name not in _words(ref):
        out["skip"] = "recipient not spoken"
    else:
        out.update(scored=True, correct=name in _words(hyp))
    return out


def score_clip(row, raw_hypothesis):
    ref = normalise_transcript(row.get("text", ""))
    hyp = normalise_transcript(raw_hypothesis)
    ops = align(ref.split(), hyp.split())
    counts = error_counts(ops)
    n_ref = len(ref.split())
    return {
        "audio": row["audio"], "speaker": row.get("speaker"), "gender": row.get("gender"),
        "first_language": row.get("first_language"),
        "spoken_language": row.get("spoken_language") or "unknown",
        "intent": row.get("intent"), "mode": row.get("mode"),
        "instruction_read": row.get("instruction_read", False),
        "usable_for_intent": row.get("usable_for_intent", True),
        "reference": ref, "hypothesis": hyp, "hypothesis_raw": raw_hypothesis,
        "ref_words": n_ref, **counts,
        "wer": (sum(counts.values()) / n_ref) if n_ref else None,
        "alignment": ops,
        "amount": score_amount(row, ref, hyp),
        "recipient": score_recipient(row, ref, hyp),
    }


# ---------------------------------------------------------------- aggregation

def aggregate(clips):
    words = sum(c["ref_words"] for c in clips)
    sub, dele, ins = (sum(c[k] for c in clips) for k in ("sub", "del", "ins"))
    am = [c["amount"] for c in clips if c["amount"]["scored"]]
    rc = [c["recipient"] for c in clips if c["recipient"]["scored"]]
    rate = lambda k, n: (k / n) if n else None  # noqa: E731
    return {
        "clips": len(clips), "speakers": len({c["speaker"] for c in clips}),
        "ref_words": words, "sub": sub, "del": dele, "ins": ins,
        "wer": rate(sub + dele + ins, words), "sub_rate": rate(sub, words),
        "del_rate": rate(dele, words), "ins_rate": rate(ins, words),
        "amount": {"n": len(am), "correct": sum(a["correct"] for a in am),
                   "missing": sum(a["missing"] for a in am),
                   "accuracy": rate(sum(a["correct"] for a in am), len(am)),
                   "missing_rate": rate(sum(a["missing"] for a in am), len(am))},
        "recipient": {"n": len(rc), "correct": sum(r["correct"] for r in rc),
                      "accuracy": rate(sum(r["correct"] for r in rc), len(rc))},
    }


def breakdown(clips):
    out = {"overall": aggregate(clips)}
    for field in BREAKDOWNS:
        groups = defaultdict(list)
        for c in clips:
            groups[str(c[field])].append(c)
        out[field] = {k: aggregate(v) for k, v in sorted(groups.items())}
    return out


def skip_counts(clips, key):
    counts = defaultdict(int)
    for c in clips:
        if not c[key]["scored"] and c[key]["skip"]:
            counts[c[key]["skip"]] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------- report

def _pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def _frac(k, n):
    return f"{_pct(k / n if n else None)} ({k}/{n})" if n else "— (0)"


def table(results, speakers_meta):
    lines = []
    head = (f"{'':<26}{'clips':>6}{'words':>7}{'WER':>8}{'sub':>7}{'del':>7}{'ins':>7}"
            f"   {'amount exact':<18}{'no amount':<16}{'recipient':<16}")
    lines.append(head)
    lines.append("-" * len(head))

    def row(label, a):
        am, rc = a["amount"], a["recipient"]
        lines.append(
            f"{label:<26}{a['clips']:>6}{a['ref_words']:>7}{_pct(a['wer']):>8}"
            f"{_pct(a['sub_rate']):>7}{_pct(a['del_rate']):>7}{_pct(a['ins_rate']):>7}"
            f"   {_frac(am['correct'], am['n']):<18}{_frac(am['missing'], am['n']):<16}"
            f"{_frac(rc['correct'], rc['n']):<16}")

    row("OVERALL", results["overall"])
    for field in BREAKDOWNS:
        lines.append(f"\nby {field.replace('_', ' ')}")
        for key, a in results[field].items():
            label = key
            if field == "speaker" and key in speakers_meta:
                g, l1 = speakers_meta[key]
                label = f"{key} ({(g or '?')[0].upper()}, {l1})"
            row(f"  {label}", a)
    return "\n".join(lines)


def clip_dump(clips):
    """Readable alignments, sclite style: errors in CAPS, *** for a missing word."""
    def bad(c):
        return (c["wer"] or 0, c["amount"]["scored"] and not c["amount"]["correct"],
                c["recipient"]["scored"] and not c["recipient"]["correct"])

    out = []
    for i, c in enumerate(sorted(clips, key=bad, reverse=True), 1):
        r_line, h_line = [], []
        for op, r, h in c["alignment"]:
            r = "***" if r is None else (r if op == "=" else r.upper())
            h = "***" if h is None else (h if op == "=" else h.upper())
            w = max(len(r), len(h))
            r_line.append(r.ljust(w))
            h_line.append(h.ljust(w))
        am, rc = c["amount"], c["recipient"]
        notes = []
        if am["scored"]:
            verdict = "ok" if am["correct"] else ("NO AMOUNT" if am["missing"] else "WRONG")
            notes.append(f"amount ref {am['reference']} hyp {am['hypothesis']} {verdict}")
        elif am["entity"] is not None:
            notes.append(f"amount not scored ({am['skip']})")
        if rc["scored"]:
            notes.append(f"recipient {rc['entity']} {'ok' if rc['correct'] else 'MISSED'}")
        wer = "—" if c["wer"] is None else f"{100 * c['wer']:.0f}%"
        out.append(
            f"#{i}  {c['speaker']}  {c['gender']}/{c['first_language']}  "
            f"spoken={c['spoken_language']}  intent={c['intent']}"
            f"{'  INSTRUCTION READ' if c['instruction_read'] else ''}\n"
            f"    WER {wer}  (S{c['sub']} D{c['del']} I{c['ins']} / {c['ref_words']} words)"
            f"   {c['audio']}\n"
            f"    REF: {' '.join(r_line).rstrip()}\n"
            f"    HYP: {' '.join(h_line).rstrip()}\n"
            + (f"    {' · '.join(notes)}\n" if notes else ""))
    return "\n".join(out)


# ---------------------------------------------------------------- decoding

def load_audio(path):
    """16 kHz mono float32. The exported wavs are already 16 kHz mono s16; anything
    else goes through faster-whisper's own decoder."""
    import numpy as np
    with wave.open(str(path)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2):
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    from faster_whisper import decode_audio
    return decode_audio(str(path), sampling_rate=16000)


def load_model(name, device, compute_type):
    p = Path(name)
    if p.is_dir() and (p / "config.json").exists() and not (p / "model.bin").exists():
        sys.exit(f"{name} looks like a Hugging Face checkpoint; convert it for faster-whisper:\n"
                 f"  ct2-transformers-converter --model {name} --output_dir {name.rstrip('/')}-ct2 "
                 "--copy_files tokenizer.json preprocessor_config.json --quantization float16")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("pip install faster-whisper")
    return WhisperModel(name, device=device, compute_type=compute_type)


def decode_settings(a):
    # Deterministic decoding: one temperature, no conditioning on earlier text, no prompt.
    return {"model": a.model, "language": a.language, "beam_size": a.beam_size,
            "temperature": 0.0, "condition_on_previous_text": False,
            "without_timestamps": True, "vad_filter": False, "initial_prompt": None,
            "device": a.device, "compute_type": a.compute_type}


def transcribe_all(rows, base, settings, cache_path, redecode, log=print):
    key = json.dumps(settings, sort_keys=True)
    cached = {}
    if cache_path.exists() and not redecode:
        for line in cache_path.read_text().splitlines():
            rec = json.loads(line)
            if rec["settings"] == key:
                cached[rec["audio"]] = rec["hypothesis"]
    todo = [r for r in rows if r["audio"] not in cached]
    if todo:
        log(f"decoding {len(todo)} clips ({len(rows) - len(todo)} cached) with {settings['model']}")
        model = load_model(settings["model"], settings["device"], settings["compute_type"])
        opts = {k: settings[k] for k in ("language", "beam_size", "temperature",
                                         "condition_on_previous_text", "without_timestamps",
                                         "vad_filter", "initial_prompt")}
        # Rewrite the cache with only current-setting rows, then append as we go so an
        # interrupted run resumes where it stopped.
        cache_path.write_text("".join(
            json.dumps({"audio": a, "settings": key, "hypothesis": h}) + "\n"
            for a, h in cached.items()))
        start = time.time()
        with cache_path.open("a") as f:
            for n, r in enumerate(todo, 1):
                segments, _info = model.transcribe(load_audio(base / r["audio"]), **opts)
                hyp = " ".join(s.text.strip() for s in segments).strip()
                cached[r["audio"]] = hyp
                f.write(json.dumps({"audio": r["audio"], "settings": key, "hypothesis": hyp}) + "\n")
                f.flush()
                eta = (time.time() - start) / n * (len(todo) - n)
                log(f"  [{n}/{len(todo)}] {r['speaker']}  {hyp[:70]}   (eta {eta:.0f}s)")
    else:
        log(f"all {len(rows)} hypotheses cached; rescoring only (--redecode to decode again)")
    return cached


# ---------------------------------------------------------------- main

def git_commit():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("manifest", type=Path, help="a split manifest, e.g. dataset/test.jsonl")
    p.add_argument("--model", required=True,
                   help='faster-whisper model: size name ("large-v3") or CTranslate2 directory')
    p.add_argument("--label", required=True,
                   help="name for this run, used in output filenames (e.g. baseline-large-v3)")
    p.add_argument("--out", type=Path, default=ROOT / "results")
    p.add_argument("--language", default="en",
                   help="Whisper has no Pidgin code; fixing it stops language-ID drift (default en)")
    p.add_argument("--beam-size", type=int, default=5)
    p.add_argument("--device", default="auto")
    p.add_argument("--compute-type", default="float32",
                   help="float32 runs everywhere and keeps CPU and GPU runs comparable; "
                   "int8 is faster on CPU but moves WER")
    p.add_argument("--limit", type=int, help="only the first N clips (smoke test)")
    p.add_argument("--redecode", action="store_true", help="ignore cached hypotheses")
    a = p.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", a.label):
        p.error("--label may only contain letters, digits, '.', '_' and '-'")
    return a


def run(a, transcribe=transcribe_all, log=print):
    manifest = a.manifest.resolve()
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    if a.limit:
        rows = rows[:a.limit]
    split = manifest.stem
    a.out.mkdir(parents=True, exist_ok=True)
    stem = a.out / f"{a.label}_{split}"
    settings = decode_settings(a)

    hyps = transcribe(rows, manifest.parent, settings, stem.with_suffix(".hyps.jsonl"),
                      a.redecode, log=log)
    clips = [score_clip(r, hyps[r["audio"]]) for r in rows]
    results = breakdown(clips)
    speakers_meta = {r["speaker"]: (r.get("gender"), r.get("first_language")) for r in rows}

    payload = {
        "run": {"label": a.label, "split": split, "manifest": str(manifest),
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "limit": a.limit, "decode": settings, "git_commit": git_commit(),
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
        "not_scored": {"amount": skip_counts(clips, "amount"),
                       "recipient": skip_counts(clips, "recipient")},
        "results": results,
        "clips": clips,
    }
    json_path = stem.with_suffix(".json")
    json_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    clips_path = a.out / f"{a.label}_{split}_clips.txt"
    clips_path.write_text(clip_dump(clips), encoding="utf-8")

    log(f"\n{a.label} · {settings['model']} · {split} ({len(clips)} clips"
        f"{', limited' if a.limit else ''})\n")
    log(table(results, speakers_meta))
    log("\nnot scored  amount: " + (", ".join(f"{k} {v}" for k, v in payload["not_scored"]["amount"].items()) or "—")
        + "\n            recipient: "
        + (", ".join(f"{k} {v}" for k, v in payload["not_scored"]["recipient"].items()) or "—"))
    log(f"\nwrote {json_path}\n      {clips_path}")
    return payload


def main(argv=None):
    run(parse_args(argv))


if __name__ == "__main__":
    main()
