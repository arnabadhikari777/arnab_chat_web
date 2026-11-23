// Package admin serves a small password-protected panel on the calling server for TURN provider control.
// It is not part of the chat website; secrets never appear in responses after save.
package admin

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"html/template"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/goppo/calling-server/internal/turn"
)

const cookieName = "goppo_admin_session"
const sessionTTL = 12 * time.Hour

type Server struct {
	password string // plaintext from env; compared with constant-time
	secret   []byte // for signing session cookies (derived from password + HMAC secret if provided)
	turn     *turn.Manager
	sessions sync.Map // token -> expiry unix
	tmpl     *template.Template
}

func New(adminPassword string, signingSeed []byte, tm *turn.Manager) *Server {
	seed := append([]byte(nil), signingSeed...)
	seed = append(seed, []byte(adminPassword)...)
	mac := hmac.New(sha256.New, []byte("goppo-admin-v1"))
	mac.Write(seed)
	s := &Server{
		password: adminPassword,
		secret:   mac.Sum(nil),
		turn:     tm,
	}
	s.tmpl = template.Must(template.New("admin").Parse(adminHTML))
	return s
}

func (s *Server) Enabled() bool {
	return s.password != ""
}

// Mount registers /admin and /admin/* on the mux. Caller should place this behind the same CORS/security wrapper or restrict carefully.
func (s *Server) Mount(mux *http.ServeMux) {
	if !s.Enabled() {
		mux.HandleFunc("/admin", func(w http.ResponseWriter, r *http.Request) {
			http.Error(w, "admin panel disabled (set ADMIN_PASSWORD)", http.StatusNotFound)
		})
		return
	}
	mux.HandleFunc("/admin", s.handlePage)
	mux.HandleFunc("/admin/", s.handlePage)
	mux.HandleFunc("/admin/login", s.handleLogin)
	mux.HandleFunc("/admin/logout", s.handleLogout)
	mux.HandleFunc("/admin/api/status", s.handleStatus)
	mux.HandleFunc("/admin/api/select", s.handleSelect)
	mux.HandleFunc("/admin/api/test", s.handleTest)
}

func (s *Server) handlePage(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/admin" && r.URL.Path != "/admin/" {
		http.NotFound(w, r)
		return
	}
	if !s.authed(r) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(loginHTML))
		return
	}
	st := s.turn.Status()
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = s.tmpl.Execute(w, st)
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	_ = r.ParseForm()
	pass := r.FormValue("password")
	if subtle.ConstantTimeCompare([]byte(pass), []byte(s.password)) != 1 {
		http.Error(w, "invalid password", http.StatusUnauthorized)
		return
	}
	tok, err := s.issueSession()
	if err != nil {
		http.Error(w, "session error", http.StatusInternalServerError)
		return
	}
	http.SetCookie(w, &http.Cookie{
		Name:     cookieName,
		Value:    tok,
		Path:     "/admin",
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
		Secure:   r.TLS != nil || strings.EqualFold(r.Header.Get("X-Forwarded-Proto"), "https"),
		MaxAge:   int(sessionTTL.Seconds()),
	})
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	if c, err := r.Cookie(cookieName); err == nil {
		s.sessions.Delete(c.Value)
	}
	http.SetCookie(w, &http.Cookie{Name: cookieName, Value: "", Path: "/admin", MaxAge: -1})
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	if !s.requireAuth(w, r) {
		return
	}
	writeJSON(w, 200, s.turn.Status())
}

func (s *Server) handleSelect(w http.ResponseWriter, r *http.Request) {
	if !s.requireAuth(w, r) {
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Provider string `json:"provider"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "bad json"})
		return
	}
	requireTest := body.Provider == turn.ProviderTurnix
	if err := s.turn.SetActive(body.Provider, requireTest); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "status": s.turn.Status()})
}

func (s *Server) handleTest(w http.ResponseWriter, r *http.Request) {
	if !s.requireAuth(w, r) {
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Provider string `json:"provider"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "bad json"})
		return
	}
	ok, msg := s.turn.TestProvider(r.Context(), body.Provider)
	writeJSON(w, 200, map[string]any{"ok": ok, "message": msg, "status": s.turn.Status()})
}

