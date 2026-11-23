// Package config reads and validates the server's environment. Invalid or unsafe settings stop the server at startup.
package config

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Port             string
	Secret           []byte
	AllowedOrigins   []string
	STUNURLs         []string
	TURNURLs         []string
	TURNSharedSecret string // coturn "use-auth-secret": short-lived credentials are derived from it
	TURNStaticUser   string // fallback for providers that only give a fixed username/password
	TURNStaticPass   string
	TURNCredTTL      time.Duration
	RequireNative    bool
	RingTimeout      time.Duration
	ReconnectGrace   time.Duration
	TokenMaxAge      time.Duration

	// Turnix (Render only; token never sent to browsers or PythonAnywhere)
	TurnixAPIToken string

	// Admin panel (Render only)
	AdminPassword string

	// Provider selection persistence and bootstrap
	TurnConfigPath     string // e.g. /data/turn_config.json when a Render disk is mounted
	ActiveTurnProvider string // optional env bootstrap: "metered" | "turnix" (default metered)
}

func Load() (*Config, error) {
	c := &Config{
		Port:               env("PORT", "8080"),
		STUNURLs:           split(env("STUN_URLS", "stun:stun.l.google.com:19302")),
		TURNURLs:           split(env("TURN_URLS", "")),
		TURNSharedSecret:   env("TURN_SHARED_SECRET", ""),
		TURNStaticUser:     env("TURN_STATIC_USERNAME", ""),
		TURNStaticPass:     env("TURN_STATIC_PASSWORD", ""),
		TURNCredTTL:        dur("TURN_CREDENTIAL_TTL_SECONDS", 3600),
		RequireNative:      env("REQUIRE_NATIVE", "true") != "false",
		RingTimeout:        dur("RING_TIMEOUT_SECONDS", 75),
		ReconnectGrace:     dur("RECONNECT_GRACE_SECONDS", 20),
		TokenMaxAge:        dur("TOKEN_MAX_AGE_SECONDS", 300),
		TurnixAPIToken:     env("TURNIX_API_TOKEN", ""),
		AdminPassword:      env("ADMIN_PASSWORD", ""),
		TurnConfigPath:     env("TURN_CONFIG_PATH", ""),
		ActiveTurnProvider: strings.ToLower(env("ACTIVE_TURN_PROVIDER", "metered")),
	}
	secret := env("CALLING_HMAC_SECRET", "")
	if len(secret) < 32 {
		return nil, errors.New("CALLING_HMAC_SECRET is required and must be at least 32 characters")
	}
	c.Secret = []byte(secret)

	for _, o := range split(env("ALLOWED_ORIGINS", "")) {
		if strings.Contains(o, "*") {
			return nil, errors.New("wildcard origins are not allowed")
		}
		u, err := url.Parse(o)
		if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" || (u.Path != "" && u.Path != "/") || u.RawQuery != "" {
			return nil, fmt.Errorf("ALLOWED_ORIGINS entry %q is not a plain origin like https://example.com", o)
		}
		if u.Scheme == "http" && !isLocalHost(u.Hostname()) {
			return nil, fmt.Errorf("ALLOWED_ORIGINS entry %q: plain http is only allowed for localhost", o)
		}
		c.AllowedOrigins = append(c.AllowedOrigins, u.Scheme+"://"+u.Host)
	}
	if len(c.AllowedOrigins) == 0 {
		return nil, errors.New("ALLOWED_ORIGINS is required (comma separated exact origins)")
	}
	for _, t := range c.TURNURLs {
		if !strings.HasPrefix(t, "turn:") && !strings.HasPrefix(t, "turns:") {
			return nil, fmt.Errorf("TURN_URLS entry %q must start with turn: or turns:", t)
		}
	}
	if len(c.TURNURLs) > 0 && c.TURNSharedSecret == "" && (c.TURNStaticUser == "" || c.TURNStaticPass == "") {
		return nil, errors.New("TURN_URLS is set but neither TURN_SHARED_SECRET nor TURN_STATIC_USERNAME/PASSWORD is")
	}
	if c.ActiveTurnProvider != "metered" && c.ActiveTurnProvider != "turnix" {
		c.ActiveTurnProvider = "metered"
	}
	return c, nil
}

func (c *Config) OriginAllowed(origin string) bool {
	for _, o := range c.AllowedOrigins {
		if o == origin {
			return true
		}
	}
	return false
}

func isLocalHost(h string) bool { return h == "localhost" || h == "127.0.0.1" || h == "::1" }

func env(k, d string) string {
	if v := strings.TrimSpace(os.Getenv(k)); v != "" {
		return v
	}
	return d
}

func dur(k string, defSeconds int) time.Duration {
	if v := env(k, ""); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return time.Duration(n) * time.Second
		}
	}
	return time.Duration(defSeconds) * time.Second
}

func split(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
