"""
Dataset health check. Run against whichever database you want to inspect:

    DATABASE_URL='<neon url>' python manage.py dataset_stats

Reports the numbers that decide whether the collection is usable: level
distribution, speaker balance, intent coverage, and review backlog.
"""
import statistics as st
from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from collector.models import Prompt, Recording, Speaker


def pct(values, p):
    return values[min(len(values) - 1, len(values) * p // 100)]


class Command(BaseCommand):
    help = "Print dataset composition, audio levels and review backlog."

    def head(self, text):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{text}"))

    def handle(self, *a, **o):
        live = Recording.objects.filter(speaker__withdrawn=False)
        total = live.count()
        if not total:
            self.stdout.write("no recordings yet")
            return

        ms = live.aggregate(s=Sum("duration_ms"))["s"] or 0
        speakers = Speaker.objects.filter(withdrawn=False).count()
        self.head("TOTALS")
        self.stdout.write(f"  clips          : {total}")
        self.stdout.write(f"  speakers       : {speakers}")
        self.stdout.write(f"  audio          : {ms / 3_600_000:.2f} hours")
        self.stdout.write(f"  mean clip      : {ms / total / 1000:.1f} s")

        self.head("AUDIO LEVEL  (peak, fraction of full scale)")
        peaks = sorted(live.values_list("peak_level", flat=True))
        zeros = sum(1 for p in peaks if p == 0)
        nonzero = [p for p in peaks if p > 0]
        self.stdout.write(f"  zero / no meter: {zeros}  ({100 * zeros / total:.1f}%)")
        if nonzero:
            for label, p in (("p05", 5), ("p10", 10), ("p50", 50), ("p90", 90)):
                self.stdout.write(f"  {label}            : {pct(nonzero, p):.4f}")
            self.stdout.write(f"  max            : {nonzero[-1]:.4f}")
            self.stdout.write(f"  clipped >2%    : {live.filter(clip_fraction__gt=0.02).count()}")

        # If the zero-peak clips all share one container, the browser analyser is the
        # cause rather than the microphone.
        self.head("ZERO-LEVEL CLIPS BY CONTAINER")
        by_mime = Counter(live.values_list("mime_type", flat=True))
        zero_mime = Counter(live.filter(peak_level=0).values_list("mime_type", flat=True))
        for mime, n in by_mime.most_common():
            self.stdout.write(f"  {mime:<16}: {n:>5} clips, {zero_mime.get(mime, 0):>5} with no level")

        self.head("CLIPS PER SPEAKER")
        counts = sorted(c for c in Speaker.objects.filter(withdrawn=False)
                        .annotate(n=Count("recordings")).values_list("n", flat=True) if c)
        if counts:
            self.stdout.write(f"  min / median / max: {counts[0]} / {int(st.median(counts))} / {counts[-1]}")
            self.stdout.write(f"  largest speaker is {100 * counts[-1] / total:.1f}% of the dataset")

        self.head("COVERAGE")
        for field in ("prompt__language", "prompt__mode", "prompt__intent"):
            rows = live.values(field).annotate(n=Count("id")).order_by("-n")
            parts = [f"{r[field]} {r['n']} ({100 * r['n'] / total:.0f}%)" for r in rows]
            self.stdout.write(f"  {field.split('__')[1]:<9}: " + ", ".join(parts))

        self.head("SPEAKER MIX")
        for field in ("gender", "age_band", "first_language", "language_pref", "environment", "state"):
            rows = (Speaker.objects.filter(withdrawn=False).values(field)
                    .annotate(n=Count("id")).order_by("-n"))
            self.stdout.write(f"  {field:<15}: " + ", ".join(f"{r[field]} {r['n']}" for r in rows))

        self.head("REVIEW BACKLOG")
        for row in live.values("status").annotate(n=Count("id")).order_by("-n"):
            self.stdout.write(f"  {row['status']:<15}: {row['n']}")
        untranscribed = live.filter(transcript_override="", prompt__transcript="").count()
        self.stdout.write(f"  need transcript: {untranscribed}  (elicited clips with no label yet)")

        self.head("PROMPTS NOT YET RECORDED")
        unused = Prompt.objects.filter(active=True, recording_count=0).count()
        self.stdout.write(f"  {unused} of {Prompt.objects.filter(active=True).count()} active prompts\n")
