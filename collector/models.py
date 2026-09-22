import secrets
import uuid

from django.db import models


def new_speaker_code():
    # Short, pseudonymous, readable over the phone. Used for resume + withdrawal.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


class Speaker(models.Model):
    AGE = [("18-24", "18–24"), ("25-34", "25–34"), ("35-44", "35–44"),
           ("45-54", "45–54"), ("55+", "55 and over")]
    GENDER = [("female", "Female"), ("male", "Male"), ("undisclosed", "Prefer not to say")]
    FIRST_LANG = [("yoruba", "Yoruba"), ("igbo", "Igbo"), ("hausa", "Hausa"),
                  ("pidgin", "Nigerian Pidgin"), ("english", "English"), ("other", "Other")]
    LANG_PREF = [("pidgin", "Mostly Pidgin"), ("english", "Mostly English"), ("both", "Both equally")]
    ENV = [("quiet", "Quiet room"), ("some", "Some background noise"), ("noisy", "Noisy (street, market, bus)")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=8, unique=True, default=new_speaker_code)
    consent_version = models.CharField(max_length=10)
    consented_at = models.DateTimeField()
    age_band = models.CharField(max_length=8, choices=AGE)
    gender = models.CharField(max_length=12, choices=GENDER)
    first_language = models.CharField(max_length=10, choices=FIRST_LANG)
    first_language_other = models.CharField(max_length=50, blank=True)
    state = models.CharField("state you grew up in", max_length=40)
    language_pref = models.CharField(max_length=8, choices=LANG_PREF)
    environment = models.CharField(max_length=8, choices=ENV)
    user_agent = models.TextField(blank=True)
    withdrawn = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.code


class Prompt(models.Model):
    MODE = [("read", "Read aloud"), ("elicited", "Own words")]
    LANG = [("pidgin", "Pidgin"), ("english", "English")]
    INTENT = [("transfer", "Transfer"), ("balance", "Balance"), ("airtime", "Airtime"),
              ("bill", "Bill payment"), ("history", "History"),
              ("confirm", "Confirm"), ("cancel", "Cancel")]

    mode = models.CharField(max_length=8, choices=MODE)
    language = models.CharField(max_length=8, choices=LANG)
    intent = models.CharField(max_length=10, choices=INTENT)
    template_id = models.CharField(max_length=20)
    display_text = models.TextField()              # what the speaker sees
    transcript = models.TextField(blank=True)      # normalised label; blank for elicited
    entities = models.JSONField(default=dict)      # ground truth: amount, recipient, bank, account...
    recording_count = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["mode", "language", "recording_count"])]

    def __str__(self):
        return f"[{self.mode}/{self.language}] {self.display_text[:60]}"


def audio_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "webm"
    return f"raw/{instance.speaker.code}/{instance.prompt_id}_{uuid.uuid4().hex[:8]}.{ext}"


class Recording(models.Model):
    STATUS = [("pending", "Pending review"), ("approved", "Approved"), ("rejected", "Rejected")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    speaker = models.ForeignKey(Speaker, on_delete=models.CASCADE, related_name="recordings")
    prompt = models.ForeignKey(Prompt, on_delete=models.PROTECT, related_name="recordings")
    audio = models.FileField(upload_to=audio_upload_path)
    mime_type = models.CharField(max_length=60)
    duration_ms = models.PositiveIntegerField()
    peak_level = models.FloatField(help_text="0–1, from the browser analyser")
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    transcript_override = models.TextField(
        blank=True, help_text="Verbatim transcript for elicited prompts, or a correction if the speaker deviated.")
    review_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def label(self):
        return self.transcript_override or self.prompt.transcript
