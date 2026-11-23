# Architecture

```
 Browser A ──HTTPS──▶ PythonAnywhere (Flask) ◀──HTTPS── Browser B        accounts, chat, call records, tokens, push
     │                    │ signed call token (HMAC, 120 s, role+jti)            ▲ Web Push (incoming call) via service worker
     │                    ▼
     ├────wss://──────▶ Render (Go calling server + C/C++/Rust) ◀────wss────┤   reliable signaling, session rules, quality
     │
     └══════════ WebRTC media, DTLS-SRTP, peer-to-peer (ICE: STUN, TURN as relay) ══════════ Browser B
```

## The media-path decision (important, honest)
Media is **peer-to-peer WebRTC**. Audio/video packets go browser ⇄ browser (or through a TURN relay), **never to the Go server**,
and they are end-to-end encrypted with DTLS-SRTP. Therefore **no server-side C, C++ or Rust code processes media packets**, and this
project does not claim otherwise. The native components work on the *signaling and telemetry plane*, where the server really sees data
(see `NATIVE_INTEGRATION_REPORT.md`).

### Why not an SFU / media server?
Native media processing on the server would require the media to pass through the server (SFU/MCU/relay with decryption or at least RTP
handling). Evaluation for a 1:1 chat site on Render:

| Concern | P2P (chosen) | SFU / media server |
|---|---|---|
| Latency | lowest (direct path) | + one hop via the server's region |
| Server CPU / RAM | almost none (signaling only) | grows with every call; video forwarding needs real CPU and bandwidth |
| Render Free (shared 0.1 CPU, 512 MB, sleeps after ~15 min idle) | works | not viable: cold starts drop live calls; CPU too small |
| UDP | not needed on Render (signaling is HTTPS/WSS) | media needs UDP ports; Render web services expose HTTP(S) only, so an SFU does not fit a Render web service at all (needs a VPS/bare-metal host) |
| Bandwidth cost | none on Render (TURN excepted) | all call media counts as server egress |
| Security | end-to-end DTLS-SRTP | server terminates or sees media metadata |
| Benefit for 1:1 calls | — | none that justifies the cost |

Decision: keep P2P. If you later need group calls, recording or server-side audio processing, add an SFU (e.g. a Go/Pion or LiveKit
deployment) on a VPS with UDP, and move the C/C++/Rust media work there. TURN (relay of encrypted packets) is separate and must be hosted
outside Render (coturn on a VPS or a TURN provider) because TURN needs UDP/TCP relay ports.

## Components
| Part | Hosted on | Responsibility |
|---|---|---|
| Flask app (`chat-web/`) | PythonAnywhere | users, chat, **call records & state machine**, short-lived tokens, incoming-call notice + Web Push, history, admin |
| Go server (`calling-server/`) | Render (Docker) | WebSocket signaling, reliable delivery, session/busy rules, ICE config, health/ready/components |
| Rust `secure-media-rs` | linked into Go (CGO) | validates **every** signaling message (roles, order, SDP/ICE syntax, DTLS fingerprint required), rate limit, call-session registry, quality score |
| C `audio-c` | linked into Go (CGO) | Opus codec, PCM utilities, ring buffers (used by the C++ engine); startup Opus self-test |
| C++ `media-engine` | linked into Go (CGO) | quality engine over browser-reported telemetry: percentiles, A/V sync, jitter-buffer target, silent-mic, video-downgrade advice |
| JS `calling.js` | browser | call UI, wake-up, reliable signaling client, WebRTC, reconnect, stats |

## Call lifecycle
1. Caller taps Voice/Video → microphone/camera permission → **wake Render** (`/ready`, bounded 90 s, Retry/Cancel) →
   `POST /api/calling/initiate` (Flask checks: logged in, verified peer, existing conversation, not busy) → callee notified (in-page poll + Web Push).
2. Caller connects `wss://…/ws` with the signed token (sent as WebSocket sub-protocol, never in a URL), sends the SDP offer.
   The server **queues** it (even though the callee is not connected yet) and reports `queued`.
3. Callee sees the incoming screen (any page, or via push notification), taps Accept → permission → `POST /api/calling/accept` → callee token →
   connects; the server replays the queued offer; the callee **acknowledges its sequence number**; only then the caller is told `delivered`.
4. Answer + ICE candidates flow the same reliable way (ICE buffered on both ends until the remote description exists).
5. WebRTC connects; both browsers report `connected` (Flask starts the duration clock from the **server's** time), quality telemetry every 2 s.
6. Hang-up → `hangup` signal + `POST /api/calling/status completed`. Duration is computed by Flask, never taken from the browser.

## Reliability and security rules (all enforced server-side)
* Roles come from the signed token, derived from the call record in Flask. Query parameters are ignored.
* Token: HMAC-SHA256, `exp` ≤ 120 s, random `jti`, single use at the WebSocket (replay protection). Reconnect = fresh token (`/api/calling/token`).
* One live call per user (Flask DB + Go sessions): busy → HTTP 409. One socket per role (newest wins, old one told `replaced`).
* Messages: ids de-duplicated, per-recipient ordering, ack-based delivery, 60–90 s expiry reported to the sender, bounded backlog (256), 64 KB frames, token-bucket rate limit (Rust).
* Reconnect grace 20 s, ring timeout 75 s (server) / 60 s (Flask → "missed" + missed-call notice).
* CORS: exact origin allow-list (no wildcard, startup refuses one); `/ws` requires an allowed `Origin`; `/ice` requires a valid call token; TURN credentials are short-lived (coturn REST scheme).
* Flask: CSRF enforced on every calling POST; CSP `connect-src` extended with the calling server's `https://` and `wss://` origin only; `Permissions-Policy: camera=(self), microphone=(self)`.
