// Package session implements reliable call signaling.
//
// Guarantees (all covered by tests):
//   - A message is only "delivered" once the receiving client acknowledged its sequence number.
//   - Messages for an offline peer are queued (bounded, with expiry) and replayed in order when it (re)connects.
//   - Duplicate client message ids are ignored (idempotent retries).
//   - Roles come from the signed token; every message is validated by the Rust core before it is queued.
//   - One live call per user (busy), one live socket per role (newest wins), bounded reconnect grace, ring timeout.
package session

import (
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/goppo/calling-server/internal/auth"
	"github.com/goppo/calling-server/internal/native"
)

type Config struct {
	RingTimeout     time.Duration // callee never connected
	ReconnectGrace  time.Duration // a dropped party may come back within this time
	ResendAfter     time.Duration // unacknowledged messages are re-sent after this
	OfferTTL        time.Duration
	SignalTTL       time.Duration
	MaxCallDuration time.Duration
	EndedLinger     time.Duration
	MaxPending      int
}

func DefaultConfig() Config {
	return Config{RingTimeout: 75 * time.Second, ReconnectGrace: 20 * time.Second, ResendAfter: 3 * time.Second,
		OfferTTL: 90 * time.Second, SignalTTL: 60 * time.Second, MaxCallDuration: 4 * time.Hour,
		EndedLinger: 15 * time.Second, MaxPending: 256}
}

var (
	ErrBusy        = errors.New("user is busy in another call")
	ErrPeerBusy    = errors.New("peer is busy in another call")
	ErrEnded       = errors.New("call already ended")
	ErrMismatch    = errors.New("token does not match the existing call")
	ErrNativeOpen  = errors.New("native session open failed")
	errBacklogFull = errors.New("backlog")
)

// CloseRequest asks the socket's writer to flush what is queued, send a close frame and hang up.
type CloseRequest struct {
	Code   int
	Reason string
}

// Conn is one WebSocket from the session's point of view.
type Conn struct {
	Send     chan []byte
	CloseReq chan CloseRequest // handled by the writer goroutine so queued messages are flushed first
	closer   func(code int, reason string)
	once     sync.Once
}

func NewConn(closer func(code int, reason string)) *Conn {
	return &Conn{Send: make(chan []byte, 128), CloseReq: make(chan CloseRequest, 1), closer: closer}
}

func (c *Conn) Close(code int, reason string) {
	c.once.Do(func() {
		c.CloseReq <- CloseRequest{code, reason}
		if c.closer != nil {
			c.closer(code, reason)
		}
	})
}

func (c *Conn) trySend(b []byte) bool {
	select {
	case c.Send <- b:
		return true
	default:
		return false
	}
}

type envelope struct {
	seq     uint64
	id      string
	kind    string
	from    int64
	payload json.RawMessage
	expires time.Time
	sentAt  time.Time
}

type party struct {
	uid       int64
	role      string
	conn      *Conn
	everSeen  bool
	ready     bool
	nextSeq   uint64
	pending   []*envelope // envelopes addressed TO this party, not yet acknowledged
	seen      map[string]struct{}
	seenOrder []string
	detached  time.Time
	quality   *native.Quality
}

type call struct {
	id        string
	caller    int64
	callee    int64
	ctype     string
	created   time.Time
	parties   map[string]*party
	ended     bool
	endReason string
	endedAt   time.Time
}

type Manager struct {
	cfg   Config
	mu    sync.Mutex
	calls map[string]*call
	users map[int64]string // user -> id of the live call the user is attached to
	now   func() time.Time
}

func NewManager(cfg Config) *Manager {
	return &Manager{cfg: cfg, calls: map[string]*call{}, users: map[int64]string{}, now: time.Now}
}

func (m *Manager) SetClock(f func() time.Time) { m.mu.Lock(); m.now = f; m.mu.Unlock() }

type AttachResult struct {
	Resumed     bool
	PeerOnline  bool
	PeerReady   bool
	Replaced    bool
	PendingSent int
}

func other(role string) string {
	if role == "caller" {
		return "callee"
	}
	return "caller"
}

func js(v any) []byte { b, _ := json.Marshal(v); return b }

