/* The chat list (Messages page). Draws /api/chats and refreshes it by polling. */
(function () {
  "use strict";
  const list = document.getElementById("chatlist");
  if (!list) return;
  const empty = document.getElementById("chatlist-empty");
  const errorBox = document.getElementById("chatlist-error");
  const fast = Math.max(Number(document.body.dataset.pollActive) || 2500, Number(document.body.dataset.pollList) || 5000);
  const slow = Math.max(fast * 2, Number(document.body.dataset.pollIdle) || 12000);
  const COLORS = ["a", "b", "c", "d", "e"];
  let last = "";

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const icon = (name, cls) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon icon--sm " + (cls || ""));
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + name);
    svg.appendChild(use);
    return svg;
  };

  function previewText(chat) {
    const t = window.app.t, m = chat.last;
    if (!m) return t("chat.hello_preview");
    if (m.deleted) return t("chat.deleted");
    if (m.type === "text") return m.body;
    return t({ image: "chat.photo", video: "chat.video", voice: "chat.voice", document: "chat.document", file: "chat.file" }[m.type] || "chat.file");
  }

  function row(chat) {
    const a = el("a", "list-item" + (chat.unread ? " list-item--unread" : ""));
    a.href = "/chat/" + chat.id;
    const av = el("span", "avatar avatar--" + COLORS[chat.user.seed]);
    if (chat.user.avatar) {
      const img = el("img");
      img.src = chat.user.avatar; img.alt = ""; img.loading = "lazy";
      av.appendChild(img);
    } else {
      av.textContent = chat.user.name.slice(0, 1).toUpperCase();
    }
    if (chat.user.online) av.appendChild(el("span", "avatar__dot"));

    const main = el("span", "list-item__main");
    const top = el("span", "list-item__top");
    top.append(el("span", "list-item__name", chat.user.name),
               el("span", "list-item__time", chat.last ? window.app.fmtStamp(chat.last.at) : ""));
    const bottom = el("span", "list-item__bottom");
    const text = el("span", "list-item__text");
    if (chat.last && chat.last.mine && !chat.last.deleted) {
      text.appendChild(icon(chat.last.status === "sent" ? "i-check" : "i-check2",
                            chat.last.status === "seen" ? "tick--seen" : ""));
      text.append(" ");
    }
    text.append(previewText(chat));
    bottom.appendChild(text);
    if (chat.unread) bottom.appendChild(el("span", "badge", chat.unread > 99 ? "99+" : String(chat.unread)));
    main.append(top, bottom);
    a.append(av, main);
    return a;
  }

  function render(data) {
    const signature = JSON.stringify(data.chats);
    if (signature === last) return;
    last = signature;
    list.replaceChildren(...data.chats.map(row));
    list.classList.toggle("hidden", !data.chats.length);
    empty.classList.toggle("hidden", data.chats.length > 0);
  }

  /* One timer, one request at a time. Nothing is requested while the tab is hidden; when it is shown
     again the list refreshes at once. When nothing changes for a while the interval grows. */
  let timer = null, busy = false, failures = 0, unchanged = 0, ctl = null, gen = 0;
  function schedule() {
    clearTimeout(timer); timer = null;
    if (document.hidden) return;
    let ms = unchanged >= 6 ? slow : fast;
    if (failures) ms = Math.min(60000, ms * Math.pow(2, failures));
    timer = setTimeout(tick, ms);
  }
  async function tick() {
    clearTimeout(timer); timer = null;
    if (busy || document.hidden) return;
    busy = true;
    const mine = ++gen;
    ctl = new AbortController();
    const signal = ctl.signal;
    await window.app.ready;
    const res = await window.app.api("/api/chats", { signal });
    if (mine === gen) busy = false;
    if (signal.aborted || mine !== gen) return;
    if (res.status === 401) return;                   // signed out: stop for good
    errorBox.classList.toggle("hidden", res.ok);
    if (res.ok) {
      failures = 0;
      const before = last;
      render(res.data);
      unchanged = last === before ? unchanged + 1 : 0;
      if (window.appBadge) { window.appBadge.set("messages", res.data.total_unread); window.appBadge.postpone(); }
    } else failures = Math.min(failures + 1, 4);
    schedule();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(timer); timer = null; if (ctl) ctl.abort(); gen++; busy = false; }
    else { unchanged = 0; tick(); }
  });
  window.addEventListener("online", () => { unchanged = 0; tick(); });
  window.addEventListener("pageshow", (e) => { if (e.persisted) { unchanged = 0; tick(); } });
  tick();
})();
