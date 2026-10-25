"""
Export recordings to a training-ready dataset:
  out/
    audio/<speaker>/<recording_id>.wav   16 kHz, mono, 16-bit PCM (what Whisper expects)
    train.jsonl / dev.jsonl / test.jsonl
    split_report.json                    what each split contains (see the printed summary)
    conversions.json                     how each wav was produced, so reruns can reuse it
Splits are BY SPEAKER, never by clip. If the same voice appears in train and test,
your WER is optimistic and an examiner can rightly call it out.

Loudness: peaks range from 0.02 to 0.99 across phones and rooms, so each clip is
normalised to a common integrated loudness (two-pass EBU R128 loudnorm, linear gain
where possible). --no-normalise exports raw levels for comparison; use a separate
output directory for it.

Reruns reuse a wav only if it was produced with the current conversion settings;
--force reconverts everything regardless.

Requires ffmpeg on PATH (not needed for --dry-run).
"""
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from collector.language import detect_spoken_language
from collector.models import DRAFT_MARK, INSTRUCTION_READ, Recording, Speaker

DEV_SHARE, TEST_SHARE = 0.1, 0.2
SPLITS = ("train", "dev", "test")
# EBU R128 broadcast is -23 LUFS; speech corpora usually sit a little hotter.
LOUDNESS_TARGET, TRUE_PEAK, LOUDNESS_RANGE = -20.0, -1.5, 11.0
# Below the R128 absolute gate the clip is effectively silence; gain would only lift noise.
SILENCE_LUFS = -70.0
CONVERSIONS = "conversions.json"
# Fixed order, so a rerun that reuses wavs writes byte-identical manifests.
CONVERSION_FIELDS = ("loudness_normalised", "input_lufs", "loudnorm_mode")
NUMBER_WORDS = set(
    "zero oh one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty "
    "sixty seventy eighty ninety hundred thousand million milli k point and naira".split())


def read_instruction(prompt, transcript):
    """True when an elicited clip is the speaker reading the scenario aloud
    ("the app is about to send... tell it to stop") rather than giving a command.
    The speech is still valid ASR data, but the intent label does not describe it."""
    if prompt.mode != "elicited" or not transcript:
        return False
    scene = set(re.sub(r"[^a-z ]", " ", prompt.display_text.lower()).split())
    # Spoken amounts never match the scenario's digits, so leave them out of the ratio.
    said = [w for w in transcript.split() if w not in NUMBER_WORDS]
    return len(said) >= 5 and sum(w in scene for w in said) / len(said) >= 0.8


def split_for(code, dev=DEV_SHARE, test=TEST_SHARE):
    h = int(hashlib.sha256(code.encode()).hexdigest(), 16) % 1000 / 1000
    return "test" if h < test else "dev" if h < test + dev else "train"


def speaker_codes(value):
    """argparse type: "AB12CD,EF34GH" -> ["AB12CD", "EF34GH"]."""
    return [c.strip().upper() for c in value.split(",") if c.strip()]


def assign_split(code, test_speakers=(), dev_speakers=(), dev=DEV_SHARE, test=TEST_SHARE):
    """Nominated speakers go where they were put and everyone else trains. Without
    nominations, fall back to the hash split. Either way a speaker is in one split only."""
    if test_speakers or dev_speakers:
        return "test" if code in test_speakers else "dev" if code in dev_speakers else "train"
    return split_for(code, dev, test)


def is_instruction_read(recording):
    return recording.review_note.strip().lower() == INSTRUCTION_READ


def conversion_settings(target):
    base = "wav 16000Hz mono s16"
    if target is None:
        return f"{base}; raw level"
    return f"{base}; loudnorm I={target} TP={TRUE_PEAK} LRA={LOUDNESS_RANGE} two-pass"


def _ffmpeg(*args):
    return subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-y", *args],
                          capture_output=True, text=True)


def _loudnorm(target, measured=None):
    f = f"loudnorm=I={target}:TP={TRUE_PEAK}:LRA={LOUDNESS_RANGE}:print_format=json"
    if measured:
        f += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
              f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
              f":offset={measured['target_offset']}:linear=true")
    return f