// Attach registers a WebSocket for the (already authenticated) claims.
func (m *Manager) Attach(cl *auth.Claims, conn *Conn) (*AttachResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	now := m.now()

	c, ok := m.calls[cl.CallID]
	if ok && c.ended {
		return nil, ErrEnded
	}
	callerID, calleeID := cl.UserID, cl.PeerID
	if cl.Role == "callee" {
		callerID, calleeID = cl.PeerID, cl.UserID
	}
	if ok && (c.caller != callerID || c.callee != calleeID || c.ctype != cl.CallType) {
		return nil, ErrMismatch // a token can never re-assign who is who in an existing call
	}
	if cur, busy := m.users[cl.UserID]; busy && cur != cl.CallID {
		if oc, alive := m.calls[cur]; alive && !oc.ended {
			return nil, ErrBusy
		}
	}
	if !ok {
		if cl.Role == "caller" {
			if cur, busy := m.users[calleeID]; busy && cur != cl.CallID {
				if oc, alive := m.calls[cur]; alive && !oc.ended {
					return nil, ErrPeerBusy
				}
			}
		}
		if rc := native.SessionOpen(cl.CallID, callerID, calleeID, cl.CallType == "video"); rc != native.OK {
			return nil, ErrNativeOpen
		}
		c = &call{id: cl.CallID, caller: callerID, callee: calleeID, ctype: cl.CallType, created: now,
			parties: map[string]*party{}}
		m.calls[cl.CallID] = c
	}

	p := c.parties[cl.Role]
	res := &AttachResult{}
	if p == nil {
		q, _ := native.NewQuality()
		p = &party{uid: cl.UserID, role: cl.Role, seen: map[string]struct{}{}, quality: q}
		c.parties[cl.Role] = p
	} else {
		res.Resumed = p.everSeen
	}
	if p.conn != nil && p.conn != conn { // newest socket wins; the old one is told why
		old := p.conn
		old.trySend(js(map[string]any{"type": "session_end", "reason": "replaced"}))
		go old.Close(4001, "replaced by a newer connection")
		res.Replaced = true
	}
	p.conn, p.everSeen, p.detached = conn, true, time.Time{}
	m.users[cl.UserID] = cl.CallID

	peer := c.parties[other(cl.Role)]
	if peer != nil {
		res.PeerOnline, res.PeerReady = peer.conn != nil, peer.ready
		if peer.conn != nil {
			peer.conn.trySend(js(map[string]any{"type": "peer_online", "ready": p.ready}))
		}
	}
	// The welcome is queued BEFORE any replayed message so the client always sees it first.
	conn.trySend(js(map[string]any{"type": "welcome", "call_id": c.id, "role": cl.Role, "user_id": cl.UserID, "peer_id": cl.PeerID,
		"call_type": c.ctype, "resumed": res.Resumed, "peer": map[string]any{"online": res.PeerOnline, "ready": res.PeerReady},
		"server_time": now.UnixMilli(), "reconnect_grace_seconds": int(m.cfg.ReconnectGrace.Seconds())}))
	for _, e := range p.pending { // replay everything that was never acknowledged
		e.sentAt = time.Time{}
	}
	res.PendingSent = m.deliverLocked(p, now)
	return res, nil
}

// Detach is called when a socket closes. The call stays alive for ReconnectGrace.
func (m *Manager) Detach(cl *auth.Claims, conn *Conn) {
	m.mu.Lock()
	defer m.mu.Unlock()
	c := m.calls[cl.CallID]
	if c == nil {
		return
	}
	p := c.parties[cl.Role]
	if p == nil || p.conn != conn {
		return // a replaced socket closing must not detach its successor
	}
	p.conn, p.detached, p.ready = nil, m.now(), false
	if c.ended {
		delete(m.users, p.uid)
		return
	}
	if peer := c.parties[other(cl.Role)]; peer != nil && peer.conn != nil {
		peer.conn.trySend(js(map[string]any{"type": "peer_offline", "grace_seconds": int(m.cfg.ReconnectGrace.Seconds())}))
	}
}

func (m *Manager) send(p *party, b []byte) {
	if p == nil || p.conn == nil {
		return
	}
	if !p.conn.trySend(b) { // client too slow: drop the socket, it will reconnect and receive a replay
		old := p.conn
		p.conn, p.detached = nil, m.now()
		go old.Close(1013, "send buffer full")
	}
}

func (m *Manager) deliverLocked(p *party, now time.Time) int {
	if p.conn == nil {
		return 0
	}
	n := 0
	for _, e := range p.pending {
		if now.After(e.expires) {
			continue
		}
		if !e.sentAt.IsZero() && now.Sub(e.sentAt) < m.cfg.ResendAfter {
			continue
		}
		e.sentAt = now
		m.send(p, js(map[string]any{"type": e.kind, "seq": e.seq, "id": e.id, "from": e.from, "payload": e.payload}))
		if p.conn == nil {
			break
		}
		n++
	}
	return n
}

type inbound struct {
	Type    string          `json:"type"`
	ID      string          `json:"id"`
	Seq     uint64          `json:"seq"`
	Payload json.RawMessage `json:"payload"`
}

