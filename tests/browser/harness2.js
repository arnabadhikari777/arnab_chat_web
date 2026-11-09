const { JSDOM, BASE, sleep, fails, check, Browser } = require("./lib.js");
const EBML = [0x1a, 0x45, 0xdf, 0xa3, 1, 2, 3, 4, 5, 6, 7, 8];
const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0, 0, 0, 0, 0];
const PDF = [0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 10, 10, 10, 10];

async function openChat(browser, path, { mic = true } = {}) {
  const errors = [];
  const dom = await JSDOM.fromURL(BASE + path, {
    cookieJar: browser.jar, runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (url, opts = {}) => { const u = new URL(url, BASE).href; return fetch(u, { ...opts, headers: { ...(opts.headers || {}), cookie: browser.jar.getCookieStringSync(u) }, redirect: "manual" }); };
      w.addEventListener("error", (e) => errors.push(e.message));
      w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      w.HTMLDialogElement.prototype.close = function () { this.open = false; };
      w.document.hasFocus = () => true;
      w.URL.createObjectURL = () => "blob:fake"; w.URL.revokeObjectURL = () => {};
      w.HTMLMediaElement.prototype.play = () => Promise.resolve(); w.HTMLMediaElement.prototype.pause = () => {};
      if (mic) {
        w.navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [{ stop() { w.__micOff = true; } }] }) };
        w.MediaRecorder = class {
          static isTypeSupported() { return true; }
          constructor() { this.state = "inactive"; this.mimeType = "audio/webm;codecs=opus"; }
          start() { this.state = "recording"; w.__micOff = false; }
          pause() { this.state = "paused"; } resume() { this.state = "recording"; }
          stop() { this.state = "inactive"; setTimeout(() => { this.ondataavailable && this.ondataavailable({ data: new w.Blob([new Uint8Array(EBML)], { type: "audio/webm" }) }); this.onstop && this.onstop(); }, 10); }
        };
      } else { delete w.navigator.mediaDevices; }
    },
  });
  await sleep(1500); dom.errors = errors; return dom;
}
const file = (w, bytes, name, type) => new w.File([new Uint8Array(bytes)], name, { type });
function pick(w, input, f) { Object.defineProperty(input, "files", { value: [f], configurable: true }); input.dispatchEvent(new w.Event("change", { bubbles: true })); }