def _loudnorm_json(stderr):
    return json.loads(stderr[stderr.rfind("{"):stderr.rfind("}") + 1])


def convert(src, dest, target=LOUDNESS_TARGET):
    """Write src to dest as 16 kHz mono s16 wav, loudness-normalised unless target is None.
    Returns (info, error). Writes via a temp name so a failed run never leaves a
    half-written wav that the next run would mistake for a finished one."""
    chain = "aformat=channel_layouts=mono"
    info = {"loudness_normalised": False, "input_lufs": None, "loudnorm_mode": None}
    if target is not None:
        res = _ffmpeg("-i", src, "-af", f"{chain},{_loudnorm(target)}", "-f", "null", "-")
        if res.returncode:
            return None, res.stderr[-300:]
        measured = _loudnorm_json(res.stderr)
        lufs = float(measured["input_i"])
        if math.isfinite(lufs) and lufs > SILENCE_LUFS:
            info["input_lufs"] = round(lufs, 1)
            chain = f"{chain},{_loudnorm(target, measured)}"
    # loudnorm resamples to 192 kHz internally, so resample after it, not before.
    tmp = dest.with_name(f"{dest.stem}.partial.wav")
    res = _ffmpeg("-i", src, "-af", f"{chain},aresample=16000",
                  "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(tmp))
    if res.returncode:
        tmp.unlink(missing_ok=True)
        return None, res.stderr[-300:]
    if info["input_lufs"] is not None:
        info["loudness_normalised"] = True
        # Linear gain unless that would breach the true-peak ceiling.
        info["loudnorm_mode"] = _loudnorm_json(res.stderr).get("normalization_type")
    tmp.replace(dest)
    return info, None


def _breakdown(rows, field):
    """{value: {"speakers": n, "clips": n}} for a speaker attribute."""
    spk, clips = {}, Counter()
    for r in rows:
        spk.setdefault(r[field], set()).add(r["speaker"])
        clips[r[field]] += 1
    return {k: {"speakers": len(spk[k]), "clips": clips[k]} for k in sorted(spk)}


def split_report(rows_by_split):
    """What each split contains. Speaker attributes are counted both by speaker and by
    clip, since a stratified split has to hold on both."""
    report = {}
    for split in SPLITS:
        rows = rows_by_split.get(split, [])
        speakers = {}
        for r in rows:
            s = speakers.setdefault(r["speaker"], {
                "gender": r["gender"], "first_language": r["first_language"],
                "age_band": r["age_band"], "clips": 0, "duration_ms": 0})
            s["clips"] += 1
            s["duration_ms"] += r["duration_ms"]
        report[split] = {
            "speakers": len(speakers),
            "clips": len(rows),
            "hours": round(sum(r["duration_ms"] for r in rows) / 3_600_000, 3),
            "gender": _breakdown(rows, "gender"),
            "first_language": _breakdown(rows, "first_language"),
            "age_band": _breakdown(rows, "age_band"),
            "spoken_language": dict(sorted(Counter(
                r["spoken_language"] or "untranscribed" for r in rows).items())),
            "prompt_vs_spoken": dict(sorted(Counter(
                f"{r['prompt_language']} -> {r['spoken_language'] or 'untranscribed'}"
                for r in rows).items())),
            "instruction_read": sum(r["instruction_read"] for r in rows),
            "usable_for_intent": sum(r["usable_for_intent"] for r in rows),
            "needs_transcription": sum(r["needs_transcription"] for r in rows),
            "speaker_list": dict(sorted(speakers.items())),
        }
    return report


class Command(BaseCommand):
    help = "Convert recordings to 16 kHz WAV and write speaker-disjoint JSONL manifests."

    def add_arguments(self, p):
        p.add_argument("out", type=Path)
        p.add_argument("--include-pending", action="store_true",
                       help="export pending as well as approved (for quick experiments)")
        p.add_argument("--dev", type=float, default=DEV_SHARE,
                       help="hash-split dev share; ignored when speakers are nominated")
        p.add_argument("--test", type=float, default=TEST_SHARE,
                       help="hash-split test share; ignored when speakers are nominated")
        p.add_argument("--test-speakers", type=speaker_codes, action="extend", default=[],
                       metavar="CODE,CODE", help="put exactly these speakers in test; "
                       "with any nomination, un-nominated speakers go to train")
        p.add_argument("--dev-speakers", type=speaker_codes, action="extend", default=[],
                       metavar="CODE,CODE", help="put exactly these speakers in dev")
        p.add_argument("--no-normalise", action="store_true",
                       help="keep raw recording levels (for comparison runs)")
        p.add_argument("--loudness", type=float, default=LOUDNESS_TARGET, metavar="LUFS",
                       help=f"integrated loudness target (default {LOUDNESS_TARGET})")
        p.add_argument("--force", action="store_true",
                       help="reconvert every clip even if a matching wav already exists")
        p.add_argument("--exclude-instruction-read", action="store_true",
                       help=f"leave out clips reviewed as '{INSTRUCTION_READ}' "
                       "(for intent training and end-to-end evaluation)")
        p.add_argument("--dry-run", action="store_true",
                       help="print what each split would contain; convert and write nothing")

    def warn(self, msg):
        self.stderr.write(self.style.WARNING(f"warning: {msg}"))

    def nominations(self, o):
        test, dev = set(o["test_speakers"]), set(o["dev_speakers"])
        if test & dev:
            raise CommandError(f"speakers nominated for both test and dev: {sorted(test & dev)}")
        known = dict(Speaker.objects.filter(code__in=test | dev).values_list("code", "withdrawn"))
        unknown = (test | dev) - known.keys()
        if unknown:
            raise CommandError(f"unknown speaker codes: {sorted(unknown)}")
        for code in sorted(c for c, w in known.items() if w):
            self.warn(f"nominated speaker {code} has withdrawn; none of their clips are exported")
        return test, dev

    def handle(self, *a, **o):
        dry = o["dry_run"]
        if not dry and not shutil.which("ffmpeg"):
            raise CommandError("ffmpeg not found on PATH")
        test_spk, dev_spk = self.nominations(o)
        target = None if o["no_normalise"] else o["loudness"]
        settings = conversion_settings(target)
        out = o["out"]

        statuses = ["approved", "pending"] if o["include_pending"] else ["approved"]
        qs = (Recording.objects.filter(status__in=statuses, speaker__withdrawn=False)
              .select_related("speaker", "prompt").order_by("speaker__code", "created_at", "id"))
        drafts = qs.filter(review_note__startswith=DRAFT_MARK).count()
        qs = qs.exclude(review_note__startswith=DRAFT_MARK)
        if drafts:
            self.stderr.write(f"skipping {drafts} clips whose Whisper draft has not been checked")

        conv_path = out / CONVERSIONS
        conversions = {}
        if not dry:
            (out / "audio").mkdir(parents=True, exist_ok=True)
            if conv_path.exists():
                conversions = json.loads(conv_path.read_text())

        rows_by_split = {s: [] for s in SPLITS}
        tally = Counter()
        marker_counts = Counter()
        exported_ids = set()
        try:
            for r in qs.iterator():
                if o["exclude_instruction_read"] and is_instruction_read(r):
                    tally["instruction_read_excluded"] += 1
                    continue
                row = self.build_row(r, out, test_spk, dev_spk, o)
                if not dry:
                    info = self.ensure_wav(r, out, settings, target, conversions, o["force"], tally)
                    if info is None:
                        continue
                    row.update({k: info.get(k) for k in CONVERSION_FIELDS})
                exported_ids.add(str(r.id))
                marker_counts.update(row["pidgin_markers"])
                if row["read_instruction"] and not row["instruction_read"]:
                    tally["heuristic_only"] += 1
                rows_by_split[row.pop("_split")].append(row)
        finally:
            if not dry:
                conv_path.write_text(json.dumps(conversions, indent=1, sort_keys=True))

        report = split_report(rows_by_split)
        if not dry:
            for split, rows in rows_by_split.items():
                with open(out / f"{split}.jsonl", "w", encoding="utf-8") as f:
                    for row in rows:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
            (out / "split_report.json").write_text(json.dumps({
                "conversion": settings, "split_mode": "nominated" if test_spk or dev_spk else "hash",
                "statuses": statuses, "instruction_read_excluded": o["exclude_instruction_read"],
                "splits": report}, indent=1))
            tally["stale"] = sum(1 for wav in (out / "audio").glob("*/*.wav")
                                 if wav.stem not in exported_ids)
        self.summary(report, tally, marker_counts, drafts, settings, statuses,
                     test_spk, dev_spk, o)

    def build_row(self, r, out, test_spk, dev_spk, o):
        sp, pr = r.speaker, r.prompt
        guess = detect_spoken_language(r.label)
        flagged = is_instruction_read(r)
        heuristic = read_instruction(pr, r.label)
        return {
            "audio": str((out / "audio" / sp.code / f"{r.id}.wav").relative_to(out)),
            "text": r.label,                      # empty for untranscribed elicited clips
            "needs_transcription": not r.label,
            "read_instruction": heuristic,        # transcript heuristic, unreviewed
            "instruction_read": flagged,          # reviewer marked it in review_note
            "usable_for_intent": not (flagged or heuristic),
            "mode": pr.mode,
            "language": pr.language,              # kept for compatibility: the PROMPT's language
            "prompt_language": pr.language,
            "spoken_language": guess.language,    # pidgin / english / ambiguous / null
            "pidgin_markers": guess.pidgin_markers,
            "english_markers": guess.english_markers,
            "intent": pr.intent,
            "entities": pr.entities, "template_id": pr.template_id,
            "speaker": sp.code, "gender": sp.gender, "age_band": sp.age_band,
            "first_language": sp.first_language, "state": sp.state,
            "environment": sp.environment, "duration_ms": r.duration_ms,
            "_split": assign_split(sp.code, test_spk, dev_spk, o["dev"], o["test"]),
        }

    def ensure_wav(self, r, out, settings, target, conversions, force, tally):
        """Reuse the wav when it was made with the same settings; otherwise (re)convert.
        Returns the per-clip conversion info, or None if conversion failed."""
        dest = out / "audio" / r.speaker.code / f"{r.id}.wav"
        key = str(r.id)
        prev = conversions.get(key)
        if not force and dest.exists() and prev and prev["settings"] == settings:
            tally["reused"] += 1
            return prev["info"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=Path(r.audio.name).suffix) as tmp:
            with r.audio.open("rb") as src:
                shutil.copyfileobj(src, tmp)
            tmp.flush()
            info, err = convert(tmp.name, dest, target)
        if err:
            self.stderr.write(f"ffmpeg failed on {r.id}: {err.strip()[:200]}")
            tally["failed"] += 1
            return None
        conversions[key] = {"settings": settings, "info": info}
        tally["converted"] += 1
        if target is not None and not info["loudness_normalised"]:
            tally["too_quiet_to_normalise"] += 1
        if info["loudnorm_mode"] == "dynamic":
            tally["dynamic_fallback"] += 1
        return info

    def summary(self, report, tally, markers, drafts, settings, statuses, test_spk, dev_spk, o):
        w = self.stdout.write
        head = lambda t: w(self.style.MIGRATE_HEADING(f"\n{t}"))  # noqa: E731
        total = sum(report[s]["clips"] for s in SPLITS)

        head("DRY RUN: nothing converted or written" if o["dry_run"] else f"EXPORTED to {o['out']}")
        w(f"  statuses      : {', '.join(statuses)}")
        w(f"  audio         : {settings}")
        if test_spk or dev_spk:
            w(f"  split         : nominated (test {len(test_spk)}, dev {len(dev_spk)}, "
              f"everyone else train)")
        else:
            w(f"  split         : speaker hash (dev {o['dev']:.0%}, test {o['test']:.0%})")
        if not o["dry_run"]:
            w(f"  wav files     : {tally['converted']} converted, {tally['reused']} reused, "
              f"{tally['failed']} failed")
        w(f"  left out      : {drafts} unchecked Whisper drafts, "
          f"{tally['instruction_read_excluded']} instruction reads"
          + ("" if o["exclude_instruction_read"] else " (not excluded; flagged in output)"))

        cols = list(SPLITS)
        line = lambda label, cells: w(f"  {label:<26}" + "".join(f"{c:>20}" for c in cells))  # noqa: E731
        head("SPLITS")
        line("", cols)
        line("speakers", [report[s]["speakers"] for s in cols])
        line("clips", [f"{report[s]['clips']} ({report[s]['clips'] / total:.0%})" if total else 0
                       for s in cols])
        line("hours", [f"{report[s]['hours']:.2f}" for s in cols])
        for field in ("gender", "first_language", "age_band"):
            head(f"{field.upper()}  (speakers / clips, % of split's clips)")
            values = sorted({v for s in cols for v in report[s][field]})
            for v in values:
                cells = []
                for s in cols:
                    b = report[s][field].get(v)
                    n = report[s]["clips"]
                    cells.append(f"{b['speakers']} / {b['clips']} ({b['clips'] / n:.0%})" if b else "-")
                line(v, cells)
        head("SPOKEN LANGUAGE  (clips, % of split; detected from the final transcript)")
        for v in sorted({v for s in cols for v in report[s]["spoken_language"]}):
            line(v, [self._pct(report[s]["spoken_language"].get(v, 0), report[s]["clips"])
                     for s in cols])
        head("PROMPT LANGUAGE -> SPOKEN LANGUAGE  (clips)")
        for v in sorted({v for s in cols for v in report[s]["prompt_vs_spoken"]}):
            line(v, [report[s]["prompt_vs_spoken"].get(v, 0) for s in cols])
        if markers:
            w("  pidgin markers: " + ", ".join(f"{m} {n}" for m, n in markers.most_common(12)))
        head("INTENT USABILITY  (clips)")
        line("instruction_read", [report[s]["instruction_read"] for s in cols])
        line("usable_for_intent", [report[s]["usable_for_intent"] for s in cols])
        line("needs_transcript", [report[s]["needs_transcription"] for s in cols])
        head("SPEAKERS")
        for s in cols:
            spk = report[s]["speaker_list"]
            w(f"  {s:<6}: " + (", ".join(
                f"{c} ({v['gender'][0].upper()}, {v['first_language']}, {v['clips']})"
                for c, v in spk.items()) or "-"))

        self.warnings(report, tally, test_spk | dev_spk)

    @staticmethod
    def _pct(n, total):
        return f"{n} ({n / total:.0%})" if total else "0"

    def warnings(self, report, tally, nominated):
        notes = []
        for s in ("dev", "test"):
            if not report[s]["clips"]:
                notes.append(f"{s} split is empty")
        for field in ("gender", "first_language"):
            everywhere = {v for s in SPLITS for v in report[s][field]}
            for s in ("dev", "test"):
                missing = everywhere - report[s][field].keys()
                if report[s]["clips"] and missing:
                    notes.append(f"{s} has no {field} = {', '.join(sorted(missing))}")
        exported = {c for s in SPLITS for c in report[s]["speaker_list"]}
        for code in sorted(nominated - exported):
            notes.append(f"nominated speaker {code} has no exported clips")
        if tally["heuristic_only"]:
            notes.append(f"{tally['heuristic_only']} clips look like instruction reads by "
                         f"transcript but are not marked '{INSTRUCTION_READ}'; review them "
                         "(already usable_for_intent=false)")
        if tally["too_quiet_to_normalise"]:
            notes.append(f"{tally['too_quiet_to_normalise']} clips were too quiet to measure "
                         "and were converted at raw level")
        if tally["dynamic_fallback"]:
            notes.append(f"{tally['dynamic_fallback']} clips needed dynamic (not linear) "
                         "loudness normalisation to stay under the true-peak ceiling")
        if tally["stale"]:
            notes.append(f"{tally['stale']} wav files under audio/ are not in this export "
                         "(excluded, withdrawn or deleted clips); delete them before sharing")
        if tally["failed"]:
            notes.append(f"{tally['failed']} clips failed to convert and are missing")
        if notes:
            self.stdout.write(self.style.MIGRATE_HEADING("\nCHECK"))
            for n in notes:
                self.stdout.write(self.style.WARNING(f"  - {n}"))
        self.stdout.write("")