type telemetry struct {
	RTTMs          float64  `json:"rtt_ms"`
	JitterMs       float64  `json:"jitter_ms"`
	LossPermille   float64  `json:"loss_permille"`
	BitrateKbps    float64  `json:"bitrate_kbps"`
	AudioPlayoutMs float64  `json:"audio_playout_ms"`
	VideoPlayoutMs float64  `json:"video_playout_ms"`
	JBDelayMs      float64  `json:"jb_delay_ms"`
	AudioPeak      *float64 `json:"audio_peak"`
}

// Handle processes one raw message from a client.
func (m *Manager) Handle(cl *auth.Claims, conn *Conn, raw []byte) {
	var in inbound
	m.mu.Lock()
	defer m.mu.Unlock()
	c := m.calls[cl.CallID]
	if c == nil {
		return
	}
	p := c.parties[cl.Role]
	if p == nil || p.conn != conn || p.uid != cl.UserID {
		return
	}
	now := m.now()
	reply := func(v any) { m.send(p, js(v)) }
	if err := json.Unmarshal(raw, &in); err != nil {
		reply(map[string]any{"type": "error", "code": "bad_json"})
		return
	}
	peer := c.parties[other(cl.Role)]

	switch in.Type {
	case "ping":
		reply(map[string]any{"type": "pong", "t": now.UnixMilli()})
	case "ack":
		for i, e := range p.pending {
			if e.seq == in.Seq {
				p.pending = append(p.pending[:i], p.pending[i+1:]...)
				if sender := c.parties[other(cl.Role)]; sender != nil {
					m.send(sender, js(map[string]any{"type": "delivered", "id": e.id, "kind": e.kind}))
				}
				break
			}
		}
	case "ready":
		if rc := native.CheckSignal(c.id, p.uid, "ready", ""); rc != native.OK {
			reply(map[string]any{"type": "error", "code": native.CodeName(rc)})
			return
		}
		p.ready = true
		if peer != nil {
			m.send(peer, js(map[string]any{"type": "peer_ready"}))
		}
	case "quality":
		m.handleQuality(c, p, in.Payload, reply)
	case "offer", "answer", "ice-candidate", "hangup", "reject", "busy":
		if c.ended && in.Type != "hangup" {
			reply(map[string]any{"type": "error", "id": in.ID, "code": "call_ended"})
			return
		}
		if in.ID == "" || len(in.ID) > 64 {
			reply(map[string]any{"type": "error", "code": "bad_id"})
			return
		}
		if _, dup := p.seen[in.ID]; dup {
			reply(map[string]any{"type": "queued", "id": in.ID, "duplicate": true})
			return
		}
		text := ""
		if in.Type == "offer" || in.Type == "answer" || in.Type == "ice-candidate" {
			var body struct {
				SDP       *string `json:"sdp"`
				Candidate *string `json:"candidate"`
			}
			if err := json.Unmarshal(in.Payload, &body); err != nil {
				reply(map[string]any{"type": "error", "id": in.ID, "code": "bad_payload"})
				return
			}
			if in.Type == "ice-candidate" && body.Candidate != nil {
				text = *body.Candidate
			} else if in.Type != "ice-candidate" && body.SDP != nil {
				text = *body.SDP
			} else {
				reply(map[string]any{"type": "error", "id": in.ID, "code": "bad_payload"})
				return
			}
		}
		if rc := native.CheckSignal(c.id, p.uid, in.Type, text); rc != native.OK {
			reply(map[string]any{"type": "error", "id": in.ID, "code": native.CodeName(rc)})
			return
		}
		p.seen[in.ID] = struct{}{}
		p.seenOrder = append(p.seenOrder, in.ID)
		if len(p.seenOrder) > 512 {
			delete(p.seen, p.seenOrder[0])
			p.seenOrder = p.seenOrder[1:]
		}
		if peer == nil { // the other side has not connected yet: create its mailbox so nothing is lost
			q, _ := native.NewQuality()
			peerUID := c.callee
			if cl.Role == "callee" {
				peerUID = c.caller
			}
			peer = &party{uid: peerUID, role: other(cl.Role), seen: map[string]struct{}{}, quality: q}
			c.parties[peer.role] = peer
		}
		if len(peer.pending) >= m.cfg.MaxPending {
			reply(map[string]any{"type": "error", "id": in.ID, "code": errBacklogFull.Error()})
			return
		}
		ttl := m.cfg.SignalTTL
		if in.Type == "offer" {
			ttl = m.cfg.OfferTTL
		}
		peer.nextSeq++
		peer.pending = append(peer.pending, &envelope{seq: peer.nextSeq, id: in.ID, kind: in.Type, from: p.uid,
			payload: in.Payload, expires: now.Add(ttl)})
		reply(map[string]any{"type": "queued", "id": in.ID, "peer_online": peer.conn != nil})
		m.deliverLocked(peer, now)
		if in.Type == "hangup" || in.Type == "reject" || in.Type == "busy" {
			m.endLocked(c, in.Type, now, false)
		}
	default:
		reply(map[string]any{"type": "error", "code": "unknown_type"})
	}
}

