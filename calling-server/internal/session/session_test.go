package session

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/goppo/calling-server/internal/auth"
	"github.com/goppo/calling-server/internal/native"
)

func claims(call string, uid, pid int64, role string) *auth.Claims {
	return &auth.Claims{Version: 1, UserID: uid, PeerID: pid, CallType: "voice", CallID: call, Role: role}
}

func drain(c *Conn) []map[string]any {
	var out []map[string]any
	for {
		select {
		case b := <-c.Send:
			var m map[string]any
			_ = json.Unmarshal(b, &m)
			out = append(out, m)
		default:
			return out
		}
	}
}

func hasType(ms []map[string]any, t string) bool {
	for _, m := range ms {
		if m["type"] == t {
			return true
		}
	}
	return false
}

type clock struct{ t time.Time }

func (c *clock) now() time.Time { return c.t }

func setup(t *testing.T) (*Manager, *clock) {
	if err := native.Init(); err != nil {
		t.Fatal(err)
	}
	cfg := DefaultConfig()
	cfg.RingTimeout, cfg.ReconnectGrace, cfg.ResendAfter = 10*time.Second, 5*time.Second, time.Second
	cfg.SignalTTL, cfg.OfferTTL, cfg.EndedLinger, cfg.MaxCallDuration = 4*time.Second, 6*time.Second, 2*time.Second, time.Hour
	m := NewManager(cfg)
	ck := &clock{t: time.Unix(1_700_000_000, 0)}
	m.SetClock(ck.now)
	return m, ck
}

const sdp = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 AA:BB\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

func offer(id string) []byte {
	b, _ := json.Marshal(map[string]any{"type": "offer", "id": id, "payload": map[string]any{"sdp": sdp}})
	return b
}

func TestRingTimeoutWhenCalleeNeverConnects(t *testing.T) {
	m, ck := setup(t)
	cl := claims("s-ring", 1, 2, "caller")
	c := NewConn(nil)
	if _, err := m.Attach(cl, c); err != nil {
		t.Fatal(err)
	}
	drain(c)
	ck.t = ck.t.Add(11 * time.Second)
	m.Tick()
	if ms := drain(c); !hasType(ms, "session_end") || ms[len(ms)-1]["reason"] != "timeout" {
		t.Fatalf("caller must be told about the timeout: %v", ms)
	}
	if m.Stats().Users != 0 {
		t.Fatal("users must be released when the call ends")
	}
}

func TestReconnectGraceThenPeerDropped(t *testing.T) {
	m, ck := setup(t)
	a, b := claims("s-grace", 1, 2, "caller"), claims("s-grace", 2, 1, "callee")
	ca, cb := NewConn(nil), NewConn(nil)
	_, _ = m.Attach(a, ca)
	_, _ = m.Attach(b, cb)
	drain(ca)
	drain(cb)
	m.Detach(b, cb)
	if !hasType(drain(ca), "peer_offline") {
		t.Fatal("peer_offline expected")
	}
	ck.t = ck.t.Add(4 * time.Second) // inside the grace period: call survives and the peer may return
	m.Tick()
	cb2 := NewConn(nil)
	res, err := m.Attach(b, cb2)
	if err != nil || !res.Resumed {
		t.Fatalf("resume within grace: %v %+v", err, res)
	}
	m.Detach(b, cb2)
	ck.t = ck.t.Add(6 * time.Second) // beyond the grace period
	m.Tick()
	ms := drain(ca)
	if !hasType(ms, "peer_left") || !hasType(ms, "session_end") {
		t.Fatalf("peer drop must end the call for the other side: %v", ms)
	}
}

func TestExpiredMessagesAreReportedToSender(t *testing.T) {
	m, ck := setup(t)
	a := claims("s-exp", 1, 2, "caller")
	ca := NewConn(nil)
	_, _ = m.Attach(a, ca)
	drain(ca)
	m.Handle(a, ca, offer("o-exp"))
	if ms := drain(ca); !hasType(ms, "queued") {
		t.Fatal(ms)
	}
	ck.t = ck.t.Add(7 * time.Second) // OfferTTL is 6 s
	m.Tick()
	ms := drain(ca)
	if !hasType(ms, "expired") {
		t.Fatalf("sender must learn that the offer expired: %v", ms)
	}
	if m.Stats().PendingMessages != 0 {
		t.Fatal("expired messages must be removed")
	}
}

func TestBacklogIsBounded(t *testing.T) {
	m, _ := setup(t)
	m.cfg.MaxPending = 3
	a := claims("s-cap", 1, 2, "caller")
	ca := NewConn(nil)
	_, _ = m.Attach(a, ca)
	drain(ca)
	for i := 0; i < 5; i++ {
		b, _ := json.Marshal(map[string]any{"type": "ice-candidate", "id": string(rune('a' + i)), "payload": map[string]any{"candidate": "candidate:1 1 udp 1 1.2.3.4 9 typ host"}})
		m.Handle(a, ca, b)
	}
	if m.Stats().PendingMessages != 3 {
		t.Fatalf("pending=%d", m.Stats().PendingMessages)
	}
}

func TestUnknownConnectionCannotInjectMessages(t *testing.T) {
	m, _ := setup(t)
	a := claims("s-inj", 1, 2, "caller")
	ca := NewConn(nil)
	_, _ = m.Attach(a, ca)
	stranger := NewConn(nil)
	m.Handle(a, stranger, offer("evil")) // a connection that is not the registered one
	if m.Stats().PendingMessages != 0 {
		t.Fatal("messages from a non-registered connection must be ignored")
	}
}
