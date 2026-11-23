// Package turn selects and obtains ICE servers from the active TURN provider (Metered or Turnix).
// The API token for Turnix never leaves this package / the Render process.
package turn

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/goppo/calling-server/internal/auth"
)

const (
	ProviderMetered = "metered"
	ProviderTurnix  = "turnix"
)

// Settings is the durable (or in-memory) provider selection. Secrets are never stored here.
type Settings struct {
	ActiveProvider string `json:"active_provider"` // "metered" | "turnix"
	UpdatedAt      int64  `json:"updated_at,omitempty"`
}

// Status describes what the admin panel shows.
type Status struct {
	ActiveProvider     string `json:"active_provider"`
	MeteredConfigured  bool   `json:"metered_configured"`
	TurnixConfigured   bool   `json:"turnix_configured"`
	LastTestProvider   string `json:"last_test_provider,omitempty"`
	LastTestOK         bool   `json:"last_test_ok,omitempty"`
	LastTestMessage    string `json:"last_test_message,omitempty"`
	LastTestAt         int64  `json:"last_test_at,omitempty"`
	ConfigPath         string `json:"config_path,omitempty"`
	Persistent         bool   `json:"persistent"`
	Note               string `json:"note,omitempty"`
}

// Manager holds env-based credentials and the active provider selection.
type Manager struct {
	mu sync.RWMutex

	// Metered (existing env)
	STUNURLs         []string
	TURNURLs         []string
	TURNSharedSecret string
	TURNStaticUser   string
	TURNStaticPass   string
	TURNCredTTL      time.Duration

	// Turnix
	TurnixToken string

	// Selection
	settings   Settings
	configPath string
	persistent bool

	// Last test result (in-memory only)
	lastTestProvider string
	lastTestOK       bool
	lastTestMessage  string
	lastTestAt       int64

	httpClient *http.Client
}

func NewManager(
	stunURLs, turnURLs []string,
	sharedSecret, staticUser, staticPass string,
	ttl time.Duration,
	turnixToken, configPath string,
) *Manager {
	m := &Manager{
		STUNURLs:         stunURLs,
		TURNURLs:         turnURLs,
		TURNSharedSecret: sharedSecret,
		TURNStaticUser:   staticUser,
		TURNStaticPass:   staticPass,
		TURNCredTTL:      ttl,
		TurnixToken:      turnixToken,
		configPath:       configPath,
		httpClient:       &http.Client{Timeout: 8 * time.Second},
		settings: Settings{
			ActiveProvider: ProviderMetered, // safe default
		},
	}
	m.load()
	return m
}

func (m *Manager) load() {
	if m.configPath == "" {
		m.persistent = false
		return
	}
	data, err := os.ReadFile(m.configPath)
	if err != nil {
		m.persistent = false
		// Directory may not exist yet; try to create parent for future saves.
		_ = os.MkdirAll(filepath.Dir(m.configPath), 0o700)
		return
	}
	var s Settings
	if err := json.Unmarshal(data, &s); err != nil {
		m.persistent = false
		return
	}
	if s.ActiveProvider != ProviderMetered && s.ActiveProvider != ProviderTurnix {
		s.ActiveProvider = ProviderMetered
	}
	// Never activate Turnix if token is missing.
	if s.ActiveProvider == ProviderTurnix && m.TurnixToken == "" {
		s.ActiveProvider = ProviderMetered
	}
	m.settings = s
	m.persistent = true
}

func (m *Manager) save() error {
	if m.configPath == "" {
		m.persistent = false
		return errors.New("no config path configured (settings are in-memory only)")
	}
	if err := os.MkdirAll(filepath.Dir(m.configPath), 0o700); err != nil {
		m.persistent = false
		return err
	}
	m.settings.UpdatedAt = time.Now().Unix()
	data, err := json.MarshalIndent(m.settings, "", "  ")
	if err != nil {
		return err
	}
	tmp := m.configPath + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		m.persistent = false
		return err
	}
	if err := os.Rename(tmp, m.configPath); err != nil {
		m.persistent = false
		return err
	}
	m.persistent = true
	return nil
}

func (m *Manager) Status() Status {
	m.mu.RLock()
	defer m.mu.RUnlock()
	note := ""
	if !m.persistent {
		note = "Provider selection is not durable on this host (no persistent disk or config path unwritable). It resets to Metered (or ACTIVE_TURN_PROVIDER env) after restart. Attach a Render disk and set TURN_CONFIG_PATH for persistence."
	}
	return Status{
		ActiveProvider:    m.settings.ActiveProvider,
		MeteredConfigured: m.meteredOK(),
		TurnixConfigured:  m.TurnixToken != "",
		LastTestProvider:  m.lastTestProvider,
		LastTestOK:        m.lastTestOK,
		LastTestMessage:   m.lastTestMessage,
		LastTestAt:        m.lastTestAt,
		ConfigPath:        m.configPath,
		Persistent:        m.persistent,
		Note:              note,
	}
}

func (m *Manager) meteredOK() bool {
	return len(m.TURNURLs) > 0 && (m.TURNSharedSecret != "" || (m.TURNStaticUser != "" && m.TURNStaticPass != ""))
}

func (m *Manager) Active() string {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.settings.ActiveProvider
}

