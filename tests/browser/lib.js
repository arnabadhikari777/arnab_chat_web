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


module.exports = { JSDOM, CookieJar, BASE, sleep, fails, check, Browser };
