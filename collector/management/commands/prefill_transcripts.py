"""
Draft transcripts for elicited clips using off-the-shelf Whisper.

This is NOT training. It just gives you a rough first draft to correct by ear
in the admin, which is far faster than typing from an empty box.

Install first (one time):
    pip install faster-whisper

Usage:
    python manage.py prefill_transcripts --limit 5 --dry-run   # try it on 5 clips
    python manage.py prefill_transcripts                       # do them all

Notes
- Only touches elicited clips with an empty transcript_override.
- Never overwrites anything you have already typed.
- Skips test-split speakers by default, so evaluation references are typed from
  scratch and cannot be anchored to Whisper's own output.
- Every draft is stamped in review_note; the admin filter "Whisper draft, unchecked"
  lists them, and editing a row by hand clears the stamp.
- Output is lowercased with punctuation stripped, per LABELLING.md. Whisper will
  still write digits ("5k", "5000"); you fix those to words while correcting.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from collector.management.commands.export_dataset import split_for
from collector.models import DRAFT_MARK, Recording

# Primes Whisper toward the house spelling and spelled-out amounts. It only biases
# vocabulary; on near-silent audio it can echo back, which the reviewer will catch.
PRIMER = ("abeg send five thousand naira give tunde wetin dey my account "
          "oya send am no be am make you buy two k airtime for my line")


def normalise(text):
    """Lowercase, strip punctuation, collapse whitespace. Digits are left alone
    deliberately, so they stand out as something for you to fix by ear."""
    text = text.lower()
    text = re.sub(r"(?<=\d),(?=\d)", "", text)          # 5,000 -> 5000
    text = re.sub(r"[^\w\s'.]", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)     # keep 1.5k, drop sentence dots
    return re.sub(r"\s+", " ", text).strip()


def load_audio(path):
    """Decode to 16 kHz mono float32 with ffmpeg. faster-whisper's own decoder goes
    through PyAV, whose API changes break it across versions."""
    import numpy as np
    res = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path,
         "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
        capture_output=True)
    if res.returncode:
        raise RuntimeError(res.stderr.decode()[:200])
    return np.frombuffer(res.stdout, dtype=np.float32)


class Command(BaseCommand):
    help = "Draft transcripts for untranscribed elicited clips using Whisper."

    def add_arguments(self, p):
        p.add_argument("--model", default="large-v3",
                       help="large-v3 is most accurate; try medium if it's too slow")
        p.add_argument("--limit", type=int, help="only process the first N clips")
        p.add_argument("--dry-run", action="store_true", help="print, don't save")
        p.add_argument("--include-test", action="store_true",
                       help="also draft test-split speakers (biases your evaluation)")

    def handle(self, *a, **o):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise CommandError("pip install faster-whisper")
        if not shutil.which("ffmpeg"):
            raise CommandError("ffmpeg not found on PATH (brew install ffmpeg)")

        # Read clips are deliberately out of scope: an empty override there means the
        # exact prompt transcript is the label, and a draft would replace a correct one.
        qs = (Recording.objects.filter(transcript_override="", speaker__withdrawn=False,
                                       prompt__mode="elicited")
              .exclude(status="rejected").select_related("prompt", "speaker")
              .order_by("created_at"))
        clips = list(qs)
        if not o["include_test"]:
            held_out = [r for r in clips if split_for(r.speaker.code) == "test"]
            clips = [r for r in clips if split_for(r.speaker.code) != "test"]
            if held_out:
                self.stdout.write(f"Skipping {len(held_out)} test-split clips; type those by ear.")
        if o["limit"]:
            clips = clips[:o["limit"]]

        total = len(clips)
        if not total:
            self.stdout.write("Nothing to do.")
            return

        self.stdout.write(f"Loading Whisper {o['model']} (first run downloads ~1.5 GB)…")
        model = WhisperModel(o["model"], device="cpu", compute_type="int8")

        done = saved = 0
        for r in clips:
            suffix = Path(r.audio.name).suffix or ".webm"
            with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
                with r.audio.open("rb") as src:
                    shutil.copyfileobj(src, tmp)
                tmp.flush()
                try:
                    audio = load_audio(tmp.name)
                except RuntimeError as e:
                    self.stderr.write(f"could not decode {r.id}: {e}")
                    continue
            # Nigerian Pidgin has no Whisper language token, so we force English
            # and let the words fall where they may.
            segments, _info = model.transcribe(
                audio, language="en", beam_size=5, initial_prompt=PRIMER,
                vad_filter=True, condition_on_previous_text=False)
            segments = list(segments)
            text = normalise(" ".join(s.text for s in segments))
            logprob = min((s.avg_logprob for s in segments), default=0.0)

            done += 1
            self.stdout.write(f"[{done}/{total}] {r.speaker.code}  "
                              f"prompt: {r.prompt.display_text[:45]}\n"
                              f"          whisper: {text or '(nothing heard)'}  "
                              f"(logprob {logprob:.2f})")
            if not o["dry_run"] and text:
                Recording.objects.filter(id=r.id, transcript_override="").update(
                    transcript_override=text,
                    review_note=f"{DRAFT_MARK} logprob={logprob:.2f}")
                saved += 1

        if o["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"\nDry run: {done} clips transcribed, nothing saved."))
            return
        self.stdout.write(self.style.SUCCESS(
            f"\n{saved} of {done} clips drafted. In the admin, filter "
            f"Transcript -> 'Whisper draft, unchecked' and correct each by ear. "
            f"Numbers as words."))