func (s *Server) requireAuth(w http.ResponseWriter, r *http.Request) bool {
	if !s.authed(r) {
		writeJSON(w, 401, map[string]any{"error": "unauthorized"})
		return false
	}
	return true
}

func (s *Server) authed(r *http.Request) bool {
	c, err := r.Cookie(cookieName)
	if err != nil || c.Value == "" {
		return false
	}
	expI, ok := s.sessions.Load(c.Value)
	if !ok {
		return false
	}
	exp, _ := expI.(int64)
	if time.Now().Unix() > exp {
		s.sessions.Delete(c.Value)
		return false
	}
	return true
}

func (s *Server) issueSession() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	tok := hex.EncodeToString(b)
	mac := hmac.New(sha256.New, s.secret)
	mac.Write([]byte(tok))
	signed := tok + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	s.sessions.Store(signed, time.Now().Add(sessionTTL).Unix())
	return signed, nil
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

const loginHTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Goppo Calling Admin</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f1419;color:#e7e9ea;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.card{background:#1a2332;padding:2rem;border-radius:12px;width:min(360px,92vw);box-shadow:0 8px 32px #0006}
h1{font-size:1.15rem;margin:0 0 1rem}
label{display:block;font-size:.85rem;margin-bottom:.35rem;color:#8b98a5}
input{width:100%;box-sizing:border-box;padding:.65rem .75rem;border-radius:8px;border:1px solid #38444d;background:#0f1419;color:#e7e9ea}
button{margin-top:1rem;width:100%;padding:.7rem;border:0;border-radius:8px;background:#1d9bf0;color:#fff;font-weight:600;cursor:pointer}
button:hover{background:#1a8cd8}
.note{font-size:.75rem;color:#8b98a5;margin-top:1rem}
</style>
</head>
<body>
<form class="card" method="post" action="/admin/login">
  <h1>Goppo Calling Admin</h1>
  <label for="password">Admin password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button>
  <p class="note">This panel lives on the Render calling server only. Tokens and passwords are never shown after save.</p>
</form>
</body>
</html>`

const adminHTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Goppo TURN Admin</title>
<style>
:root{--bg:#0f1419;--card:#1a2332;--border:#38444d;--text:#e7e9ea;--muted:#8b98a5;--accent:#1d9bf0;--ok:#00ba7c;--bad:#f4212e}
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--text);margin:0;padding:1.25rem}
.wrap{max-width:640px;margin:0 auto}
h1{font-size:1.25rem;margin:0 0 .25rem}
.sub{color:var(--muted);font-size:.85rem;margin-bottom:1.25rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.25rem;margin-bottom:1rem}
.row{display:flex;justify-content:space-between;align-items:center;gap:.75rem;padding:.5rem 0;border-bottom:1px solid var(--border)}
.row:last-child{border-bottom:0}
.label{color:var(--muted);font-size:.85rem}
.val{font-weight:600}
.badge{display:inline-block;padding:.2rem .55rem;border-radius:999px;font-size:.75rem;font-weight:600}
.badge.ok{background:#00ba7c22;color:var(--ok)}
.badge.bad{background:#f4212e22;color:var(--bad)}
.badge.neutral{background:#8b98a522;color:var(--muted)}
.actions{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem}
button,.btn{padding:.55rem 1rem;border-radius:8px;border:1px solid var(--border);background:#273340;color:var(--text);cursor:pointer;font-weight:600;font-size:.9rem}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button:disabled{opacity:.5;cursor:not-allowed}
#msg{margin-top:.75rem;font-size:.9rem;min-height:1.2em}
.note{font-size:.8rem;color:var(--muted);margin-top:1rem;line-height:1.4}
a.logout{float:right;color:var(--muted);font-size:.85rem}
</style>
</head>
<body>
<div class="wrap">
  <a class="logout" href="/admin/logout">Log out</a>
  <h1>TURN Provider Admin</h1>
  <p class="sub">Switch active provider without redeploying. Secrets are never displayed.</p>

  <div class="card">
    <div class="row"><span class="label">Current active</span><span class="val" id="active">{{.ActiveProvider}}</span></div>
    <div class="row"><span class="label">Metered configured</span><span id="metered" class="badge {{if .MeteredConfigured}}ok{{else}}bad{{end}}">{{if .MeteredConfigured}}yes{{else}}no{{end}}</span></div>
    <div class="row"><span class="label">Turnix configured</span><span id="turnix" class="badge {{if .TurnixConfigured}}ok{{else}}bad{{end}}">{{if .TurnixConfigured}}yes{{else}}no{{end}}</span></div>
    <div class="row"><span class="label">Settings persistent</span><span id="persist" class="badge {{if .Persistent}}ok{{else}}neutral{{end}}">{{if .Persistent}}yes{{else}}no (ephemeral){{end}}</span></div>
    <div class="row"><span class="label">Last test</span><span class="val" id="lasttest">{{if .LastTestProvider}}{{.LastTestProvider}}: {{if .LastTestOK}}OK{{else}}FAIL{{end}} — {{.LastTestMessage}}{{else}}—{{end}}</span></div>
  </div>

  <div class="card">
    <div class="actions">
      <button type="button" class="primary" data-action="select" data-provider="metered">Select Metered</button>
      <button type="button" class="primary" data-action="select" data-provider="turnix">Select Turnix</button>
      <button type="button" data-action="test" data-provider="metered">Test Metered</button>
      <button type="button" data-action="test" data-provider="turnix">Test Turnix</button>
      <button type="button" data-action="refresh">Refresh status</button>
    </div>
    <div id="msg"></div>
    {{if .Note}}<p class="note">{{.Note}}</p>{{end}}
    <p class="note">Turnix requires a successful Test before it can be selected. If Turnix fails at call time, the /ice endpoint returns an error (no silent STUN-only fallback when Turnix is active).</p>
  </div>
</div>
<script>
async function api(path, body) {
  const opts = { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } };
  if (body) { opts.method = 'POST'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok && data.ok !== false, status: res.status, data };
}
function paint(st) {
  if (!st) return;
  document.getElementById('active').textContent = st.active_provider || '—';
  const m = document.getElementById('metered');
  m.textContent = st.metered_configured ? 'yes' : 'no';
  m.className = 'badge ' + (st.metered_configured ? 'ok' : 'bad');
  const t = document.getElementById('turnix');
  t.textContent = st.turnix_configured ? 'yes' : 'no';
  t.className = 'badge ' + (st.turnix_configured ? 'ok' : 'bad');
  const p = document.getElementById('persist');
  p.textContent = st.persistent ? 'yes' : 'no (ephemeral)';
  p.className = 'badge ' + (st.persistent ? 'ok' : 'neutral');
  const lt = document.getElementById('lasttest');
  if (st.last_test_provider) {
    lt.textContent = st.last_test_provider + ': ' + (st.last_test_ok ? 'OK' : 'FAIL') + ' — ' + (st.last_test_message || '');
  } else { lt.textContent = '—'; }
}
document.querySelector('.actions').addEventListener('click', async (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;
  const msg = document.getElementById('msg');
  msg.textContent = 'Working…';
  const action = btn.dataset.action;
  const provider = btn.dataset.provider;
  try {
    if (action === 'refresh') {
      const r = await api('/admin/api/status');
      paint(r.data);
      msg.textContent = r.ok ? 'Status refreshed' : (r.data.error || 'Failed');
      return;
    }
    if (action === 'test') {
      const r = await api('/admin/api/test', { provider });
      paint(r.data.status || r.data);
      msg.textContent = r.data.message || (r.ok ? 'OK' : 'Failed');
      return;
    }
    if (action === 'select') {
      const r = await api('/admin/api/select', { provider });
      paint(r.data.status || r.data);
      msg.textContent = r.ok ? ('Active provider: ' + provider) : (r.data.error || 'Failed');
      return;
    }
  } catch (err) {
    msg.textContent = 'Network error';
  }
});
</script>
</body>
</html>
`
