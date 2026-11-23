package config

import (
	"strings"
	"testing"
)

func set(t *testing.T, kv map[string]string) {
	for _, k := range []string{"CALLING_HMAC_SECRET", "ALLOWED_ORIGINS", "TURN_URLS", "TURN_SHARED_SECRET", "TURN_STATIC_USERNAME", "TURN_STATIC_PASSWORD", "REQUIRE_NATIVE"} {
		t.Setenv(k, "")
	}
	for k, v := range kv {
		t.Setenv(k, v)
	}
}

const good = "0123456789abcdef0123456789abcdef"

func TestValidConfig(t *testing.T) {
	set(t, map[string]string{"CALLING_HMAC_SECRET": good, "ALLOWED_ORIGINS": "https://me.pythonanywhere.com, http://localhost:5000/"})
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if !c.OriginAllowed("https://me.pythonanywhere.com") || !c.OriginAllowed("http://localhost:5000") || c.OriginAllowed("https://evil.example") ||
		c.OriginAllowed("https://me.pythonanywhere.com.evil.example") || c.OriginAllowed("") || !c.RequireNative {
		t.Fatalf("%+v", c)
	}
}

func TestUnsafeConfigIsRefused(t *testing.T) {
	cases := map[string]map[string]string{
		"no secret":        {"ALLOWED_ORIGINS": "https://a.example"},
		"short secret":     {"CALLING_HMAC_SECRET": "short", "ALLOWED_ORIGINS": "https://a.example"},
		"no origins":       {"CALLING_HMAC_SECRET": good},
		"wildcard":         {"CALLING_HMAC_SECRET": good, "ALLOWED_ORIGINS": "*"},
		"wildcard host":    {"CALLING_HMAC_SECRET": good, "ALLOWED_ORIGINS": "https://*.example.com"},
		"http remote":      {"CALLING_HMAC_SECRET": good, "ALLOWED_ORIGINS": "http://a.example"},
		"origin with path": {"CALLING_HMAC_SECRET": good, "ALLOWED_ORIGINS": "https://a.example/app"},
		"turn no secret":   {"CALLING_HMAC_SECRET": good, "ALLOWED_ORIGINS": "https://a.example", "TURN_URLS": "turn:t.example:3478"},
		"turn bad scheme":  {"CALLING_HMAC_SECRET": good, "ALLOWED_ORIGINS": "https://a.example", "TURN_URLS": "http://t.example", "TURN_SHARED_SECRET": "x"},
	}
	for name, env := range cases {
		set(t, env)
		if _, err := Load(); err == nil {
			t.Errorf("%s: must be refused", name)
		} else if strings.Contains(err.Error(), good) {
			t.Errorf("%s: error must not leak the secret", name)
		}
	}
}
