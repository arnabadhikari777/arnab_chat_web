# Native component integration report

All three native libraries are linked into the Go binary through **one cgo package** (`calling-server/internal/native`), built as static
libraries by `scripts/build_native.sh`. There is **no Go fallback**: without the libraries the Go build fails (verified), and at startup each
component runs a real self-test; `REQUIRE_NATIVE=true` (default) stops the server if any fails.

| Language | Library | Real work on the live path | Where it runs | How to verify |
|---|---|---|---|---|
| **Rust** | `libgoppo_secure_media.a` | `check_signal` for **every** offer/answer/ICE/hangup/reject/busy/ready/quality message: participant check, role vs type, state machine (answer only after offer, nothing after end), SDP validation (must contain a DTLS fingerprint, `UDP/TLS/RTP/SAVPF`, no voice-call video), ICE candidate syntax, size limits, per-user token bucket; per-call quality EWMA → 0–100 score | `session.Handle` | `GET /components` → `signals_checked`, `signals_rejected`, `quality_updates`; Go test `TestRustValidationThroughCgo` |
| **C++** | `libgoppo_media.a` | `goppo_q_*` quality engine fed by each browser's `quality` telemetry (every 2 s): p50/p95 RTT, p95 jitter, mean loss, A/V playout skew, jitter-buffer target, silent-microphone detection, advice level → client pauses video on a bad network | `session.handleQuality` | `/components` → `telemetry_samples_processed`; e2e test asserts the counter advances |
| **C** | `libgoppo_audio.a` | ring buffers (used by every C++ window), `goppo_level_to_dbfs` (mic level), Opus encoder/decoder, PCM gain/limiter/convert. Startup **Opus round-trip self-test** (440 Hz tone, RMS ratio checked) | called from the C++ engine and `native.Init` | `/components` → `opus_bytes_per_20ms_frame`, `rms_ratio`; `scripts/build_native.sh` runs `audio-c/tests/test_audio.c` |

## What is deliberately NOT claimed
* No native code touches RTP/media packets of calls: they never reach the server (see `ARCHITECTURE.md`).
* The Opus encode/decode and PCM helpers in C are exercised by the **self-test and the C++ frame self-test**, not on live call audio.
* `inspect_rtp` (Rust, RTP header metadata inspection) is implemented and unit-tested but is **not on the live P2P path**; it is available for a future TURN/SFU inspection layer.
* No WebAssembly is used (the empty `wasm/` folder of the earlier ZIP was removed).

## Build and linking details
* glibc everywhere (Debian bookworm): Rust `rust:1.82-bookworm`, Go `golang:1.22-bookworm`, runtime `debian:bookworm-slim` + `libopus0`. No musl/glibc mixing.
* `CGO_ENABLED=1`; link line: `-lgoppo_media -lgoppo_audio -lgoppo_secure_media -lopus -lstdc++ -lm -ldl -lpthread -lgcc_s -lutil -lrt`.
* Rust built with `panic = "abort"`, LTO, no third-party crates (`Cargo.lock` committed, `--locked`).
* Dockerfile fails if: a library is missing, a native unit test fails, `CGO_ENABLED≠1`, `go vet`/`go test` fail, `goppo-calling -selftest` fails (both in the build stage and in the final image), or `ldd` shows a missing shared library.
