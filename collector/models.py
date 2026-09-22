import secrets
import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

# 36 states + FCT. Fixed list so the demographic breakdown is analysable;
# free text fragments into "Lagos"/"lagos"/"LAG" and cannot be fixed later.
NIGERIAN_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
    "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "Federal Capital Territory",
    "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara",
    "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers",
    "Sokoto", "Taraba", "Yobe", "Zamfara",
]
STATE_CHOICES = [(s, s) for s in NIGERIAN_STATES] + [("outside", "Outside Nigeria")]


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
    state = models.CharField("state you grew up in", max_length=30, choices=STATE_CHOICES)
    language_pref = models.CharField(max_length=8, choices=LANG_PREF)
    environment = models.CharField(max_length=8, choices=ENV)
    user_agent = models.TextField(blank=True)
    withdrawn = models.BooleanField(default=False)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["withdrawn", "created_at"])]

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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Matches the hot query in views._pick_prompt: active + language + mode, least-recorded first.
        indexes = [models.Index(fields=["active", "language", "mode", "recording_count"])]
        constraints = [
            models.UniqueConstraint(
                fields=["transcript"], condition=models.Q(mode="read"),
                name="uniq_read_prompt_transcript"),
        ]

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
    audio = models.FileField(upload_to=audio_upload_path, max_length=200)
    mime_type = models.CharField(max_length=60)
    duration_ms = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    peak_level = models.FloatField(
        help_text="0–1, from the browser analyser",
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    clip_fraction = models.FloatField(
        default=0.0, help_text="share of analyser frames at full scale; high means distorted",
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    transcript_override = models.TextField(
        blank=True, help_text="Verbatim transcript for elicited prompts, or a correction if the speaker deviated.")
    review_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["speaker", "prompt"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["speaker", "prompt"], name="uniq_speaker_prompt"),
        ]

    def __str__(self):
        return f"{self.speaker.code} · prompt {self.prompt_id} · {self.status}"

    @property
    def label(self):
        return self.transcript_override or self.prompt.transcript


@receiver(post_delete, sender=Recording)
def delete_audio_file(sender, instance, **kwargs):
    # Without this, admin/withdrawal deletes leave the audio orphaned in the bucket.
    if instance.audio:
        instance.audio.delete(save=False)
