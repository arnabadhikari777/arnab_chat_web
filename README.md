# Goppo Chat — advanced voice & video calling (repaired, complete project)

```
goppo-full/
├── chat-web/            Flask app (PythonAnywhere): chat, auth, push, admin + call API, state machine, tokens, migrations, calling.js
├── calling-server/      Go signaling server (Render): reliable signaling, auth, ICE, health/ready/components
├── audio-c/             C: Opus, PCM, ring buffers (+tests)
├── media-engine/        C++: telemetry quality engine (+tests)
├── secure-media-rs/     Rust: signaling validation, session state machine, quality score (+tests)
├── integration-tests/   end-to-end test: real Go binary + Flask + WebSockets
├── scripts/             build_native.sh  build_all.sh  test_all.sh
├── Dockerfile  render.yaml  .dockerignore      (Render build; context = this folder)
└── docs/                ARCHITECTURE, NATIVE_INTEGRATION_REPORT, DEPLOY_PYTHONANYWHERE, DEPLOY_RENDER,
                         TESTING_REPORT, KNOWN_LIMITATIONS, DEPLOYMENT_CHECKLIST
```
Environment files: `chat-web/.env.example` (PythonAnywhere only) and `calling-server/.env.example` (Render only). `CALLING_HMAC_SECRET` must be identical on both.

Start with `docs/DEPLOYMENT_CHECKLIST.md`. Read `docs/TESTING_REPORT.md` and `docs/KNOWN_LIMITATIONS.md` first: they say exactly what was tested here and what still needs two real devices.
Local build + all tests: `./scripts/test_all.sh`.
