import random

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ConsentForm, ProfileForm, ResumeForm
from .models import Prompt, Recording, Speaker

ALLOWED_MIME = ("audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav")
EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav"}


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")


def _code_guess_limited(request):
    """Throttle speaker-code guesses; a 6-character code is all that guards a speaker's data."""
    key = f"codetry:{_client_ip(request)}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, settings.CODE_ATTEMPT_WINDOW)
        count = 1
    return count > settings.CODE_ATTEMPT_LIMIT


def current_speaker(request):
    sid = request.session.get("speaker_id")
    if not sid:
        return None
    return Speaker.objects.filter(id=sid, withdrawn=False).first()


def home(request):
    form = ResumeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if _code_guess_limited(request):
            form.add_error(None, "Too many attempts. Please wait a few minutes and try again.")
        else:
            sp = Speaker.objects.filter(code=form.cleaned_data["code"], withdrawn=False).first()
            if sp:
                request.session.cycle_key()
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
    return render(request, "collector/consent.html", {
        "form": form,
        "version": settings.CONSENT_VERSION,
        "researcher_name": settings.RESEARCHER_NAME,
        "researcher_email": settings.RESEARCHER_EMAIL,
        "supervisor_name": settings.SUPERVISOR_NAME,
        "supervisor_email": settings.SUPERVISOR_EMAIL,
    })


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
        if _code_guess_limited(request):
            form.add_error(None, "Too many attempts. Please wait a few minutes and try again.")
            return render(request, "collector/withdraw.html", {"form": form, "done_msg": None})
        sp = Speaker.objects.filter(code=form.cleaned_data["code"]).first()
        if sp:
            with transaction.atomic():
                for prompt_id in sp.recordings.values_list("prompt_id", flat=True):
                    Prompt.objects.filter(id=prompt_id, recording_count__gt=0).update(
                        recording_count=F("recording_count") - 1)
                sp.recordings.all().delete()   # post_delete signal removes the audio files
                sp.withdrawn = True
                sp.withdrawn_at = timezone.now()
                sp.save(update_fields=["withdrawn", "withdrawn_at"])
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
        peak = min(1.0, max(0.0, float(request.POST["peak"])))
        clip_fraction = min(1.0, max(0.0, float(request.POST.get("clip_fraction", 0))))
    except (KeyError, ValueError, Prompt.DoesNotExist):
        return JsonResponse({"error": "bad request"}, status=400)

    mime = (f.content_type if f else "").split(";")[0]
    if not f or mime not in ALLOWED_MIME:
        return JsonResponse({"error": f"unsupported audio type {mime}"}, status=400)
    if f.size > settings.MAX_UPLOAD_BYTES:
        return JsonResponse({"error": "recording too large"}, status=400)
    if not settings.MIN_DURATION_MS <= duration <= settings.MAX_DURATION_MS:
        return JsonResponse({"error": "recording too short or too long"}, status=400)

    f.name = f"clip.{EXT[mime]}"
    try:
        with transaction.atomic():
            Recording.objects.create(speaker=sp, prompt=prompt, audio=f, mime_type=mime,
                                     duration_ms=duration, peak_level=peak,
                                     clip_fraction=clip_fraction)
            Prompt.objects.filter(id=prompt.id).update(recording_count=F("recording_count") + 1)
    except IntegrityError:
        pass   # double-submit of the same prompt; keep the first clip and move on
    return JsonResponse(_prompt_payload(request, sp))
