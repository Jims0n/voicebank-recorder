from django.contrib import admin
from django.db.models import Count, Sum
from django.utils.html import format_html

from .models import Prompt, Recording, Speaker


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
                    "peak_level", "status", "transcript_override")
    list_editable = ("status", "transcript_override")
    list_filter = ("status", "prompt__mode", "prompt__language", "prompt__intent", "speaker__gender")
    search_fields = ("speaker__code", "prompt__display_text", "transcript_override")
    list_per_page = 50
    actions = ["approve", "reject"]

    def prompt_text(self, obj):
        return obj.prompt.display_text

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
