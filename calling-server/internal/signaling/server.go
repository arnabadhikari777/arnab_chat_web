// Package signaling exposes the HTTP/WebSocket surface: /health /ready /ice /components /ws and admin panel.
package signaling

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strings"
	"sync/atomic"
	"time"

	"github.com/goppo/calling-server/internal/admin"
	"github.com/goppo/calling-server/internal/auth"
	"github.com/goppo/calling-server/internal/config"
	"github.com/goppo/calling-server/internal/native"
	"github.com/goppo/calling-server/internal/session"
	"github.com/goppo/calling-server/internal/turn"
	"github.com/gorilla/websocket"
)

const subprotocol = "goppo.v1"
const authPrefix = "auth."

type Server struct {
	cfg      *config.Config
	val      *auth.Validator
	mgr      *session.Manager
	turn     *turn.Manager
	admin    *admin.Server
	upgrader websocket.Upgrader
	started  time.Time
	sockets  atomic.Int64
	draining atomic.Bool
}

func New(cfg *config.Config, val *auth.Validator, mgr *session.Manager, tm *turn.Manager, adm *admin.Server) *Server {
	s := &Server{cfg: cfg, val: val, mgr: mgr, turn: tm, admin: adm, started: time.Now()}
	s.upgrader = websocket.Upgrader{
		ReadBufferSize: 4096, WriteBufferSize: 4096,
		Subprotocols: []string{subprotocol},
		// A browser always sends Origin on WebSocket handshakes. A missing or unlisted Origin is refused.
		CheckOrigin: func(r *http.Request) bool { return cfg.OriginAllowed(r.Header.Get("Origin")) },
	}
	return s
}

func (s *Server) Drain() { s.draining.Store(true) }

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.health)
	mux.HandleFunc("/ready", s.ready)
	mux.HandleFunc("/ice", s.ice)
	mux.HandleFunc("/components", s.components)
	mux.HandleFunc("/ws", s.ws)
	if s.admin != nil {
		s.admin.Mount(mux)
	}
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, 200, map[string]any{"service": "goppo-calling", "status": "ok"})
	})
	return s.secure(mux)
}

// secure applies strict CORS (exact origin allow-list), security headers and OPTIONS handling.
func (s *Server) secure(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Cache-Control", "no-store")
		h.Set("Referrer-Policy", "no-referrer")
		h.Add("Vary", "Origin")
		// Admin panel is same-origin browser use from the Render URL; do not apply chat-site CORS to it.
		if strings.HasPrefix(r.URL.Path, "/admin") {
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
			return
		}
		origin := r.Header.Get("Origin")
		if origin != "" {
			if !s.cfg.OriginAllowed(origin) {
				if r.Method == http.MethodOptions {
					http.Error(w, "origin not allowed", http.StatusForbidden)
					return
				}
				// Same-origin-less callers (curl, Render's health checker) send no Origin and pass; a browser from an
				// unlisted origin still receives no CORS headers, so it cannot read the answer. /ws re-checks the origin.
			} else {
				h.Set("Access-Control-Allow-Origin", origin)
				h.Set("Access-Control-Allow-Methods", "GET, OPTIONS")
				h.Set("Access-Control-Allow-Headers", "Content-Type, X-Call-Token")
				h.Set("Access-Control-Max-Age", "600")
			}
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

// /health: the process is up (used by Render's health check and for waking a sleeping free instance).
func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"status": "ok", "service": "goppo-calling"})
}

// /ready: 200 only when every native component loaded and passed its self-test and the server is not draining.
func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	ok := native.AllVerified() && !s.draining.Load()
	code := 200
	if !ok {
		code = 503
	}
	st := s.mgr.Stats()
	writeJSON(w, code, map[string]any{"ready": ok, "native_verified": native.AllVerified(), "draining": s.draining.Load(),
		"uptime_seconds": int(time.Since(s.started).Seconds()), "calls": st.Calls, "sockets": s.sockets.Load()})
}

func (s *Server) components(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"all_verified": native.AllVerified(), "components": native.Components(),
		"require_native": s.cfg.RequireNative, "architecture": "p2p-webrtc (media never reaches this server)"})
}

