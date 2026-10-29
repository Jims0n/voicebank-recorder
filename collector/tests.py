import array
import io
import json
import math
import shutil
import subprocess
import tempfile
import wave
from collections import Counter
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from collector.management.commands.export_dataset import SPLITS, split_for
from collector.models import Prompt, Recording, Speaker

MEDIA = tempfile.mkdtemp(prefix="voicebank-test-")

# Django forces DEBUG=False in tests, which makes the manifest static storage demand a
# staticfiles.json that only collectstatic writes. Use plain storage instead.
isolated = override_settings(
    MEDIA_ROOT=MEDIA,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)


def make_speaker(**kw):
    return Speaker.objects.create(**{
        "consent_version": "v1", "consented_at": timezone.now(), "age_band": "25-34",
        "gender": "female", "first_language": "yoruba", "state": "Lagos",
        "language_pref": "both", "environment": "quiet", **kw})


def make_prompt(**kw):
    return Prompt.objects.create(**{
        "mode": "read", "language": "english", "intent": "transfer", "template_id": "t1",
        "display_text": "Send five k to Ade", "transcript": "send five k to ade", **kw})


def clip(name="clip.webm", content_type="audio/webm", size=2048):
    return SimpleUploadedFile(name, b"\x1a\x45\xdf\xa3" + b"0" * size, content_type=content_type)


@isolated
class UploadApiTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.speaker = make_speaker()
        self.prompt = make_prompt()
        session = self.client.session
        session["speaker_id"] = str(self.speaker.id)
        session["batch_start"] = 0
        session.save()

    def post(self, **overrides):
        data = {"audio": clip(), "prompt_id": self.prompt.id,
                "duration_ms": "2500", "peak": "0.6", "clip_fraction": "0.0"}
        data.update(overrides)
        return self.client.post(reverse("api_upload"), data)

    def test_happy_path_creates_recording(self):
        res = self.post()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Recording.objects.count(), 1)
        self.prompt.refresh_from_db()
        self.assertEqual(self.prompt.recording_count, 1)

    def test_rejects_upload_without_session(self):
        self.client.logout()
        self.client.cookies.clear()
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(Recording.objects.count(), 0)

    def test_rejects_too_short_and_too_long(self):
        self.assertEqual(self.post(duration_ms=str(settings.MIN_DURATION_MS - 1)).status_code, 400)
        self.assertEqual(self.post(duration_ms=str(settings.MAX_DURATION_MS + 1)).status_code, 400)
        self.assertEqual(Recording.objects.count(), 0)

    def test_rejects_unsupported_mime(self):
        res = self.post(audio=clip("evil.exe", "application/octet-stream"))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Recording.objects.count(), 0)

    def test_rejects_oversized_file(self):
        res = self.post(audio=clip(size=settings.MAX_UPLOAD_BYTES + 1))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Recording.objects.count(), 0)

    def test_duplicate_prompt_does_not_create_second_recording(self):
        """The unique constraint must absorb a double-tap without a 500."""
        self.post()
        res = self.post()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Recording.objects.count(), 1)
        self.prompt.refresh_from_db()
        self.assertEqual(self.prompt.recording_count, 1)

    def test_peak_is_clamped(self):
        self.post(peak="99")
        self.assertEqual(Recording.objects.get().peak_level, 1.0)


@isolated
class PromptSelectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.speaker = make_speaker(language_pref="english")
        session = self.client.session
        session["speaker_id"] = str(self.speaker.id)
        session["batch_start"] = 0
        session.save()

    def test_never_serves_a_prompt_the_speaker_already_recorded(self):
        done = make_prompt(transcript="already done")
        Recording.objects.create(speaker=self.speaker, prompt=done, audio=clip(),
                                 mime_type="audio/webm", duration_ms=1200, peak_level=0.5)
        remaining = make_prompt(transcript="still to do")
        for _ in range(10):
            payload = self.client.get(reverse("api_next")).json()
            self.assertEqual(payload["id"], remaining.id)

    def test_inactive_prompts_are_never_served(self):
        make_prompt(transcript="switched off", active=False)
        self.assertTrue(self.client.get(reverse("api_next")).json()["finished"])

    def test_batch_ends_after_batch_size(self):
        make_prompt(transcript="one more")
        session = self.client.session
        session["batch_start"] = -settings.BATCH_SIZE
        session.save()
        self.assertTrue(self.client.get(reverse("api_next")).json()["finished"])


