// Package auth validates the short-lived signed call tokens issued by the Flask app and derives TURN credentials.
//
// Token = base64url(JSON claims) "." base64url(HMAC-SHA256(secret, base64url(JSON claims)))
// The role (caller/callee) is part of the SIGNED claims; nothing about identity is read from the URL.
package auth

import (
	"crypto/hmac"
	"crypto/sha1" // #nosec G505 -- required by the coturn REST-credential scheme, not used for integrity of our tokens
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
)

type Claims struct {
	Version  int    `json:"v"`
	UserID   int64  `json:"uid"`
	PeerID   int64  `json:"pid"`
	Conv     int64  `json:"cid"`
	CallType string `json:"type"` // voice | video
	CallID   string `json:"call_id"`
	Role     string `json:"role"` // caller | callee
	JTI      string `json:"jti"`
	IssuedAt int64  `json:"iat"`
	Exp      int64  `json:"exp"`
}

var (
	ErrFormat    = errors.New("invalid token format")
	ErrSignature = errors.New("invalid signature")
	ErrExpired   = errors.New("token expired")
	ErrClaims    = errors.New("invalid claims")
	ErrReplay    = errors.New("token already used")
)

type Validator struct {
	secret []byte
	maxAge time.Duration
	now    func() time.Time
	mu     sync.Mutex
	used   map[string]int64 // jti -> exp (unix)
}

func NewValidator(secret []byte, maxAge time.Duration) *Validator {
	return &Validator{secret: secret, maxAge: maxAge, now: time.Now, used: map[string]int64{}}
}

// Verify checks signature, expiry and claim sanity but does NOT consume the token (used by /ice).
func (v *Validator) Verify(token string) (*Claims, error) {
	if len(token) > 2048 {
		return nil, ErrFormat
	}
	parts := strings.Split(token, ".")
	if len(parts) != 2 {
		return nil, ErrFormat
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, ErrFormat
	}
	mac := hmac.New(sha256.New, v.secret)
	mac.Write([]byte(parts[0]))
	if !hmac.Equal(sig, mac.Sum(nil)) {
		return nil, ErrSignature
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return nil, ErrFormat
	}
	var c Claims
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, ErrFormat
	}
	now := v.now().Unix()
	if c.Exp <= now {
		return nil, ErrExpired
	}
	if c.IssuedAt > now+30 || (v.maxAge > 0 && c.Exp-c.IssuedAt > int64(v.maxAge.Seconds())) {
		return nil, fmt.Errorf("%w: lifetime", ErrClaims)
	}
	if c.Version != 1 || c.UserID <= 0 || c.PeerID <= 0 || c.UserID == c.PeerID || c.CallID == "" || len(c.CallID) > 64 || len(c.JTI) < 16 {
		return nil, ErrClaims
	}
	if c.Role != "caller" && c.Role != "callee" {
		return nil, fmt.Errorf("%w: role", ErrClaims)
	}
	if c.CallType != "voice" && c.CallType != "video" {
		return nil, fmt.Errorf("%w: call type", ErrClaims)
	}
	return &c, nil
}

// Consume verifies the token and marks its jti as used: a token opens exactly one WebSocket (replay protection).
func (v *Validator) Consume(token string) (*Claims, error) {
	c, err := v.Verify(token)
	if err != nil {
		return nil, err
	}
	v.mu.Lock()
	defer v.mu.Unlock()
	now := v.now().Unix()
	for k, exp := range v.used { // opportunistic cleanup keeps the map bounded
		if exp <= now {
			delete(v.used, k)
		}
	}
	if _, seen := v.used[c.JTI]; seen {
		return nil, ErrReplay
	}
	v.used[c.JTI] = c.Exp
	return c, nil
}

func (v *Validator) UsedCount() int {
	v.mu.Lock()
	defer v.mu.Unlock()
	return len(v.used)
}

// TURNCredential implements the coturn REST API: username "<expiry>:<user>", password base64(HMAC-SHA1(secret, username)).
func TURNCredential(sharedSecret string, userID int64, ttl time.Duration, now time.Time) (username, credential string) {
	username = fmt.Sprintf("%d:%d", now.Add(ttl).Unix(), userID)
	mac := hmac.New(sha1.New, []byte(sharedSecret)) // #nosec G401
	mac.Write([]byte(username))
	return username, base64.StdEncoding.EncodeToString(mac.Sum(nil))
}

// Sign mints tokens exactly like the Flask app does (used by tests and tools).
func Sign(secret []byte, c Claims) string {
	raw, _ := json.Marshal(c)
	p := base64.RawURLEncoding.EncodeToString(raw)
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(p))
	return p + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}