// /ice: STUN/TURN configuration. Requires a valid (unexpired, signed) call token; TURN credentials are short-lived.
// Format preserved for the existing frontend: { iceServers, ttl_seconds, turn_configured }.
func (s *Server) ice(w http.ResponseWriter, r *http.Request) {
	tok := r.Header.Get("X-Call-Token")
	claims, err := s.val.Verify(tok)
	if err != nil {
		writeJSON(w, 401, map[string]any{"error": "invalid_token"})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	servers, ttlSec, turnConfigured, err := s.turn.ICEServers(ctx, claims.UserID)
	if err != nil {
		log.Printf("ice provider error user=%d: %v", claims.UserID, err)
		writeJSON(w, 503, map[string]any{
			"error":           "turn_provider_unavailable",
			"message":         err.Error(),
			"turn_configured": false,
		})
		return
	}
	writeJSON(w, 200, map[string]any{
		"iceServers":      servers,
		"ttl_seconds":     ttlSec,
		"turn_configured": turnConfigured,
	})
}

func tokenFromProtocols(r *http.Request) string {
	for _, h := range r.Header.Values("Sec-WebSocket-Protocol") {
		for _, p := range strings.Split(h, ",") {
			if p = strings.TrimSpace(p); strings.HasPrefix(p, authPrefix) {
				return strings.TrimPrefix(p, authPrefix)
			}
		}
	}
	return ""
}

// /ws: the token travels in the Sec-WebSocket-Protocol header (not in the URL, so it never lands in access logs).
func (s *Server) ws(w http.ResponseWriter, r *http.Request) {
	if s.draining.Load() {
		http.Error(w, "server restarting", http.StatusServiceUnavailable)
		return
	}
	if !s.cfg.OriginAllowed(r.Header.Get("Origin")) {
		http.Error(w, "origin not allowed", http.StatusForbidden)
		return
	}
	claims, err := s.val.Consume(tokenFromProtocols(r))
	if err != nil {
		code := http.StatusUnauthorized
		msg := "invalid_token"
		if errors.Is(err, auth.ErrExpired) {
			msg = "token_expired"
		}
		if errors.Is(err, auth.ErrReplay) {
			msg = "token_replayed"
		}
		writeJSON(w, code, map[string]any{"error": msg})
		return
	}

	conn := session.NewConn(nil)
	// Attach BEFORE upgrading so refusals are plain HTTP statuses the client can act on.
	if _, err := s.mgr.Attach(claims, conn); err != nil {
		switch {
		case errors.Is(err, session.ErrBusy), errors.Is(err, session.ErrPeerBusy):
			writeJSON(w, 409, map[string]any{"error": "busy", "peer_busy": errors.Is(err, session.ErrPeerBusy)})
		case errors.Is(err, session.ErrEnded):
			writeJSON(w, 410, map[string]any{"error": "call_ended"})
		default:
			writeJSON(w, 403, map[string]any{"error": "forbidden"})
		}
		return
	}
	up, err := s.upgrader.Upgrade(w, r, http.Header{})
	if err != nil {
		s.mgr.Detach(claims, conn)
		return
	}
	s.sockets.Add(1)
	defer s.sockets.Add(-1)

	go s.writePump(up, conn)
	s.readPump(up, claims, conn)
}

const (
	readLimit  = 64 * 1024
	pongWait   = 45 * time.Second
	pingPeriod = 20 * time.Second
)

func (s *Server) readPump(c *websocket.Conn, claims *auth.Claims, conn *session.Conn) {
	defer func() {
		s.mgr.Detach(claims, conn)
		conn.Close(1000, "bye")
		_ = c.Close()
	}()
	c.SetReadLimit(readLimit)
	_ = c.SetReadDeadline(time.Now().Add(pongWait))
	c.SetPongHandler(func(string) error { return c.SetReadDeadline(time.Now().Add(pongWait)) })
	for {
		mt, data, err := c.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure, websocket.CloseNoStatusReceived) {
				log.Printf("ws read user=%d call=%s: %v", claims.UserID, claims.CallID, err)
			}
			return
		}
		if mt != websocket.TextMessage {
			continue
		}
		_ = c.SetReadDeadline(time.Now().Add(pongWait)) // any traffic proves liveness
		s.mgr.Handle(claims, conn, data)
	}
}

func (s *Server) writePump(c *websocket.Conn, conn *session.Conn) {
	t := time.NewTicker(pingPeriod)
	defer t.Stop()
	write := func(msg []byte) bool {
		_ = c.SetWriteDeadline(time.Now().Add(10 * time.Second))
		return c.WriteMessage(websocket.TextMessage, msg) == nil
	}
	for {
		select {
		case msg := <-conn.Send:
			if !write(msg) {
				_ = c.Close()
				return
			}
		case req := <-conn.CloseReq:
			for { // flush what is already queued (e.g. the final session_end), then say goodbye
				select {
				case msg := <-conn.Send:
					write(msg)
					continue
				default:
				}
				break
			}
			_ = c.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(req.Code, req.Reason), time.Now().Add(time.Second))
			_ = c.Close()
			return
		case <-t.C:
			_ = c.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := c.WriteMessage(websocket.PingMessage, nil); err != nil {
				_ = c.Close()
				return
			}
		}
	}
}