// SetActive switches provider. Turnix is refused if token missing or last successful test is required by caller.
func (m *Manager) SetActive(provider string, requirePriorTest bool) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	switch provider {
	case ProviderMetered:
		if !m.meteredOK() {
			return errors.New("Metered is not configured (set TURN_URLS and credentials)")
		}
		m.settings.ActiveProvider = ProviderMetered
	case ProviderTurnix:
		if m.TurnixToken == "" {
			return errors.New("Turnix is not configured (set TURNIX_API_TOKEN on Render)")
		}
		if requirePriorTest && !(m.lastTestOK && m.lastTestProvider == ProviderTurnix) {
			return errors.New("Turnix has not been successfully tested yet; run Test Provider first")
		}
		m.settings.ActiveProvider = ProviderTurnix
	default:
		return fmt.Errorf("unknown provider %q", provider)
	}
	return m.save()
}

// TestProvider fetches credentials (or validates Metered config) without changing the active provider.
func (m *Manager) TestProvider(ctx context.Context, provider string) (ok bool, message string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.lastTestProvider = provider
	m.lastTestAt = time.Now().Unix()

	switch provider {
	case ProviderMetered:
		if !m.meteredOK() {
			m.lastTestOK = false
			m.lastTestMessage = "Metered not configured"
			return false, m.lastTestMessage
		}
		m.lastTestOK = true
		m.lastTestMessage = "Metered configuration looks valid"
		return true, m.lastTestMessage
	case ProviderTurnix:
		if m.TurnixToken == "" {
			m.lastTestOK = false
			m.lastTestMessage = "TURNIX_API_TOKEN is not set"
			return false, m.lastTestMessage
		}
		servers, ttl, err := m.fetchTurnix(ctx)
		if err != nil {
			m.lastTestOK = false
			m.lastTestMessage = err.Error()
			return false, m.lastTestMessage
		}
		n := 0
		for _, s := range servers {
			if urls, ok := s["urls"]; ok {
				switch u := urls.(type) {
				case []any:
					n += len(u)
				case []string:
					n += len(u)
				case string:
					n++
				}
			}
		}
		m.lastTestOK = true
		m.lastTestMessage = fmt.Sprintf("Turnix OK: %d ICE entry(ies), ttl~%ds", n, int(ttl.Seconds()))
		return true, m.lastTestMessage
	default:
		m.lastTestOK = false
		m.lastTestMessage = "unknown provider"
		return false, m.lastTestMessage
	}
}

// ICEServers returns iceServers for the active provider. On failure it does NOT silently fall back to STUN-only.
func (m *Manager) ICEServers(ctx context.Context, userID int64) (servers []map[string]any, ttlSeconds int, turnConfigured bool, err error) {
	m.mu.RLock()
	active := m.settings.ActiveProvider
	m.mu.RUnlock()

	switch active {
	case ProviderTurnix:
		if m.TurnixToken == "" {
			return nil, 0, false, errors.New("Turnix selected but TURNIX_API_TOKEN is not configured")
		}
		servers, ttl, err := m.fetchTurnix(ctx)
		if err != nil {
			return nil, 0, false, fmt.Errorf("Turnix credentials failed: %w", err)
		}
		return servers, int(ttl.Seconds()), true, nil
	default: // metered
		servers = []map[string]any{}
		if len(m.STUNURLs) > 0 {
			servers = append(servers, map[string]any{"urls": m.STUNURLs})
		}
		ttl := m.TURNCredTTL
		if len(m.TURNURLs) > 0 {
			e := map[string]any{"urls": m.TURNURLs}
			if m.TURNSharedSecret != "" {
				e["username"], e["credential"] = auth.TURNCredential(m.TURNSharedSecret, userID, ttl, time.Now())
			} else {
				e["username"], e["credential"] = m.TURNStaticUser, m.TURNStaticPass
			}
			servers = append(servers, e)
			return servers, int(ttl.Seconds()), true, nil
		}
		// Metered active but no TURN URLs: still return STUN so easy networks work; mark turn_configured false.
		return servers, int(ttl.Seconds()), false, nil
	}
}

type turnixResponse struct {
	IceServers []map[string]any `json:"iceServers"`
	ExpiresAt  string           `json:"expiresAt"`
	Error      string           `json:"error"`
	Message    string           `json:"message"`
}

func (m *Manager) fetchTurnix(ctx context.Context) ([]map[string]any, time.Duration, error) {
	body := map[string]any{"ttl": int(m.TURNCredTTL.Seconds())}
	if body["ttl"].(int) <= 0 {
		body["ttl"] = 3600
	}
	raw, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, "https://turnix.io/api/v1/credentials/ice", bytes.NewReader(raw))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Authorization", "Bearer "+m.TurnixToken)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	resp, err := m.httpClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("Turnix request failed: %w", err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		var tr turnixResponse
		_ = json.Unmarshal(data, &tr)
		msg := tr.Error
		if msg == "" {
			msg = tr.Message
		}
		if msg == "" {
			msg = string(data)
		}
		if len(msg) > 200 {
			msg = msg[:200]
		}
		return nil, 0, fmt.Errorf("Turnix HTTP %d: %s", resp.StatusCode, msg)
	}
	var tr turnixResponse
	if err := json.Unmarshal(data, &tr); err != nil {
		return nil, 0, fmt.Errorf("Turnix invalid JSON: %w", err)
	}
	if len(tr.IceServers) == 0 {
		return nil, 0, errors.New("Turnix returned empty iceServers")
	}
	ttl := m.TURNCredTTL
	if tr.ExpiresAt != "" {
		if t, err := time.Parse(time.RFC3339, tr.ExpiresAt); err == nil {
			d := time.Until(t)
			if d > 30*time.Second {
				ttl = d
			}
		}
	}
	return tr.IceServers, ttl, nil
}
