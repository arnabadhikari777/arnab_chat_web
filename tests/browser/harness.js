const { JSDOM, CookieJar } = require("jsdom");
const BASE = "http://127.0.0.1:5000";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
const check = (name, ok) => { console.log((ok ? "PASS " : "FAIL ") + name); if (!ok) fails.push(name); };

class Browser {                       // a tiny cookie-keeping HTTP client (a "second phone")
  constructor() { this.jar = new CookieJar(); }
  async req(path, opts = {}) {
    const cookie = this.jar.getCookieStringSync(BASE + path);
    const res = await fetch(BASE + path, { redirect: "manual", ...opts, headers: { ...(opts.headers || {}), cookie } });
    (res.headers.getSetCookie() || []).forEach((c) => this.jar.setCookieSync(c, BASE + path));
    return res;
  }
  async login(user) {
    const page = await (await this.req("/login")).text();
    const token = page.match(/name="csrf_token" value="([^"]+)"/)[1];
    const res = await this.req("/login", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ csrf_token: token, identifier: user, password: "secret123" }) });
    if (res.status !== 302) throw new Error("login failed " + res.status);
    this.csrf = (await (await this.req("/")).text()).match(/name="csrf-token" content="([^"]+)"/)[1];
  }
  json(path, body) {
    return this.req(path, { method: "POST", headers: { "content-type": "application/json", "x-csrftoken": this.csrf, accept: "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());
  }
  get(path) { return this.req(path, { headers: { accept: "application/json" } }).then((r) => r.json()); }
}

async function open(browser, path) {
  const errors = [];
  const dom = await JSDOM.fromURL(BASE + path, {
    cookieJar: browser.jar, runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = (url, opts = {}) => {
        const u = new URL(url, BASE).href;
        const cookie = browser.jar.getCookieStringSync(u);
        return fetch(u, { ...opts, headers: { ...(opts.headers || {}), cookie }, redirect: "manual" });
      };
      window.addEventListener("error", (e) => errors.push(e.message));
      window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      window.HTMLDialogElement.prototype.close = function () { this.open = false; };
      window.document.hasFocus = () => true;
    },
  });
  await sleep(1800);
  dom.errors = errors;
  return dom;
}

(async () => {
  const alice = new Browser(), bob = new Browser();
  await alice.login("alice"); await bob.login("bob");
  // alice opens a chat with bob (public) through the real endpoint
  const r = await alice.req("/u/bob/message", { method: "POST", headers: { "x-csrftoken": alice.csrf } });
  const chatPath = r.headers.get("location");
  check("alice is sent to a chat page", /^\/chat\/\d+$/.test(chatPath));
  const convId = chatPath.split("/")[2];

  // bob sends two messages before alice opens the screen
  await bob.json(`/api/conversations/${convId}/messages`, { uuid: "bob-msg-0001", body: "Hi from Bob 👋" });
  await bob.json(`/api/conversations/${convId}/messages`, { uuid: "bob-msg-0002", body: "<img src=x onerror=alert(1)>" });

  const dom = await open(alice, chatPath);
  const doc = dom.window.document;
  const bubbles = () => [...doc.querySelectorAll(".msg")];
  check("no script errors on load", dom.errors.length === 0 || (console.log(dom.errors), false));
  check("history rendered (2 messages)", bubbles().length === 2);
  check("text shown as text, never as HTML", doc.querySelector("#chat-messages img") === null && doc.body.textContent.includes("<img src=x"));
  check("day separator drawn", doc.querySelectorAll(".day-sep").length === 1);
  check("header shows peer status", doc.getElementById("peer-status").textContent.trim().length > 0);
  check("mic shown, send hidden when empty", !doc.getElementById("btn-mic").classList.contains("hidden") && doc.getElementById("btn-send").classList.contains("hidden"));

  // alice's screen was visible, so bob's messages must become "seen" on the server
  await sleep(500);
  const bobView = await bob.get(`/api/conversations/${convId}/messages`);
  check("server marked bob's messages seen by alice's open screen", bobView.messages.every((m) => m.status === "seen"));

  // alice types and sends
  const input = doc.getElementById("msg-input");
  input.value = "Hello Bob";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  check("send button appears when typing", !doc.getElementById("btn-send").classList.contains("hidden"));
  doc.getElementById("composer").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
  await sleep(300);
  check("my message appears at once", bubbles().length === 3);
  await sleep(1200);
  const mine = bubbles()[2];
  check("my message confirmed by the server (has an id)", mine.dataset.id && mine.dataset.id !== "");
  check("input cleared after send", input.value === "");
  const stored = await bob.get(`/api/conversations/${convId}/messages`);
  check("bob really received it exactly once", stored.messages.filter((m) => m.body === "Hello Bob").length === 1);

  // bob marks seen -> alice's tick turns blue through polling
  await bob.json(`/api/conversations/${convId}/seen`, {});
  await sleep(1600);
  check("tick turned blue after bob saw it", !!mine.querySelector(".tick--seen"));

  // bob sends a new message -> arrives by polling
  await bob.json(`/api/conversations/${convId}/messages`, { uuid: "bob-msg-0003", body: "live message" });
  await sleep(1600);
  check("new message arrived by polling", bubbles().length === 4 && bubbles()[3].textContent.includes("live message"));

  // typing indicator
  await bob.json(`/api/conversations/${convId}/typing`, { typing: true });
  await sleep(1200);
  check("typing indicator shows", doc.getElementById("peer-status").textContent.includes("typing"));

  // delete for everyone by bob -> tombstone on alice's screen
  const bobMsg = (await bob.get(`/api/conversations/${convId}/messages`)).messages.find((m) => m.uuid === "bob-msg-0003");
  await bob.json(`/api/messages/${bobMsg.id}/delete`, { scope: "everyone" });
  await sleep(1600);
  check("deleted-for-everyone shows a tombstone, text gone", bubbles()[3].textContent.includes("deleted") && !bubbles()[3].textContent.includes("live message"));

  // delete for me via the real UI path (API)
  const mineId = Number(mine.dataset.id);
  await alice.json(`/api/messages/${mineId}/delete`, { scope: "me" });
  check("delete-for-me leaves bob's copy", (await bob.get(`/api/conversations/${convId}/messages`)).messages.some((m) => m.uuid === mine.dataset.uuid));

  // emoji panel + attach menu toggles
  doc.getElementById("btn-emoji").click();
  check("emoji panel opens with emoji", !doc.getElementById("emoji-panel").classList.contains("hidden") && doc.querySelectorAll(".emoji").length > 40);
  doc.querySelector(".emoji").click();
  check("emoji inserted into the input", input.value.length > 0);
  doc.getElementById("btn-attach").click();
  check("attach menu opens", !doc.getElementById("attach-menu").classList.contains("hidden"));

  // voice button without MediaRecorder -> friendly message, no crash
  doc.getElementById("btn-mic").click();
  await sleep(300);
  check("voice without microphone support shows a message", !![...doc.querySelectorAll(".toast")].find((t) => t.textContent.length > 5));
  check("still no script errors", dom.errors.length === 0 || (console.log(dom.errors), false));
  dom.window.close();

  // ---- chat list + badges on the Messages page (bob has 0 unread, alice has none either; make one)
  await bob.json(`/api/conversations/${convId}/messages`, { uuid: "bob-msg-0004", body: "unread for alice" });
  const list = await open(alice, "/messages");
  const ld = list.window.document;
  check("chat list shows the chat", ld.querySelectorAll("#chatlist a.list-item").length === 1);
  check("chat list shows unread badge", !!ld.querySelector("#chatlist .badge"));
  check("nav badge shows unread count", (() => { const b = ld.querySelector('.bottom-nav [data-badge="messages"]'); return b && !b.classList.contains("hidden") && b.textContent === "1"; })());
  check("no script errors on the list page", list.errors.length === 0 || (console.log(list.errors), false));
  list.window.close();

  console.log(fails.length ? "\nFAILED: " + fails.join(", ") : "\nALL BROWSER CHECKS PASSED");
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error("HARNESS ERROR", e); process.exit(2); });