func (m *Manager) handleQuality(c *call, p *party, payload json.RawMessage, reply func(any)) {
	var t telemetry
	if err := json.Unmarshal(payload, &t); err != nil || p.quality == nil {
		reply(map[string]any{"type": "error", "code": "bad_payload"})
		return
	}
	peak := -1.0
	if t.AudioPeak != nil {
		peak = *t.AudioPeak
	}
	if rc := native.CheckSignal(c.id, p.uid, "quality", ""); rc != native.OK {
		reply(map[string]any{"type": "error", "code": native.CodeName(rc)})
		return
	}
	err := p.quality.Add(native.Sample{RTTMs: t.RTTMs, JitterMs: t.JitterMs, LossPermille: t.LossPermille, BitrateKbps: t.BitrateKbps,
		AudioPlayoutMs: t.AudioPlayoutMs, VideoPlayoutMs: t.VideoPlayoutMs, JBDelayMs: t.JBDelayMs, AudioPeak: peak})
	if err != nil {
		reply(map[string]any{"type": "error", "code": "bad_telemetry"})
		return
	}
	score := native.QualityScore(c.id, p.uid, t.RTTMs, t.JitterMs, t.LossPermille)
	rep, _ := p.quality.Report()
	reply(map[string]any{"type": "quality_report", "score": score, "report": rep})
}

// endLocked terminates a call. notify=true tells attached parties via session_end (timeouts, dropped peers).
func (m *Manager) endLocked(c *call, reason string, now time.Time, notify bool) {
	if c.ended {
		return
	}
	c.ended, c.endReason, c.endedAt = true, reason, now
	native.SessionClose(c.id)
	for _, p := range c.parties {
		if m.users[p.uid] == c.id {
			delete(m.users, p.uid)
		}
		if notify && p.conn != nil {
			m.send(p, js(map[string]any{"type": "session_end", "reason": reason}))
		}
	}
}

// Tick drives retries, expiry, grace periods and cleanup. The server calls it once a second.
func (m *Manager) Tick() {
	m.mu.Lock()
	defer m.mu.Unlock()
	now := m.now()
	for id, c := range m.calls {
		if c.ended {
			m.deliverAll(c, now)
			if now.Sub(c.endedAt) > m.cfg.EndedLinger {
				for _, p := range c.parties {
					if p.conn != nil {
						go p.conn.Close(1000, "call ended")
					}
					p.quality.Free()
				}
				delete(m.calls, id)
			}
			continue
		}
		calleeSeen := c.parties["callee"] != nil && c.parties["callee"].everSeen
		switch {
		case !calleeSeen && now.Sub(c.created) > m.cfg.RingTimeout:
			m.endLocked(c, "timeout", now, true)
			continue
		case now.Sub(c.created) > m.cfg.MaxCallDuration:
			m.endLocked(c, "max_duration", now, true)
			continue
		}
		for role, p := range c.parties {
			if p.conn == nil && p.everSeen && !p.detached.IsZero() && now.Sub(p.detached) > m.cfg.ReconnectGrace {
				if peer := c.parties[other(role)]; peer != nil {
					m.send(peer, js(map[string]any{"type": "peer_left", "reason": "disconnected"}))
				}
				m.endLocked(c, "peer_dropped", now, true)
				break
			}
		}
		m.deliverAll(c, now)
	}
}

func (m *Manager) deliverAll(c *call, now time.Time) {
	for _, p := range c.parties {
		kept := p.pending[:0]
		for _, e := range p.pending {
			if now.After(e.expires) {
				if sender := c.parties[other(p.role)]; sender != nil {
					m.send(sender, js(map[string]any{"type": "expired", "id": e.id, "kind": e.kind}))
				}
				continue
			}
			kept = append(kept, e)
		}
		p.pending = kept
		m.deliverLocked(p, now)
	}
}

// Shutdown closes every socket (graceful stop).
func (m *Manager) Shutdown() {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, c := range m.calls {
		for _, p := range c.parties {
			if p.conn != nil {
				p.conn.trySend(js(map[string]any{"type": "session_end", "reason": "server_shutdown"}))
				go p.conn.Close(1001, "server shutting down")
			}
		}
	}
}

type Stats struct {
	Calls, Users, PendingMessages int
}

func (m *Manager) Stats() Stats {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := Stats{Calls: len(m.calls), Users: len(m.users)}
	for _, c := range m.calls {
		for _, p := range c.parties {
			s.PendingMessages += len(p.pending)
		}
	}
	return s
}
