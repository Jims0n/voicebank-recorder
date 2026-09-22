(() => {
  const app = document.getElementById("app");
  const $ = (id) => document.getElementById(id);
  const MIN_MS = +app.dataset.minMs, MAX_MS = +app.dataset.maxMs;
  const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;

  let prompt = null, stream = null, recorder = null, chunks = [], blob = null;
  let t0 = 0, duration = 0, peak = 0, clipFrac = 0, raf = null, stopTimer = null;
  let audioCtx = null, analyser = null;

  // Pick a format the browser can record. Chrome/Android: webm/opus. iOS Safari: mp4/aac.
  const MIME = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
    .find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";

  function status(msg, isError = false) {
    $("status").textContent = msg;
    $("status").classList.toggle("error", isError);
  }

  function show(p) {
    if (p.finished) { location.href = app.dataset.done; return; }
    prompt = p;
    const elicited = p.mode === "elicited";
    document.querySelector(".prompt").classList.toggle("elicited", elicited);
    $("instruction").textContent = elicited
      ? `In your own words, in ${p.language === "pidgin" ? "Pidgin" : "English"}:`
      : "Read this aloud, the way you normally talk:";
    $("prompt").textContent = p.text;
    $("count").textContent = p.progress;
    $("bar").style.width = `${(100 * p.progress) / p.batch_size}%`;
    $("review").hidden = true;
    $("rec").hidden = false;
    blob = null;
    status("Tap the button and speak.");
  }

  async function api(url, body) {
    const res = await fetch(url, {
      method: body ? "POST" : "GET",
      headers: { "X-CSRFToken": csrf },
      body, credentials: "same-origin",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);
    return data;
  }

  async function ensureMic() {
    if (stream) return;
    // Browser processing OFF: we want the raw signal and add noise ourselves later.
    // Keep these identical in the deployed app so train and test audio match.
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 2048;
    audioCtx.createMediaStreamSource(stream).connect(analyser);
  }

  function meter() {
    const buf = new Float32Array(analyser.fftSize);
    let frames = 0, clipped = 0;
    const tick = () => {
      analyser.getFloatTimeDomainData(buf);
      let p = 0;
      for (const v of buf) p = Math.max(p, Math.abs(v));
      peak = Math.max(peak, p);
      frames++; if (p > 0.99) clipped++;
      clipFrac = clipped / frames;
      $("level").style.width = `${Math.min(100, p * 140)}%`;
      raf = requestAnimationFrame(tick);
    };
    tick();
  }

  async function start() {
    try { await ensureMic(); }
    catch { status("Microphone blocked. Allow microphone access in your browser settings, then reload.", true); return; }
    if (audioCtx.state === "suspended") await audioCtx.resume();
    chunks = []; peak = 0; clipFrac = 0;
    recorder = new MediaRecorder(stream, MIME ? { mimeType: MIME } : undefined);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = finish;
    recorder.start();
    t0 = performance.now();
    meter();
    $("rec").classList.add("recording");
    $("rec").setAttribute("aria-label", "Stop recording");
    status("Recording… tap again when you finish.");
    stopTimer = setTimeout(() => recorder.state === "recording" && recorder.stop(), MAX_MS);
  }

  function finish() {
    clearTimeout(stopTimer);
    cancelAnimationFrame(raf);
    $("level").style.width = "0";
    $("rec").classList.remove("recording");
    $("rec").setAttribute("aria-label", "Start recording");
    duration = performance.now() - t0;
    blob = new Blob(chunks, { type: recorder.mimeType || MIME || "audio/webm" });

    if (duration < MIN_MS) { status("That was too short. Try again.", true); blob = null; return; }
    if (peak < 0.02) { status("We couldn't hear anything. Check your mic and try again.", true); blob = null; return; }

    $("playback").src = URL.createObjectURL(blob);
    $("rec").hidden = true;
    $("review").hidden = false;
    status(clipFrac > 0.02
      ? "It sounds a bit loud and may be distorted. Hold the phone a little further away and record again if you can."
      : "Listen back. If it sounds right, save it.", clipFrac > 0.02);
  }

  $("rec").addEventListener("click", () => {
    if (recorder && recorder.state === "recording") recorder.stop(); else start();
  });

  $("redo").addEventListener("click", () => { $("review").hidden = true; $("rec").hidden = false; blob = null; start(); });

  $("save").addEventListener("click", async () => {
    if (!blob) return;
    $("save").disabled = true;
    status("Saving…");
    const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
    const fd = new FormData();
    fd.append("audio", blob, `clip.${ext}`);
    fd.append("prompt_id", prompt.id);
    fd.append("duration_ms", Math.round(duration));
    fd.append("peak", peak.toFixed(4));
    try { show(await api(app.dataset.upload, fd)); }
    catch (e) { status(`Couldn't save: ${e.message}. Check your connection and tap Save again.`, true); }
    finally { $("save").disabled = false; }
  });

  $("skip").addEventListener("click", async () => {
    const fd = new FormData(); fd.append("prompt_id", prompt.id);
    try { show(await api(app.dataset.skip, fd)); } catch (e) { status(e.message, true); }
  });

  if (!window.MediaRecorder || !navigator.mediaDevices) {
    status("This browser can't record audio. Open this page in Chrome.", true);
    $("rec").disabled = true;
  } else {
    api(app.dataset.next).then(show).catch((e) => status(e.message, true));
  }
})();
