import random
import re

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from collector import bank_templates as T
from collector.models import Prompt


def fake_nuban(rng):
    # Synthetic 10-digit number. Never collect or display real account numbers.
    return "".join(str(rng.randint(0, 9)) for _ in range(10))


def fill(pattern, intent, rng):
    """Fill a read template. Returns (transcript, entities)."""
    ents = {"intent": intent}
    out = pattern
    if "{amount}" in out:
        pool = T.AIRTIME_AMOUNTS if intent == "airtime" else T.AMOUNTS
        value, forms = rng.choice(pool)
        out = out.replace("{amount}", rng.choice(forms))
        ents["amount"] = value
    if "{name}" in out:
        name = rng.choice(T.NAMES)
        out = out.replace("{name}", name.lower())
        ents["recipient"] = name
    if "{bank}" in out:
        canon, spoken = rng.choice(T.BANKS)
        out = out.replace("{bank}", spoken)
        ents["bank"] = canon
    if "{acct}" in out:
        acct = fake_nuban(rng)
        out = out.replace("{acct}", " ".join(T.DIGITS[int(d)] for d in acct))
        ents["account"] = acct
    if "{network}" in out:
        canon, spoken = rng.choice(T.NETWORKS)
        out = out.replace("{network}", spoken)
        ents["network"] = canon
    if "{biller}" in out:
        canon, spoken = rng.choice(T.BILLERS)
        out = out.replace("{biller}", spoken)
        ents["biller"] = canon
    return re.sub(r"\s+", " ", out).strip(), ents


def display(transcript):
    # Readable version of the label: capitalise first letter and names.
    words = transcript.split()
    names = {n.lower() for n in T.NAMES}
    acronyms = {"gtb": "GTB", "uba": "UBA", "mtn": "MTN", "dstv": "DStv", "gotv": "GOtv",
                "ikedc": "IKEDC", "ekedc": "EKEDC"}
    words = [w.capitalize() if w in names else acronyms.get(w, w) for w in words]
    s = " ".join(words)
    return s[0].upper() + s[1:]


class Command(BaseCommand):
    help = "Generate concrete prompts from bank_templates.py (idempotent with --reset)."

    def add_arguments(self, p):
        p.add_argument("--per-template", type=int, default=8,
                       help="fills per read template that has slots")
        p.add_argument("--elicited-per-template", type=int, default=10)
        p.add_argument("--seed", type=int, default=2026)
        p.add_argument("--reset", action="store_true",
                       help="delete prompts that have no recordings first")

    @transaction.atomic
    def handle(self, *a, **o):
        rng = random.Random(o["seed"])
        erng = random.Random(o["seed"] + 1)
        if o["reset"]:
            deleted, _ = Prompt.objects.filter(recording_count=0).delete()
            self.stdout.write(f"deleted {deleted} unrecorded prompts")

        # Idempotency is by target count per (template, language), not by content.
        # fill() draws from a large random space, so a re-run would otherwise invent
        # fresh combinations that are not duplicates and pile them up forever.
        have = {(tid, lang): n for tid, lang, n in Prompt.objects
                .values_list("template_id", "language").annotate(n=Count("id"))}
        existing = set(Prompt.objects.values_list("transcript", flat=True))
        created = 0

        for tid, lang, intent, pattern in T.READ_TEMPLATES:
            target = o["per_template"] if "{" in pattern else 1
            need, tries = target - have.get((tid, lang), 0), 0
            while need > 0 and tries < target * 20:
                tries += 1
                transcript, ents = fill(pattern, intent, rng)
                if transcript in existing:
                    continue
                existing.add(transcript)
                need -= 1
                Prompt.objects.create(mode="read", language=lang, intent=intent, template_id=tid,
                                      display_text=display(transcript), transcript=transcript,
                                      entities=ents)
                created += 1

        for tid, intent, pattern in T.ELICITED_TEMPLATES:
            target = o["elicited_per_template"] if "{" in pattern else 1
            for lang in ("pidgin", "english"):
                for _ in range(max(0, target - have.get((tid, lang), 0))):
                    ents = {"intent": intent}
                    ctx = {}
                    if "{amount_fmt}" in pattern:
                        pool = T.AIRTIME_AMOUNTS if intent == "airtime" else T.AMOUNTS
                        v, _f = erng.choice(pool)
                        ctx["amount_fmt"] = f"{v:,}"
                        ents["amount"] = v
                    if "{name}" in pattern:
                        ctx["name"] = ents["recipient"] = erng.choice(T.NAMES)
                    if "{bank}" in pattern:
                        ctx["bank"] = ents["bank"] = erng.choice(T.BANKS)[0]
                    if "{acct_digits}" in pattern:
                        acct = fake_nuban(erng)
                        ctx["acct_digits"] = f"{acct[:4]} {acct[4:7]} {acct[7:]}"
                        ents["account"] = acct
                    if "{network}" in pattern:
                        ctx["network"] = ents["network"] = erng.choice(T.NETWORKS)[0]
                    if "{biller}" in pattern:
                        ctx["biller"] = ents["biller"] = erng.choice(T.BILLERS)[0]
                    Prompt.objects.create(mode="elicited", language=lang, intent=intent,
                                          template_id=tid, display_text=pattern.format(**ctx),
                                          transcript="", entities=ents)
                    created += 1

        self.stdout.write(self.style.SUCCESS(
            f"created {created} prompts; total now {Prompt.objects.count()}"))
