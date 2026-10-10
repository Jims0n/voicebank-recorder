from django.contrib import admin
from django.db.models import Count, Q, Sum
from django.utils.html import format_html

from .models import DRAFT_MARK, Prompt, Recording, Speaker

# Set from the observed distribution (n=537): p05 0.029, median 0.122, p90 0.491.
# Sits at the 5th percentile so it flags the genuinely quiet tail, not every clip.
QUIET_PEAK = 0.03


class AudioQualityFilter(admin.SimpleListFilter):
    """Triage hundreds of clips without listening to every one."""
    title = "audio quality"
    parameter_name = "quality"

    def lookups(self, request, model_admin):
        return [("clipped", "Clipped / distorted"), ("quiet", "Too quiet"),
                ("short", "Very short"), ("nometer", "No level reading"), ("ok", "No flags")]

    def queryset(self, request, qs):
        flags = (Q(clip_fraction__gt=0.02) | Q(peak_level__lt=QUIET_PEAK)
                 | Q(duration_ms__lt=1000))
        return {
            "clipped": qs.filter(clip_fraction__gt=0.02),
            "quiet": qs.filter(peak_level__gt=0, peak_level__lt=QUIET_PEAK),
            "short": qs.filter(duration_ms__lt=1000),
            "nometer": qs.filter(peak_level=0),
            "ok": qs.exclude(flags),
        }.get(self.value(), qs)


class NeedsTranscriptFilter(admin.SimpleListFilter):
    """Elicited clips carry no transcript until someone types one."""
    title = "transcript"
    parameter_name = "needs_transcript"

    def lookups(self, request, model_admin):
        return [("yes", "Missing (elicited)"), ("draft", "Whisper draft, unchecked"),
                ("no", "Present")]

    def queryset(self, request, qs):
        missing = Q(transcript_override="") & Q(prompt__transcript="")
        if self.value() == "yes":
            return qs.filter(missing)
        if self.value() == "draft":
            return qs.filter(review_note__startswith=DRAFT_MARK)
        if self.value() == "no":
            return qs.exclude(missing)
        return qs


@admin.register(Speaker)
class SpeakerAdmin(admin.ModelAdmin):
    list_display = ("code", "gender", "age_band", "first_language", "state",
                    "language_pref", "environment", "n_recordings", "withdrawn", "created_at")
    list_filter = ("gender", "first_language", "language_pref", "environment", "withdrawn")
    search_fields = ("code", "state")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_n=Count("recordings"))

    @admin.display(ordering="_n", description="recordings")
    def n_recordings(self, obj):
        return obj._n


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    list_display = ("id", "mode", "language", "intent", "display_text", "recording_count", "active")
    list_filter = ("mode", "language", "intent", "active")
    search_fields = ("display_text", "transcript")


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    list_display = ("created_at", "speaker", "prompt_text", "expected", "player", "duration_ms",
                    "quality", "status", "transcript_override")
    list_editable = ("status", "transcript_override")
    list_filter = ("status", AudioQualityFilter, NeedsTranscriptFilter, "prompt__mode",
                   "prompt__language", "prompt__intent", "speaker__gender")
    search_fields = ("speaker__code", "prompt__display_text", "transcript_override")
    list_per_page = 50
    list_select_related = ("speaker", "prompt")
    actions = ["approve", "reject"]

    def prompt_text(self, obj):
        return obj.prompt.display_text

    @admin.display(description="ground truth")
    def expected(self, obj):
        # Lets a reviewer check the spoken amount against what the prompt asked for.
        ents = {k: v for k, v in (obj.prompt.entities or {}).items() if k != "intent"}
        return format_html("<small>{}</small>",
                           ", ".join(f"{k}={v}" for k, v in ents.items()) or "—")

    @admin.display(description="quality", ordering="peak_level")
    def quality(self, obj):
        flags = []
        if obj.clip_fraction > 0.02:
            flags.append("clipped")
        if 0 < obj.peak_level < QUIET_PEAK:
            flags.append("quiet")
        if obj.peak_level == 0:
            flags.append("no meter")
        if obj.duration_ms < 1000:
            flags.append("short")
        return format_html(
            '<span style="color:{}">{}</span><br><small>peak {:.3f}</small>',
            "#d7263d" if flags else "#0b7a55", ", ".join(flags) or "ok", obj.peak_level)

    def player(self, obj):
        return format_html('<audio controls preload="none" src="{}" style="height:32px"></audio>', obj.audio.url)

    @admin.action(description="Approve selected")
    def approve(self, request, qs):
        qs.update(status="approved")

    def save_model(self, request, obj, form, change):
        # Editing or re-statusing a row by hand means a human has checked the draft.
        # Bulk actions bypass this on purpose, so they never clear the marker.
        if form.changed_data and obj.review_note.startswith(DRAFT_MARK):
            obj.review_note = ""
        super().save_model(request, obj, form, change)

    @admin.action(description="Reject selected")
    def reject(self, request, qs):
        qs.update(status="rejected")

    def changelist_view(self, request, extra_context=None):
        agg = Recording.objects.exclude(status="rejected").exclude(speaker__withdrawn=True) \
            .aggregate(ms=Sum("duration_ms"), n=Count("id"))
        hours = (agg["ms"] or 0) / 3_600_000
        extra_context = {**(extra_context or {}),
                         "title": f"Recordings: {agg['n']} usable clips, {hours:.2f} hours"}
        return super().changelist_view(request, extra_context)
