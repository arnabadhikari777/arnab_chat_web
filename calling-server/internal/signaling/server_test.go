package signaling

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/goppo/calling-server/internal/auth"
	"github.com/goppo/calling-server/internal/config"
	"github.com/goppo/calling-server/internal/native"
	"github.com/goppo/calling-server/internal/session"
	"github.com/gorilla/websocket"
)

const origin = "https://goppo.example"

var secret = []byte("0123456789abcdef0123456789abcdef")
var jtiCounter atomic.Int64

const sdp = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 AA:BB\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
const cand = "candidate:1 1 udp 2113937151 203.0.113.9 50000 typ host"

type env struct {
	ts  *httptest.Server
	mgr *session.Manager
	cfg *config.Config
}

func newEnv(t *testing.T, mut func(*config.Config)) *env {
	t.Helper()
	if err := native.Init(); err != nil {
		t.Fatal(err)
	}
	cfg := &config.Config{Secret: secret, AllowedOrigins: []string{origin, "http://localhost:5000"}, STUNURLs: []string{"stun:stun.example:3478"},
		TURNCredTTL: time.Hour, RequireNative: true, TokenMaxAge: 5 * time.Minute}
	if mut != nil {
		mut(cfg)
	}
	scfg := session.DefaultConfig()
	scfg.ResendAfter = 200 * time.Millisecond
	mgr := session.NewManager(scfg)
	srv := New(cfg, auth.NewValidator(secret, cfg.TokenMaxAge), mgr)
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	return &env{ts: ts, mgr: mgr, cfg: cfg}
}

func mint(callID string, uid, pid int64, role, ctype string) string {
	now := time.Now()
	return auth.Sign(secret, auth.Claims{Version: 1, UserID: uid, PeerID: pid, Conv: 1, CallType: ctype, CallID: callID, Role: role,
		JTI: fmt.Sprintf("jti-%016d", jtiCounter.Add(1)), IssuedAt: now.Unix(), Exp: now.Add(2 * time.Minute).Unix()})
}

type client struct {
	t   *testing.T
	c   *websocket.Conn
	in  chan map[string]any
	end chan error
}

func (e *env) dial(t *testing.T, token, org string) (*client, *http.Response, error) {
	d := websocket.Dialer{Subprotocols: []string{"goppo.v1", "auth." + token}, HandshakeTimeout: 3 * time.Second}
	h := http.Header{}
	if org != "" {
		h.Set("Origin", org)
	}
	c, resp, err := d.Dial("ws"+strings.TrimPrefix(e.ts.URL, "http")+"/ws", h)
	if err != nil {
		return nil, resp, err
	}
	t.Cleanup(func() { c.Close() })
	cl := &client{t: t, c: c, in: make(chan map[string]any, 256), end: make(chan error, 1)}
	go func() { // a dedicated reader: gorilla connections are unusable after any read error, including timeouts
		for {
			var m map[string]any
			if err := c.ReadJSON(&m); err != nil {
				cl.end <- err
				return
			}
			cl.in <- m
		}
	}()
	return cl, resp, nil
}

func (e *env) must(t *testing.T, token string) *client {
	c, resp, err := e.dial(t, token, origin)
	if err != nil {
		code := 0
		if resp != nil {
			code = resp.StatusCode
		}
		t.Fatalf("dial: %v (status %d)", err, code)
	}
	return c
}

func (c *client) send(v map[string]any) {
	if err := c.c.WriteJSON(v); err != nil {
		c.t.Fatal(err)
	}
}

// next returns the next message of the wanted type (other messages are skipped).
func (c *client) next(want string) map[string]any {
	c.t.Helper()
	timeout := time.After(3 * time.Second)
	for {
		select {
		case m := <-c.in:
			if m["type"] == want {
				return m
			}
		case err := <-c.end:
			c.end <- err
			for { // select picks randomly when both are ready: whatever arrived before the close must still be read
				select {
				case m := <-c.in:
					if m["type"] == want {
						return m
					}
					continue
				default:
				}
				break
			}
			c.t.Fatalf("connection ended while waiting for %q: %v", want, err)
		case <-timeout:
			c.t.Fatalf("timeout waiting for %q", want)
		}
	}
}

