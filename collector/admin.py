from django.contrib import admin
from django.db.models import Count, Q, Sum
from django.utils.html import format_html

from .models import Prompt, Recording, Speaker


class AudioQualityFilter(admin.SimpleListFilter):
    """Triage hundreds of clips without listening to every one."""
    title = "audio quality"
    parameter_name = "quality"

    def lookups(self, request, model_admin):
        return [("clipped", "Clipped / distorted"), ("quiet", "Too quiet"),
                ("short", "Very short"), ("nometer", "No level reading"), ("ok", "No flags")]

    def queryset(self, request, qs):
        flags = (Q(clip_fraction__gt=0.02) | Q(peak_level__lt=0.05)
                 | Q(duration_ms__lt=1000))
        return {
            "clipped": qs.filter(clip_fraction__gt=0.02),
            "quiet": qs.filter(peak_level__gt=0, peak_level__lt=0.05),
            "short": qs.filter(duration_ms__lt=1000),
            "nometer": qs.filter(peak_level=0),
            "ok": qs.exclude(flags),
        }.get(self.value(), qs)


class NeedsTranscriptFilter(admin.SimpleListFilter):
    """Elicited clips carry no transcript until someone types one."""
    title = "transcript"
    parameter_name = "needs_transcript"

    def lookups(self, request, model_admin):
        return [("yes", "Missing (elicited)"), ("no", "Present")]

    def queryset(self, request, qs):
        missing = Q(transcript_override="") & Q(prompt__transcript="")
        if self.value() == "yes":
            return qs.filter(missing)
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
    list_display = ("created_at", "speaker", "prompt_text", "player", "duration_ms",
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

    @admin.display(description="quality")
    def quality(self, obj):
        flags = []
        if obj.clip_fraction > 0.02:
            flags.append("clipped")
        if 0 < obj.peak_level < 0.05:
            flags.append("quiet")
        if obj.peak_level == 0:
            flags.append("no meter")
        if obj.duration_ms < 1000:
            flags.append("short")
        if not flags:
            return format_html('<span style="color:#0b7a55">ok</span>')
        return format_html('<span style="color:#d7263d">{}</span>', ", ".join(flags))

    def player(self, obj):
        return format_html('<audio controls preload="none" src="{}" style="height:32px"></audio>', obj.audio.url)

    @admin.action(description="Approve selected")
    def approve(self, request, qs):
        qs.update(status="approved")

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
