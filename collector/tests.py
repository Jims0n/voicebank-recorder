import shutil
import tempfile

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from collector.management.commands.export_dataset import split_for
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