@isolated
class WithdrawalTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_withdrawal_deletes_recordings_and_decrements_counters(self):
        speaker = make_speaker()
        prompt = make_prompt()
        rec = Recording.objects.create(speaker=speaker, prompt=prompt, audio=clip(),
                                       mime_type="audio/webm", duration_ms=1200, peak_level=0.5)
        path = rec.audio.path
        Prompt.objects.filter(id=prompt.id).update(recording_count=1)

        self.client.post(reverse("withdraw"), {"code": speaker.code})

        speaker.refresh_from_db()
        prompt.refresh_from_db()
        self.assertTrue(speaker.withdrawn)
        self.assertIsNotNone(speaker.withdrawn_at)
        self.assertEqual(Recording.objects.count(), 0)
        self.assertEqual(prompt.recording_count, 0)
        self.assertFalse(shutil.os.path.exists(path), "audio file must be removed from storage")

    def test_code_guessing_is_throttled(self):
        for _ in range(settings.CODE_ATTEMPT_LIMIT + 2):
            res = self.client.post(reverse("withdraw"), {"code": "ZZZZZZ"})
        self.assertContains(res, "Too many attempts")

    def test_withdrawn_code_cannot_resume_and_says_so(self):
        speaker = make_speaker(withdrawn=True, withdrawn_at=timezone.now())
        res = self.client.post(reverse("home"), {"code": speaker.code})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "withdrawn from the study")
        self.assertNotIn("speaker_id", self.client.session)

    def test_unknown_code_is_reported_as_not_found(self):
        res = self.client.post(reverse("home"), {"code": "ZZZZZZ"})
        self.assertContains(res, "wasn&#x27;t found")


