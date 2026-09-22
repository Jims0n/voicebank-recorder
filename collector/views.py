import random

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ConsentForm, ProfileForm, ResumeForm
from .models import Prompt, Recording, Speaker

ALLOWED_MIME = ("audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav")
EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav"}


def current_speaker(request):
    sid = request.session.get("speaker_id")
    if not sid:
        return None
    return Speaker.objects.filter(id=sid, withdrawn=False).first()


def home(request):
    form = ResumeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sp = Speaker.objects.filter(code=form.cleaned_data["code"], withdrawn=False).first()
        if sp:
            request.session["speaker_id"] = str(sp.id)
            request.session["batch_start"] = sp.recordings.count()
            return redirect("record")
        form.add_error("code", "That code wasn't found. Check it and try again.")
    return render(request, "collector/home.html", {"form": form, "speaker": current_speaker(request)})


def consent(request):
    form = ConsentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.session["consented_at"] = timezone.now().isoformat()
        return redirect("profile")
    return render(request, "collector/consent.html",
                  {"form": form, "version": settings.CONSENT_VERSION})


def profile(request):
    if "consented_at" not in request.session:
        return redirect("consent")
    form = ProfileForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sp = form.save(commit=False)
        sp.consent_version = settings.CONSENT_VERSION
        sp.consented_at = request.session["consented_at"]
        sp.user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
        sp.save()
        request.session["speaker_id"] = str(sp.id)
        request.session["batch_start"] = 0
        return redirect("record")
    return render(request, "collector/profile.html", {"form": form})


def record(request):
    sp = current_speaker(request)
    if not sp:
        return redirect("home")
    return render(request, "collector/record.html", {
        "speaker": sp, "batch_size": settings.BATCH_SIZE,
        "min_ms": settings.MIN_DURATION_MS, "max_ms": settings.MAX_DURATION_MS,
    })


def done(request):
    sp = current_speaker(request)
    if not sp:
        return redirect("home")
    return render(request, "collector/done.html", {
        "speaker": sp, "total": sp.recordings.count(),
        "can_continue": sp.recordings.count() < settings.MAX_PER_SPEAKER,
    })


@require_POST
def next_batch(request):
    sp = current_speaker(request)
    if sp:
        request.session["batch_start"] = sp.recordings.count()
    return redirect("record")


def withdraw(request):
    form = ResumeForm(request.POST or None)
    done_msg = None
    if request.method == "POST" and form.is_valid():
        sp = Speaker.objects.filter(code=form.cleaned_data["code"]).first()
        if sp:
            with transaction.atomic():
                for r in sp.recordings.all():
                    r.audio.delete(save=False)
                    Prompt.objects.filter(id=r.prompt_id).update(recording_count=F("recording_count") - 1)
                sp.recordings.all().delete()
                sp.withdrawn = True
                sp.save(update_fields=["withdrawn"])
            request.session.flush()
            done_msg = "Your recordings have been deleted and you have been withdrawn from the study."
        else:
            form.add_error("code", "That code wasn't found.")
    return render(request, "collector/withdraw.html", {"form": form, "done_msg": done_msg})


# ---------- JSON API used by recorder.js ----------

def _pick_prompt(sp, skipped):
    done_ids = sp.recordings.values_list("prompt_id", flat=True)
    base = Prompt.objects.filter(active=True).exclude(id__in=done_ids).exclude(id__in=skipped)

    # Language weighting: mostly-X speakers get ~75% X, "both" gets 50/50.
    p_pidgin = {"pidgin": 0.75, "english": 0.25, "both": 0.5}[sp.language_pref]
    lang = "pidgin" if random.random() < p_pidgin else "english"
    mode = "elicited" if random.random() < settings.ELICITED_SHARE else "read"

    # Least-recorded first, so coverage stays balanced across prompts.
    for qs in (base.filter(language=lang, mode=mode), base.filter(language=lang), base):
        p = qs.order_by("recording_count", "?").first()
        if p:
            return p
    return None


def _prompt_payload(request, sp):
    total = sp.recordings.count()
    in_batch = total - request.session.get("batch_start", 0)
    if in_batch >= settings.BATCH_SIZE or total >= settings.MAX_PER_SPEAKER:
        return {"finished": True}
    p = _pick_prompt(sp, request.session.get("skipped", []))
    if not p:
        return {"finished": True}
    return {"finished": False, "id": p.id, "mode": p.mode, "language": p.language,
            "text": p.display_text, "progress": in_batch, "batch_size": settings.BATCH_SIZE}


def api_next(request):
    sp = current_speaker(request)
    if not sp:
        return JsonResponse({"error": "no session"}, status=403)
    return JsonResponse(_prompt_payload(request, sp))


@require_POST
def api_skip(request):
    sp = current_speaker(request)
    if not sp:
        return JsonResponse({"error": "no session"}, status=403)
    skipped = request.session.get("skipped", [])
    try:
        skipped.append(int(request.POST["prompt_id"]))
    except (KeyError, ValueError):
        return JsonResponse({"error": "bad prompt_id"}, status=400)
    request.session["skipped"] = skipped[-200:]
    return JsonResponse(_prompt_payload(request, sp))


@require_POST
def api_upload(request):
    sp = current_speaker(request)
    if not sp:
        return JsonResponse({"error": "no session"}, status=403)
    f = request.FILES.get("audio")
    try:
        prompt = Prompt.objects.get(id=int(request.POST["prompt_id"]), active=True)
        duration = int(float(request.POST["duration_ms"]))
        peak = float(request.POST["peak"])
    except (KeyError, ValueError, Prompt.DoesNotExist):
        return JsonResponse({"error": "bad request"}, status=400)

    mime = (f.content_type if f else "").split(";")[0]
    if not f or mime not in ALLOWED_MIME:
        return JsonResponse({"error": f"unsupported audio type {mime}"}, status=400)
    if not settings.MIN_DURATION_MS <= duration <= settings.MAX_DURATION_MS:
        return JsonResponse({"error": "recording too short or too long"}, status=400)
    if sp.recordings.filter(prompt=prompt).exists():
        return JsonResponse(_prompt_payload(request, sp))  # double-submit; just move on

    f.name = f"clip.{EXT[mime]}"
    with transaction.atomic():
        Recording.objects.create(speaker=sp, prompt=prompt, audio=f, mime_type=mime,
                                 duration_ms=duration, peak_level=peak)
        Prompt.objects.filter(id=prompt.id).update(recording_count=F("recording_count") + 1)
    return JsonResponse(_prompt_payload(request, sp))
