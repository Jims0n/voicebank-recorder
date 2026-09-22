"""
Export recordings to a training-ready dataset:
  out/
    audio/<speaker>/<recording_id>.wav   16 kHz, mono, 16-bit PCM (what Whisper expects)
    train.jsonl / dev.jsonl / test.jsonl
Splits are BY SPEAKER, never by clip. If the same voice appears in train and test,
your WER is optimistic and an examiner can rightly call it out.
Requires ffmpeg on PATH.
"""
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from collector.models import Recording


def split_for(code, dev, test):
    h = int(hashlib.sha256(code.encode()).hexdigest(), 16) % 1000 / 1000
    return "test" if h < test else "dev" if h < test + dev else "train"


class Command(BaseCommand):
    help = "Convert recordings to 16 kHz WAV and write speaker-disjoint JSONL manifests."

    def add_arguments(self, p):
        p.add_argument("out", type=Path)
        p.add_argument("--include-pending", action="store_true",
                       help="export pending as well as approved (for quick experiments)")
        p.add_argument("--dev", type=float, default=0.1)
        p.add_argument("--test", type=float, default=0.2)

    def handle(self, *a, **o):
        if not shutil.which("ffmpeg"):
            raise CommandError("ffmpeg not found on PATH")
        out = o["out"]
        (out / "audio").mkdir(parents=True, exist_ok=True)
        statuses = ["approved", "pending"] if o["include_pending"] else ["approved"]
        qs = (Recording.objects.filter(status__in=statuses, speaker__withdrawn=False)
              .select_related("speaker", "prompt"))

        files = {s: open(out / f"{s}.jsonl", "w", encoding="utf-8") for s in ("train", "dev", "test")}
        counts = {s: 0 for s in files}
        skipped = 0
        for r in qs.iterator():
            sp, pr = r.speaker, r.prompt
            dest = out / "audio" / sp.code / f"{r.id}.wav"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                with tempfile.NamedTemporaryFile(suffix=Path(r.audio.name).suffix) as tmp:
                    with r.audio.open("rb") as src:
                        shutil.copyfileobj(src, tmp)
                    tmp.flush()
                    res = subprocess.run(
                        ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp.name,
                         "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(dest)],
                        capture_output=True)
                    if res.returncode:
                        self.stderr.write(f"ffmpeg failed on {r.id}: {res.stderr.decode()[:200]}")
                        skipped += 1
                        continue
            split = split_for(sp.code, o["dev"], o["test"])
            row = {
                "audio": str(dest.relative_to(out)),
                "text": r.label,                      # empty for untranscribed elicited clips
                "needs_transcription": not r.label,
                "mode": pr.mode, "language": pr.language, "intent": pr.intent,
                "entities": pr.entities, "template_id": pr.template_id,
                "speaker": sp.code, "gender": sp.gender, "age_band": sp.age_band,
                "first_language": sp.first_language, "state": sp.state,
                "environment": sp.environment, "duration_ms": r.duration_ms,
            }
            files[split].write(json.dumps(row, ensure_ascii=False) + "\n")
            counts[split] += 1
        for f in files.values():
            f.close()
        self.stdout.write(self.style.SUCCESS(f"exported {counts}, skipped {skipped}"))