@isolated
class AdminTests(TestCase):
    def test_recording_changelist_renders(self):
        """Every list column runs per row, so one bad format string takes the page down."""
        from django.contrib.auth.models import User
        speaker = make_speaker()
        Recording.objects.create(speaker=speaker, prompt=make_prompt(), audio=clip(),
                                 mime_type="audio/webm", duration_ms=1200, peak_level=0.1222,
                                 clip_fraction=0.03)
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "pw"))
        res = self.client.get(reverse("admin:collector_recording_changelist"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "peak 0.122")


class ExportSplitTests(TestCase):
    def test_split_is_deterministic_and_speaker_disjoint(self):
        codes = [f"SPK{i:03d}" for i in range(500)]
        first = {c: split_for(c, 0.1, 0.2) for c in codes}
        second = {c: split_for(c, 0.1, 0.2) for c in codes}
        self.assertEqual(first, second, "same speaker must always land in the same split")
        self.assertEqual(set(first.values()), {"train", "dev", "test"})

    def test_split_proportions_are_roughly_right(self):
        codes = [f"SPK{i:04d}" for i in range(4000)]
        splits = [split_for(c, 0.1, 0.2) for c in codes]
        self.assertAlmostEqual(splits.count("test") / len(codes), 0.2, delta=0.03)
        self.assertAlmostEqual(splits.count("dev") / len(codes), 0.1, delta=0.03)


class LabelTests(TestCase):
    def test_primer_echo_is_detected(self):
        from collector.management.commands.prefill_transcripts import echoes_primer
        self.assertTrue(echoes_primer("oya send five thousand naira give tunde wetin dey"))
        self.assertFalse(echoes_primer("abeg send five k give musa"))

    def test_reading_the_scenario_aloud_is_flagged(self):
        from collector.management.commands.export_dataset import read_instruction
        p = make_prompt(mode="elicited", transcript="", intent="cancel",
                        display_text="The app is about to send ₦150,000 to the wrong person. Tell it to stop.")
        self.assertTrue(read_instruction(p, "the app is about to send one hundred and fifty "
                                            "thousand naira to the wrong person tell it to stop"))
        self.assertFalse(read_instruction(p, "abeg stop am no send am"))
        self.assertFalse(read_instruction(p, "stop the one point five k you about to send is not the legitimate person"))
    def test_draft_normaliser_keeps_numbers_readable(self):
        from collector.management.commands.prefill_transcripts import normalise
        self.assertEqual(normalise("Send ₦5,000 to Tunde."), "send 5000 to tunde")
        self.assertEqual(normalise("Abeg, send 1.5k now!"), "abeg send 1.5k now")
    def test_override_wins_over_prompt_transcript(self):
        speaker, prompt = make_speaker(), make_prompt()
        rec = Recording(speaker=speaker, prompt=prompt)
        self.assertEqual(rec.label, "send five k to ade")
        rec.transcript_override = "send five thousand to ade"
        self.assertEqual(rec.label, "send five thousand to ade")

    def test_elicited_clip_has_empty_label_until_transcribed(self):
        speaker = make_speaker()
        prompt = make_prompt(mode="elicited", transcript="", display_text="Ask for a transfer.")
        self.assertEqual(Recording(speaker=speaker, prompt=prompt).label, "")


class PromptGenerationTests(TestCase):
    def test_rerunning_creates_nothing(self):
        """build.sh runs this on every deploy; a second run must be a no-op."""
        from django.core.management import call_command
        call_command("generate_prompts", verbosity=0)
        after_first = Prompt.objects.count()
        call_command("generate_prompts", verbosity=0)
        call_command("generate_prompts", verbosity=0)
        self.assertEqual(Prompt.objects.count(), after_first)

    def test_read_transcripts_are_unique(self):
        from django.core.management import call_command
        call_command("generate_prompts", verbosity=0)
        reads = list(Prompt.objects.filter(mode="read").values_list("transcript", flat=True))
        self.assertEqual(len(reads), len(set(reads)))

    def test_confirm_and_cancel_are_well_covered(self):
        """These drive the risk layer; thin coverage here is a dataset defect."""
        from django.core.management import call_command
        call_command("generate_prompts", verbosity=0)
        for intent in ("confirm", "cancel"):
            for language in ("pidgin", "english"):
                n = Prompt.objects.filter(intent=intent, language=language).count()
                self.assertGreaterEqual(n, 10, f"{language} {intent} prompts are too few ({n})")


class SpokenLanguageTests(TestCase):
    def detect(self, text):
        from collector.language import detect_spoken_language
        return detect_spoken_language(text)

    def test_pidgin_markers_fire_and_are_reported(self):
        cases = {
            "abeg send five k give tunde": {"abeg", "give <name>"},
            "wetin dey my account": {"wetin", "dey"},
            "make you cancel am": {"make you", "am (object)"},
            "send am go gtb": {"am (object)", "go <name>"},
            "no be am": {"no be", "am (object)"},
            "i wan send fifteen k to chidi": {"wan"},
            "send two fifty k enter femi account": {"enter <account>"},
            "e no correct": {"e", "no correct"},
        }
        for text, expected in cases.items():
            guess = self.detect(text)
            self.assertEqual(guess.language, "pidgin", text)
            self.assertTrue(expected <= set(guess.pidgin_markers), (text, guess.pidgin_markers))

    def test_english_look_alikes_do_not_fire(self):
        for text in ("i am sending money to tunde", "go ahead and send it", "give me my balance",
                     "give tunde five k", "how much am i owing", "please give it to ade",
                     "send it to the wrong account at eight am"):
            guess = self.detect(text)
            self.assertEqual(guess.pidgin_markers, [], text)
            self.assertIn(guess.language, ("english", "ambiguous"), text)

    def test_marker_free_commands_are_ambiguous_and_empty_is_none(self):
        self.assertEqual(self.detect("check my balance").language, "ambiguous")
        self.assertEqual(self.detect("yes").language, "ambiguous")
        self.assertIsNone(self.detect("").language)

    def test_english_prompt_answered_in_pidgin(self):
        guess = self.detect("abeg the app make you stop am")
        self.assertEqual(guess.language, "pidgin")
        self.assertIn("the", guess.english_markers)   # recorded, but Pidgin wins

    def test_no_read_prompt_is_tagged_as_the_other_language(self):
        """Read prompts have a known language; the detector may abstain, never contradict."""
        from django.core.management import call_command
        call_command("generate_prompts", verbosity=0)
        found = Counter()
        for lang, text in Prompt.objects.filter(mode="read").values_list("language", "transcript"):
            guess = self.detect(text).language
            self.assertNotIn({lang, guess}, [{"pidgin", "english"}], text)
            found[lang, guess] += 1
        pidgin_total = found["pidgin", "pidgin"] + found["pidgin", "ambiguous"]
        self.assertGreater(found["pidgin", "pidgin"] / pidgin_total, 0.8)


class SplitAssignmentTests(TestCase):
    def test_nominated_speakers_go_where_told_and_the_rest_train(self):
        from collector.management.commands.export_dataset import assign_split
        self.assertEqual(assign_split("AAA", {"AAA"}, {"BBB"}), "test")
        self.assertEqual(assign_split("BBB", {"AAA"}, {"BBB"}), "dev")
        self.assertEqual(assign_split("CCC", {"AAA"}, {"BBB"}), "train")
        self.assertEqual(assign_split("CCC"), split_for("CCC"))

    def test_codes_parse_from_comma_lists(self):
        from collector.management.commands.export_dataset import speaker_codes
        self.assertEqual(speaker_codes("ab12cd, EF34GH,"), ["AB12CD", "EF34GH"])


def tone(volume, seconds=2.5):
    """A real, decodable clip at a given level (peak ≈ volume)."""
    res = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=f=220:d={seconds},volume={volume}", "-ac", "2", "-f", "wav", "pipe:1"],
        capture_output=True, check=True)
    return SimpleUploadedFile("clip.wav", res.stdout, content_type="audio/wav")


