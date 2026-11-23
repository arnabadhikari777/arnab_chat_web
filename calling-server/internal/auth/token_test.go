package auth

import (
	"strings"
	"testing"
	"time"
)

var secret = []byte("0123456789abcdef0123456789abcdef")

func claims(now time.Time) Claims {
	return Claims{Version: 1, UserID: 1, PeerID: 2, Conv: 9, CallType: "voice", CallID: "call-1", Role: "caller",
		JTI: "jti-0123456789abcdef", IssuedAt: now.Unix(), Exp: now.Add(2 * time.Minute).Unix()}
}

func TestValidTokenAndReplay(t *testing.T) {
	v := NewValidator(secret, 5*time.Minute)
	tok := Sign(secret, claims(time.Now()))
	c, err := v.Verify(tok)
	if err != nil || c.Role != "caller" || c.UserID != 1 {
		t.Fatalf("verify: %v %+v", err, c)
	}
	if _, err := v.Verify(tok); err != nil {
		t.Fatal("Verify must not consume the token (used by /ice)")
	}
	if _, err := v.Consume(tok); err != nil {
		t.Fatal(err)
	}
	if _, err := v.Consume(tok); err != ErrReplay {
		t.Fatalf("second use must be a replay, got %v", err)
	}
}

func TestRejections(t *testing.T) {
	v := NewValidator(secret, 5*time.Minute)
	now := time.Now()
	good := claims(now)
	mut := func(f func(*Claims)) string { c := good; f(&c); return Sign(secret, c) }
	cases := map[string]string{
		"wrong secret":      Sign([]byte("another-secret-another-secret-xxxxx"), good),
		"expired":           mut(func(c *Claims) { c.Exp = now.Add(-time.Second).Unix() }),
		"lifetime too long": mut(func(c *Claims) { c.Exp = now.Add(time.Hour).Unix() }),
		"future iat":        mut(func(c *Claims) { c.IssuedAt = now.Add(time.Hour).Unix() }),
		"bad role":          mut(func(c *Claims) { c.Role = "admin" }),
		"no role":           mut(func(c *Claims) { c.Role = "" }),
		"self call":         mut(func(c *Claims) { c.PeerID = c.UserID }),
		"bad type":          mut(func(c *Claims) { c.CallType = "screen" }),
		"short jti":         mut(func(c *Claims) { c.JTI = "x" }),
		"version":           mut(func(c *Claims) { c.Version = 2 }),
		"garbage":           "not-a-token",
		"empty":             "",
		"huge":              strings.Repeat("a", 5000),
	}
	for name, tok := range cases {
		if _, err := v.Verify(tok); err == nil {
			t.Errorf("%s: token must be refused", name)
		}
	}
	// tamper with the payload but keep the signature
	parts := strings.Split(Sign(secret, good), ".")
	tampered := Sign(secret, func() Claims { c := good; c.UserID = 99; return c }())
	if _, err := v.Verify(strings.Split(tampered, ".")[0] + "." + parts[1]); err != ErrSignature {
		t.Fatalf("tampered payload must fail the signature, got %v", err)
	}
}

func TestTURNCredentialMatchesCoturnScheme(t *testing.T) {
	now := time.Unix(1_700_000_000, 0)
	u, p := TURNCredential("shared", 42, time.Hour, now)
	if u != "1700003600:42" {
		t.Fatalf("username %q", u)
	}
	u2, p2 := TURNCredential("shared", 42, time.Hour, now)
	if u != u2 || p != p2 || p == "" {
		t.Fatal("credential must be deterministic")
	}
	if _, p3 := TURNCredential("other", 42, time.Hour, now); p3 == p {
		t.Fatal("credential must depend on the secret")
	}
}