func (c *client) expectNone(want string, d time.Duration) {
	c.t.Helper()
	timeout := time.After(d)
	for {
		select {
		case m := <-c.in:
			if m["type"] == want {
				c.t.Fatalf("unexpected %q: %v", want, m)
			}
		case <-timeout:
			return
		}
	}
}

func payload(sdpText string) map[string]any { return map[string]any{"sdp": sdpText} }

func TestCORSExactOriginsOnly(t *testing.T) {
	e := newEnv(t, nil)
	get := func(path, org string) *http.Response {
		req, _ := http.NewRequest("GET", e.ts.URL+path, nil)
		if org != "" {
			req.Header.Set("Origin", org)
		}
		r, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	if r := get("/health", origin); r.Header.Get("Access-Control-Allow-Origin") != origin {
		t.Fatal("allowed origin must be echoed")
	}
	if r := get("/ready", "https://evil.example"); r.Header.Get("Access-Control-Allow-Origin") != "" {
		t.Fatal("unlisted origin must get no CORS header")
	}
	if r := get("/health", ""); r.StatusCode != 200 {
		t.Fatal("health check without Origin (Render) must work")
	}
	if r := get("/health", origin); r.Header.Get("Access-Control-Allow-Credentials") != "" {
		t.Fatal("no credentials are used, so none are allowed")
	}
	pre := func(org string) int {
		req, _ := http.NewRequest("OPTIONS", e.ts.URL+"/ice", nil)
		req.Header.Set("Origin", org)
		req.Header.Set("Access-Control-Request-Method", "GET")
		req.Header.Set("Access-Control-Request-Headers", "x-call-token")
		r, _ := http.DefaultClient.Do(req)
		return r.StatusCode
	}
	if pre(origin) != 204 || pre("https://evil.example") != 403 {
		t.Fatal("preflight: allowed 204, unlisted 403")
	}
	if pre("http://localhost:5000") != 204 {
		t.Fatal("local development origin must work")
	}
}

func TestHealthReadyComponentsICE(t *testing.T) {
	e := newEnv(t, func(c *config.Config) {
		c.TURNURLs = []string{"turn:t.example:3478?transport=udp", "turn:t.example:3478?transport=tcp", "turns:t.example:5349?transport=tcp"}
		c.TURNSharedSecret = "turn-secret"
		c.TURNStaticUser, c.TURNStaticPass = "perm-user", "perm-pass"
	})
	var body map[string]any
	getJSON := func(path, tok string) (int, map[string]any) {
		req, _ := http.NewRequest("GET", e.ts.URL+path, nil)
		if tok != "" {
			req.Header.Set("X-Call-Token", tok)
		}
		r, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		body = map[string]any{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		return r.StatusCode, body
	}
	if code, b := getJSON("/health", ""); code != 200 || b["status"] != "ok" {
		t.Fatal("health", code, b)
	}
	if code, b := getJSON("/ready", ""); code != 200 || b["ready"] != true || b["native_verified"] != true {
		t.Fatal("ready", code, b)
	}
	code, b := getJSON("/components", "")
	if code != 200 || b["all_verified"] != true {
		t.Fatal("components", code, b)
	}
	langs := map[string]bool{}
	for _, c := range b["components"].([]any) {
		m := c.(map[string]any)
		langs[m["language"].(string)] = m["loaded"] == true && m["verified"] == true
	}
	if !(langs["c"] && langs["c++"] && langs["rust"]) {
		t.Fatalf("all three native components must be loaded and verified: %v", langs)
	}
	if code, _ := getJSON("/ice", ""); code != 401 {
		t.Fatalf("/ice without token must be 401, got %d", code)
	}
	if code, _ := getJSON("/ice", "garbage"); code != 401 {
		t.Fatal("/ice with garbage token must be 401")
	}
	tok := mint("call-ice", 1, 2, "caller", "voice")
	code, b = getJSON("/ice", tok)
	if code != 200 {
		t.Fatal("ice", code, b)
	}
	if code, _ = getJSON("/ice", tok); code != 200 {
		t.Fatal("/ice must not consume the token")
	}
	raw, _ := json.Marshal(b)
	if strings.Contains(string(raw), "perm-pass") || strings.Contains(string(raw), "turn-secret") {
		t.Fatal("permanent credentials / shared secret must never be returned")
	}
	servers := b["iceServers"].([]any)
	turn := servers[len(servers)-1].(map[string]any)
	if len(turn["urls"].([]any)) != 3 || !strings.Contains(turn["username"].(string), ":1") || turn["credential"] == "" {
		t.Fatalf("turn entry %v", turn)
	}
}

func TestWebSocketAuthentication(t *testing.T) {
	e := newEnv(t, nil)
	if _, resp, err := e.dial(t, mint("c-auth", 1, 2, "caller", "voice"), "https://evil.example"); err == nil || resp.StatusCode != 403 {
		t.Fatal("unlisted origin must get 403")
	}
	if _, resp, err := e.dial(t, mint("c-auth", 1, 2, "caller", "voice"), ""); err == nil || resp.StatusCode != 403 {
		t.Fatal("missing origin must get 403")
	}
	if _, resp, err := e.dial(t, "", origin); err == nil || resp.StatusCode != 401 {
		t.Fatal("missing token must get 401")
	}
	forged := auth.Sign([]byte("another-secret-another-secret-xxxxx"), auth.Claims{Version: 1, UserID: 1, PeerID: 2, CallType: "voice",
		CallID: "c-auth", Role: "caller", JTI: "jti-forged-forged-1", IssuedAt: time.Now().Unix(), Exp: time.Now().Add(time.Minute).Unix()})
	if _, resp, err := e.dial(t, forged, origin); err == nil || resp.StatusCode != 401 {
		t.Fatal("forged token must get 401")
	}
	tok := mint("c-auth", 1, 2, "caller", "voice")
	c := e.must(t, tok)
	if w := c.next("welcome"); w["role"] != "caller" {
		t.Fatal(w)
	}
	if _, resp, err := e.dial(t, tok, origin); err == nil || resp.StatusCode != 401 {
		t.Fatal("replayed token must get 401")
	}
}

func TestRoleComesFromSignedTokenNotFromQuery(t *testing.T) {
	e := newEnv(t, nil)
	d := websocket.Dialer{Subprotocols: []string{"goppo.v1", "auth." + mint("c-role", 2, 1, "callee", "voice")}}
	h := http.Header{"Origin": {origin}}
	c, _, err := d.Dial("ws"+strings.TrimPrefix(e.ts.URL, "http")+"/ws?role=caller&uid=1&token=x", h)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	var w map[string]any
	_ = c.ReadJSON(&w)
	if w["role"] != "callee" || w["user_id"].(float64) != 2 {
		t.Fatalf("query parameters must be ignored: %v", w)
	}
}

func TestOfferSentBeforeCalleeConnectsIsNeverLost(t *testing.T) {
	e := newEnv(t, nil)
	caller := e.must(t, mint("c-offer", 1, 2, "caller", "voice"))
	caller.next("welcome")
	caller.send(map[string]any{"type": "offer", "id": "m-offer", "payload": payload(sdp)})
	q := caller.next("queued")
	if q["id"] != "m-offer" || q["peer_online"] != false {
		t.Fatalf("queued ack must say the peer is offline: %v", q)
	}
	caller.send(map[string]any{"type": "ice-candidate", "id": "m-ice1", "payload": map[string]any{"candidate": cand}})
	caller.next("queued")
	caller.expectNone("delivered", 300*time.Millisecond) // sending over the socket is NOT delivery

	callee := e.must(t, mint("c-offer", 2, 1, "callee", "voice"))
	callee.next("welcome") // welcome always comes before the replayed messages
	off := callee.next("offer")
	if off["id"] != "m-offer" || off["from"].(float64) != 1 {
		t.Fatal(off)
	}
	ice := callee.next("ice-candidate")
	if ice["seq"].(float64) <= off["seq"].(float64) {
		t.Fatal("per-recipient order must be preserved")
	}
	callee.send(map[string]any{"type": "ack", "seq": off["seq"]})
	d := caller.next("delivered")
	if d["id"] != "m-offer" {
		t.Fatalf("delivered must reference the original id: %v", d)
	}
	callee.send(map[string]any{"type": "answer", "id": "m-ans", "payload": payload(sdp)})
	caller.next("answer")
}

func TestUnacknowledgedMessagesAreResentThenReplayedOnReconnect(t *testing.T) {
	e := newEnv(t, nil)
	caller := e.must(t, mint("c-resume", 1, 2, "caller", "voice"))
	caller.next("welcome")
	callee := e.must(t, mint("c-resume", 2, 1, "callee", "voice"))
	callee.next("welcome")
	caller.send(map[string]any{"type": "offer", "id": "o1", "payload": payload(sdp)})
	first := callee.next("offer")
	// no ack -> the server's retry ticker resends; here the manager is ticked by hand
	time.Sleep(250 * time.Millisecond)
	e.mgr.Tick()
	again := callee.next("offer")
	if again["seq"] != first["seq"] {
		t.Fatal("a resend keeps the same seq so clients can de-duplicate")
	}
	callee.c.Close() // network drop, offer still unacknowledged
	time.Sleep(100 * time.Millisecond)
	callee2 := e.must(t, mint("c-resume", 2, 1, "callee", "voice"))
	w := callee2.next("welcome")
	if w["resumed"] != true {
		t.Fatalf("second connection of the same role is a resume: %v", w)
	}
	if o := callee2.next("offer"); o["seq"] != first["seq"] {
		t.Fatal("pending offer must be replayed after reconnect")
	}
}

func TestDuplicateMessageIDsAreIdempotent(t *testing.T) {
	e := newEnv(t, nil)
	caller := e.must(t, mint("c-dup", 1, 2, "caller", "voice"))
	caller.next("welcome")
	callee := e.must(t, mint("c-dup", 2, 1, "callee", "voice"))
	callee.next("welcome")
	for i := 0; i < 3; i++ {
		caller.send(map[string]any{"type": "ice-candidate", "id": "same-id", "payload": map[string]any{"candidate": cand}})
	}
	caller.next("queued")
	if q := caller.next("queued"); q["duplicate"] != true {
		t.Fatal("second send must be flagged duplicate")
	}
	callee.next("ice-candidate")
	callee.expectNone("ice-candidate", 400*time.Millisecond) // note: resend after 200ms would show up only if unacked AND duplicated
}

func TestRustValidationBlocksBadMessages(t *testing.T) {
	e := newEnv(t, nil)
	caller := e.must(t, mint("c-val", 1, 2, "caller", "voice"))
	caller.next("welcome")
	callee := e.must(t, mint("c-val", 2, 1, "callee", "voice"))
	callee.next("welcome")

	callee.send(map[string]any{"type": "offer", "id": "x1", "payload": payload(sdp)})
	if m := callee.next("error"); m["code"] != "type_forbidden" {
		t.Fatalf("callee must not offer: %v", m)
	}
	callee.send(map[string]any{"type": "answer", "id": "x2", "payload": payload(sdp)})
	if m := callee.next("error"); m["code"] != "invalid_state" {
		t.Fatalf("answer before offer: %v", m)
	}
	caller.send(map[string]any{"type": "offer", "id": "x3", "payload": payload("v=0 but not really sdp")})
	if m := caller.next("error"); m["code"] != "invalid_sdp" {
		t.Fatalf("garbage SDP: %v", m)
	}
	caller.send(map[string]any{"type": "offer", "id": "x4", "payload": payload(strings.Replace(sdp, "a=fingerprint:sha-256 AA:BB\r\n", "", 1))})
	if m := caller.next("error"); m["code"] != "invalid_sdp" {
		t.Fatalf("SDP without DTLS fingerprint must be refused: %v", m)
	}
	caller.send(map[string]any{"type": "ice-candidate", "id": "x5", "payload": map[string]any{"candidate": "candidate:evil"}})
	if m := caller.next("error"); m["code"] != "invalid_candidate" {
		t.Fatal(m)
	}
	caller.send(map[string]any{"type": "teleport", "id": "x6"})
	caller.next("error")
	callee.expectNone("offer", 200*time.Millisecond)
	// the failed ids were not burned: a corrected retry with the same id works
	caller.send(map[string]any{"type": "offer", "id": "x3", "payload": payload(sdp)})
	caller.next("queued")
	callee.next("offer")
}

func TestBusyAndDuplicateSessions(t *testing.T) {
	e := newEnv(t, nil)
	a := e.must(t, mint("c-busy-1", 1, 2, "caller", "voice"))
	a.next("welcome")
	// user 1 is already in c-busy-1: a second call is refused
	if _, resp, err := e.dial(t, mint("c-busy-2", 1, 3, "caller", "voice"), origin); err == nil || resp.StatusCode != 409 {
		t.Fatal("caller already in a call must get 409")
	}
	// user 3 calls user 1, who is busy: peer_busy
	_, resp, err := e.dial(t, mint("c-busy-3", 3, 1, "caller", "voice"), origin)
	if err == nil || resp.StatusCode != 409 {
		t.Fatal("calling a busy user must get 409")
	}
	// a token cannot re-assign roles of an existing call
	if _, resp, err := e.dial(t, mint("c-busy-1", 9, 1, "caller", "voice"), origin); err == nil || resp.StatusCode != 403 && resp.StatusCode != 409 {
		t.Fatalf("mismatching participants must be refused, got %v", resp)
	}
	// newest socket of the same role wins
	a2 := e.must(t, mint("c-busy-1", 1, 2, "caller", "voice"))
	a2.next("welcome")
	if m := a.next("session_end"); m["reason"] != "replaced" {
		t.Fatal(m)
	}
}

func TestHangupEndsCallAndLateAttachIsGone(t *testing.T) {
	e := newEnv(t, nil)
	caller := e.must(t, mint("c-end", 1, 2, "caller", "voice"))
	caller.next("welcome")
	callee := e.must(t, mint("c-end", 2, 1, "callee", "voice"))
	callee.next("welcome")
	callee.send(map[string]any{"type": "reject", "id": "r1"})
	caller.next("reject")
	if _, resp, err := e.dial(t, mint("c-end", 2, 1, "callee", "voice"), origin); err == nil || resp.StatusCode != 410 {
		t.Fatal("attaching to an ended call must get 410")
	}
	// both users are free again
	b := e.must(t, mint("c-end-next", 1, 3, "caller", "voice"))
	b.next("welcome")
}

func TestReadinessAndPeerEvents(t *testing.T) {
	e := newEnv(t, nil)
	caller := e.must(t, mint("c-ready", 1, 2, "caller", "video"))
	caller.next("welcome")
	callee := e.must(t, mint("c-ready", 2, 1, "callee", "video"))
	if w := callee.next("welcome"); w["peer"].(map[string]any)["online"] != true {
		t.Fatal(w)
	}
	caller.next("peer_online")
	callee.send(map[string]any{"type": "ready"})
	caller.next("peer_ready")
	callee.c.Close()
	caller.next("peer_offline")
}

func TestQualityTelemetryRunsThroughCppAndRust(t *testing.T) {
	e := newEnv(t, nil)
	c := e.must(t, mint("c-q", 1, 2, "caller", "video"))
	c.next("welcome")
	before := native.Components()
	for i := 0; i < 6; i++ {
		c.send(map[string]any{"type": "quality", "payload": map[string]any{"rtt_ms": 900, "jitter_ms": 200, "loss_permille": 300,
			"bitrate_kbps": 40, "audio_playout_ms": 1000, "video_playout_ms": 900, "jb_delay_ms": 120, "audio_peak": 0.2}})
	}
	var last map[string]any
	for i := 0; i < 6; i++ {
		last = c.next("quality_report")
	}
	rep := last["report"].(map[string]any)
	if rep["level"].(float64) != 3 || rep["advise_audio_only"] != true || last["score"].(float64) > 40 {
		t.Fatalf("bad network must be reported as bad: %v", last)
	}
	after := native.Components()
	if after[0].Detail["quality_updates"].(uint64) < before[0].Detail["quality_updates"].(uint64)+6 {
		t.Fatal("rust quality counter did not advance")
	}
	if after[2].Detail["telemetry_samples_processed"].(uint64) < before[2].Detail["telemetry_samples_processed"].(uint64)+6 {
		t.Fatal("c++ sample counter did not advance")
	}
	c.send(map[string]any{"type": "quality", "payload": map[string]any{"rtt_ms": -1}})
	if m := c.next("error"); m["code"] != "bad_telemetry" {
		t.Fatal(m)
	}
}
