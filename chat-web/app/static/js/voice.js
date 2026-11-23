/* Voice recorder: a thin, careful wrapper around MediaRecorder. */
(function () {
  "use strict";
  const CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

  function pickMime() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    return CANDIDATES.find((c) => MediaRecorder.isTypeSupported(c)) || "";
  }
  function fail(code) { const e = new Error(code); e.code = code; return e; }

  class VoiceRecorder {
    static supported() {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    }
    constructor({ maxSeconds, onTick, onLimit }) {
      this.maxSeconds = maxSeconds; this.onTick = onTick; this.onLimit = onLimit;
      this.chunks = []; this.stream = null; this.rec = null;
      this.base = 0; this.since = 0; this.timer = null; this.state = "idle";
    }
    get elapsed() { return this.base + (this.state === "recording" ? (Date.now() - this.since) / 1000 : 0); }

    async start() {
      if (!VoiceRecorder.supported()) throw fail("unsupported");   // also true on plain http
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (e) {
        if (e.name === "NotAllowedError" || e.name === "SecurityError") throw fail("denied");
        if (e.name === "NotFoundError" || e.name === "OverconstrainedError") throw fail("missing");
        throw fail("failed");
      }
      try {
        const mime = pickMime();
        this.rec = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
      } catch (e) { this._release(); throw fail("failed"); }
      this.chunks = [];
      this.rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) this.chunks.push(ev.data); };
      this.rec.start(250);
      this.state = "recording"; this.base = 0; this.since = Date.now();
      this.timer = setInterval(() => {
        this.onTick && this.onTick(this.elapsed);
        if (this.elapsed >= this.maxSeconds) { this.onLimit && this.onLimit(); }
      }, 200);
    }
    pause() {
      if (this.state !== "recording") return;
      this.rec.pause(); this.base = this.elapsed; this.state = "paused";
    }
    resume() {
      if (this.state !== "paused") return;
      this.rec.resume(); this.since = Date.now(); this.state = "recording";
    }
    stop() {
      return new Promise((resolve, reject) => {
        if (!this.rec || this.state === "idle") return reject(fail("failed"));
        const seconds = this.elapsed;
        this.rec.onstop = () => {
          const type = this.rec.mimeType || "audio/webm";
          const blob = new Blob(this.chunks, { type });
          const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
          this._release();
          resolve({ blob, seconds, ext });
        };
        this.rec.stop();
      });
    }
    cancel() {
      if (this.rec && this.state !== "idle") { this.rec.onstop = null; try { this.rec.stop(); } catch (e) { /* already stopped */ } }
      this._release();
    }
    _release() {
      clearInterval(this.timer);
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());   // turns the mic light off
      this.stream = null; this.state = "idle";
    }
  }
  window.VoiceRecorder = VoiceRecorder;
})();
