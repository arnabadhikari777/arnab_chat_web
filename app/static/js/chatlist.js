/* The chat list (Messages page). Draws /api/chats and refreshes it by polling. */
(function () {
  "use strict";
  const list = document.getElementById("chatlist");
  if (!list) return;
  const empty = document.getElementById("chatlist-empty");
  const errorBox = document.getElementById("chatlist-error");
  const active = Number(document.body.dataset.pollActive) || 2500;
  const idle = Number(document.body.dataset.pollIdle) || 12000;
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

  async function tick() {
    await window.app.ready;
    const res = await window.app.api("/api/chats");
    errorBox.classList.toggle("hidden", res.ok);
    if (res.ok) render(res.data);
    setTimeout(tick, document.hidden ? idle : active);
  }
  tick();
})();