def rms_dbfs(path):
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        samples = array.array("h", w.readframes(w.getnframes()))
    return 20 * math.log10(math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768)


EXPORT_MEDIA = tempfile.mkdtemp(prefix="voicebank-export-test-")


@skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
@override_settings(MEDIA_ROOT=EXPORT_MEDIA,
                   STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                             "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class ExportCommandTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(EXPORT_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix="voicebank-export-out-"))
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.quiet = make_speaker(code="QUIET1", gender="male", first_language="igbo")
        self.loud = make_speaker(code="LOUD22", gender="female", first_language="yoruba")
        self.other = make_speaker(code="OTHER3", gender="female", first_language="hausa")
        english = make_prompt(mode="elicited", transcript="", intent="transfer",
                              display_text="Ask the app to send ₦5,000 to Tunde.")
        self.rec = {}
        for speaker, volume, said, note in (
                (self.quiet, 0.02, "abeg send five k give tunde", ""),
                (self.loud, 0.9, "send five thousand naira to tunde", ""),
                (self.other, 0.3, "ask the app to send five thousand naira to tunde", "instruction_read")):
            self.rec[speaker.code] = Recording.objects.create(
                speaker=speaker, prompt=english, audio=tone(volume), mime_type="audio/wav",
                duration_ms=2500, peak_level=volume, status="approved",
                transcript_override=said, review_note=note)

    def export(self, *args, out=None):
        from django.core.management import call_command
        buf = io.StringIO()
        call_command("export_dataset", str(out or self.out), *args, stdout=buf, stderr=io.StringIO())
        return buf.getvalue()

    def manifest(self, split, out=None):
        path = (out or self.out) / f"{split}.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_nominated_split_is_speaker_disjoint_and_reported(self):
        text = self.export("--test-speakers", "quiet1", "--dev-speakers", "LOUD22")
        self.assertEqual({r["speaker"] for r in self.manifest("test")}, {"QUIET1"})
        self.assertEqual({r["speaker"] for r in self.manifest("dev")}, {"LOUD22"})
        self.assertEqual({r["speaker"] for r in self.manifest("train")}, {"OTHER3"})
        report = json.loads((self.out / "split_report.json").read_text())["splits"]
        self.assertEqual(report["test"]["gender"], {"male": {"speakers": 1, "clips": 1}})
        self.assertEqual(report["test"]["spoken_language"], {"pidgin": 1})
        self.assertIn("QUIET1 (M, igbo, 1)", text)

    def test_bad_nominations_are_rejected(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.export("--test-speakers", "QUIET1", "--dev-speakers", "QUIET1")
        with self.assertRaises(CommandError):
            self.export("--test-speakers", "NOSUCH")

    def test_rows_keep_old_fields_and_add_language_and_instruction_flags(self):
        self.export("--test-speakers", "QUIET1")
        rows = {r["speaker"]: r for s in SPLITS for r in self.manifest(s)}
        pidgin = rows["QUIET1"]
        self.assertEqual((pidgin["language"], pidgin["prompt_language"]), ("english", "english"))
        self.assertEqual(pidgin["spoken_language"], "pidgin")
        self.assertIn("give <name>", pidgin["pidgin_markers"])
        for key in ("audio", "text", "needs_transcription", "read_instruction", "mode", "intent",
                    "entities", "template_id", "gender", "age_band", "first_language", "state",
                    "environment", "duration_ms"):
            self.assertIn(key, pidgin)
        self.assertTrue(rows["OTHER3"]["instruction_read"])
        self.assertFalse(rows["OTHER3"]["usable_for_intent"])
        self.assertTrue(rows["LOUD22"]["usable_for_intent"])

    def test_heuristic_alone_does_not_change_usability(self):
        Recording.objects.filter(speaker=self.other).update(review_note="")
        text = self.export("--exclude-instruction-read")
        rows = {r["speaker"]: r for s in SPLITS for r in self.manifest(s)}
        self.assertTrue(rows["OTHER3"]["read_instruction"])
        self.assertFalse(rows["OTHER3"]["instruction_read"])
        self.assertTrue(rows["OTHER3"]["usable_for_intent"])
        self.assertIn("1 clips look like instruction reads by transcript", text)

    def test_instruction_reads_can_be_excluded(self):
        self.export("--test-speakers", "QUIET1", "--exclude-instruction-read")
        speakers = {r["speaker"] for s in SPLITS for r in self.manifest(s)}
        self.assertEqual(speakers, {"QUIET1", "LOUD22"})

    def test_loudness_is_normalised_unless_disabled(self):
        self.export()
        raw_out = self.out / "raw"
        self.export("--no-normalise", out=raw_out)
        wav = lambda root, code: root / "audio" / code / f"{self.rec[code].id}.wav"  # noqa: E731
        normed_gap = rms_dbfs(wav(self.out, "LOUD22")) - rms_dbfs(wav(self.out, "QUIET1"))
        raw_gap = rms_dbfs(wav(raw_out, "LOUD22")) - rms_dbfs(wav(raw_out, "QUIET1"))
        self.assertGreater(raw_gap, 30)          # 0.9 vs 0.02 peak is ~33 dB
        self.assertLess(abs(normed_gap), 1.5)
        rows = {r["speaker"]: r for s in SPLITS for r in self.manifest(s)}
        self.assertTrue(rows["QUIET1"]["loudness_normalised"])
        self.assertLess(rows["QUIET1"]["input_lufs"], rows["LOUD22"]["input_lufs"])

    def test_rerun_reuses_wavs_until_settings_change_or_forced(self):
        self.assertIn("3 converted, 0 reused", self.export())
        before = (self.out / "train.jsonl").read_text() + (self.out / "test.jsonl").read_text()
        self.assertIn("0 converted, 3 reused", self.export())
        after = (self.out / "train.jsonl").read_text() + (self.out / "test.jsonl").read_text()
        self.assertEqual(before, after)
        self.assertIn("3 converted, 0 reused", self.export("--no-normalise"))
        self.assertIn("3 converted, 0 reused", self.export("--no-normalise", "--force"))

    def test_dry_run_writes_nothing(self):
        out = self.out / "dry"
        text = self.export("--dry-run", "--test-speakers", "QUIET1", out=out)
        self.assertFalse(out.exists())
        self.assertIn("DRY RUN", text)
        self.assertIn("SPOKEN LANGUAGE", text)