(async () => {
  const alice = new Browser(), bob = new Browser();
  await alice.login("alice"); await bob.login("bob");
  const path = (await alice.req("/u/bob/message", { method: "POST", headers: { "x-csrftoken": alice.csrf } })).headers.get("location");
  const convId = path.split("/")[2];
  const dom = await openChat(alice, path); const w = dom.window, doc = w.document;
  const $ = (id) => doc.getElementById(id);
  const vis = (id) => !$(id).classList.contains("hidden");
  const bubbles = () => [...doc.querySelectorAll(".msg")];
  const bobList = async () => (await bob.get(`/api/conversations/${convId}/messages`)).messages;
  const before = bubbles().length;
  const serverBefore = (await bobList()).length;

  // ---------------- voice: record -> pause -> resume -> stop -> preview -> delete
  $("btn-mic").click(); await sleep(400);
  check("recording UI opens", vis("composer-voice") && !vis("composer-default") && vis("vr-recording") && vis("vr-pause") && vis("vr-stop") && !vis("vr-send"));
  await sleep(1300);
  check("timer runs", $("vr-time").textContent !== "0:00");
  $("vr-pause").click(); check("paused state shown", $("vr-state").textContent.length > 0 && !$("vr-dot").classList.contains("vr-dot--live"));
  $("vr-pause").click(); check("resumed", $("vr-dot").classList.contains("vr-dot--live"));
  $("vr-stop").click(); await sleep(400);
  check("preview UI: play + seek + send + delete", vis("vr-preview") && vis("vr-send") && !vis("vr-stop") && !vis("vr-pause") && vis("vr-delete"));
  check("microphone released after stopping", w.__micOff === true);
  $("vr-play").click(); check("preview play toggles to pause icon", $("vr-play").querySelector("use").getAttribute("href") === "#i-pause");
  $("vr-delete").click(); await sleep(100);
  check("delete before sending returns to normal composer", vis("composer-default") && !vis("composer-voice"));
  check("nothing was sent after delete", (await bobList()).length === serverBefore);

  // ---------------- voice: record -> stop -> send
  $("btn-mic").click(); await sleep(1500); $("vr-stop").click(); await sleep(400); $("vr-send").click();
  await sleep(300);
  check("voice bubble appears at once", bubbles().length === before + 1);
  await sleep(1500);
  let m = (await bobList()).find((x) => x.type === "voice");
  check("voice message saved with attachment + duration", m && m.att && m.att.url.includes("/video/") && m.att.dur >= 1);
  check("voice bubble has a player", !!doc.querySelector(".msg .voice audio") && !!doc.querySelector(".msg .voice .vr-seek"));
  check("composer back to normal after sending", vis("composer-default"));

  // ---------------- image + pdf upload through the file inputs
  pick(w, $("file-media"), file(w, PNG, "pic.png", "image/png")); await sleep(1800);
  pick(w, $("file-doc"), file(w, PDF, "paper.pdf", "application/pdf")); await sleep(1800);
  const all = await bobList();
  check("image saved", all.some((x) => x.type === "image" && x.att.url.includes("/image/")));
  check("pdf saved", all.some((x) => x.type === "document" && x.att.name === "paper.pdf"));
  check("image thumbnail + file card shown", !!doc.querySelector(".msg .media img") && !!doc.querySelector(".msg a.filecard"));
  check("file card opens safely", doc.querySelector("a.filecard").rel.includes("noopener"));

  // bad file: server says no -> toast, no bubble left behind
  const count = bubbles().length;
  pick(w, $("file-doc"), file(w, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "evil.exe", "application/octet-stream")); await sleep(1500);
  check("refused file shows an error and leaves no bubble", bubbles().length === count && [...doc.querySelectorAll(".toast--error")].length > 0);

  // ---------------- delete UI: options sheet -> confirm -> gone
  const mineBubble = bubbles().find((b) => b.classList.contains("msg--out") && b.querySelector(".bubble__more"));
  mineBubble.querySelector(".bubble__more").click();
  let dlg = doc.querySelector("dialog.dialog");
  const labels = [...dlg.querySelectorAll("button")].map((b) => b.textContent);
  check("options sheet offers both deletes + cancel", labels.length === 3 && labels.includes("Delete for me") && labels.includes("Delete for everyone"));
  [...dlg.querySelectorAll("button")].find((b) => b.textContent === "Delete for everyone").click(); await sleep(150);
  dlg = doc.querySelector("dialog.dialog");
  check("a confirmation dialog follows (destructive action)", !!dlg && dlg.querySelector("h3").textContent.includes("Delete"));
  [...dlg.querySelectorAll("button")].find((b) => b.className.includes("btn--danger")).click(); await sleep(1500);
  check("message became a tombstone", mineBubble.textContent.includes("deleted"));
  check("server agrees: deleted for everyone", (await bobList()).some((x) => x.deleted));

  // cancel path changes nothing
  const other = bubbles().find((b) => b.classList.contains("msg--out") && b.querySelector(".bubble__more"));
  other.querySelector(".bubble__more").click();
  [...doc.querySelectorAll("dialog.dialog button")].find((b) => b.textContent === "Cancel").click(); await sleep(150);
  check("cancel closes the sheet and deletes nothing", !doc.querySelector("dialog.dialog"));

  // bob cannot see a delete-for-everyone button on alice's message
  const bdom = await openChat(bob, path); const bd = bdom.window.document;
  const aliceMsgInBob = [...bd.querySelectorAll(".msg--in")].find((b) => b.querySelector(".bubble__more"));
  aliceMsgInBob.querySelector(".bubble__more").click();
  const bl = [...bd.querySelectorAll("dialog.dialog button")].map((b) => b.textContent);
  check("recipient only gets 'Delete for me'", bl.includes("Delete for me") && !bl.includes("Delete for everyone"));
  bdom.window.close();

  check("no script errors during all of the above", dom.errors.length === 0 || (console.log(dom.errors), false));
  dom.window.close();

  // ---------------- no microphone support
  const nomic = await openChat(alice, path, { mic: false });
  nomic.window.document.getElementById("btn-mic").click(); await sleep(400);
  const t = [...nomic.window.document.querySelectorAll(".toast")].map((x) => x.textContent).join();
  check("no-microphone browser gets a clear message", t.includes("not supported"));
  check("composer unchanged", !nomic.window.document.getElementById("btn-mic").classList.contains("hidden") && nomic.window.document.getElementById("composer-voice").classList.contains("hidden"));
  nomic.window.close();

  console.log(fails.length ? "\nFAILED: " + fails.join(", ") : "\nALL VOICE/MEDIA/DELETE CHECKS PASSED");
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error("HARNESS ERROR", e); process.exit(2); });
