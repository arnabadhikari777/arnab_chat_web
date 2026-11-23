# Deploy the calling server to Render

Render hosts **only** the calling infrastructure. The Flask app and its database stay on PythonAnywhere.

## 1. GitHub repository layout
Push the ZIP's top folder (`goppo-full/`) contents as the repository root:
```
Dockerfile            ← root Dockerfile (build context = repository root)
render.yaml           ← optional Blueprint
audio-c/  media-engine/  secure-media-rs/  calling-server/  scripts/
chat-web/ docs/       ← may stay in the repo; .dockerignore keeps them out of the image build
```
Do not commit `.env`, `native/`, `target/`, `node_modules/`.

## 2. Create the service (Dashboard → New → Web Service)
| Setting | Value |
|---|---|
| Source | your GitHub repo |
| Language / Runtime | **Docker** |
| Root Directory | *(leave empty)* |
| Dockerfile Path | `./Dockerfile` |
| Docker Build Context Directory | `.` |
| Branch | `main` |
| Instance type | Free to test; **Starter or higher** for real use (see limits) |
| **Health Check Path** | **`/health`** |
| Auto-Deploy | on |
(Or use **New → Blueprint** with `render.yaml`.)

## 3. Environment variables (Render only — full list in `calling-server/.env.example`)
| Variable | Required | Value |
|---|---|---|
| `CALLING_HMAC_SECRET` | yes | ≥32 chars, **identical** to PythonAnywhere |
| `ALLOWED_ORIGINS` | yes | `https://YOURNAME.pythonanywhere.com` (add `http://localhost:5000` only for local testing) |
| `STUN_URLS` | no | default `stun:stun.l.google.com:19302` |
| `TURN_URLS` | recommended | `turn:HOST:3478?transport=udp,turn:HOST:3478?transport=tcp,turns:HOST:5349?transport=tcp` |
| `TURN_SHARED_SECRET` | with TURN | coturn `static-auth-secret` (server derives short-lived credentials) |
| `TURN_STATIC_USERNAME` / `TURN_STATIC_PASSWORD` | alternative | only if the provider has no shared-secret mode |
| `REQUIRE_NATIVE` | no | `true` (default) |
Do **not** set `PORT` (Render provides it). The server refuses to start with a missing/short secret, an empty or wildcard `ALLOWED_ORIGINS`, or TURN URLs without credentials.

## 4. What happens during the build
`Dockerfile` stage 1 compiles C, C++ and Rust and runs their unit tests → stage 2 links the Go server with CGO, runs `go vet`, `go test`, and `goppo-calling -selftest` → stage 3 copies the binary into `debian:bookworm-slim` (+`libopus0`) and runs the self-test again. Any failure fails the deploy. First build takes several minutes (Rust).

## 5. Verify the deployment
```bash
curl https://YOUR-SERVICE.onrender.com/health        # {"status":"ok",...}
curl https://YOUR-SERVICE.onrender.com/ready         # 200 {"ready":true,"native_verified":true}
curl https://YOUR-SERVICE.onrender.com/components    # c, c++, rust: loaded=true verified=true
curl -i -H "Origin: https://evil.example" https://YOUR-SERVICE.onrender.com/health   # no Access-Control-Allow-Origin header
curl -i -H "Origin: https://YOURNAME.pythonanywhere.com" https://YOUR-SERVICE.onrender.com/health  # header present
curl -i https://YOUR-SERVICE.onrender.com/ice        # 401 (needs a call token)
```
Render logs show one `component … verified=true` line per language at startup.

## STUN / TURN
* STUN alone connects many home/Wi-Fi pairs, **not** symmetric NATs or many mobile carrier networks. For those you need TURN.
* **Render cannot host TURN** (no UDP/relay port ranges for web services). Use coturn on a small VPS or a TURN provider.
  coturn essentials: `use-auth-secret`, `static-auth-secret=<TURN_SHARED_SECRET>`, `realm`, `listening-port=3478`, `tls-listening-port=5349` with a real certificate (for `turns:`), relay port range open in the firewall.
* The server returns short-lived (default 1 h) TURN credentials from `/ice` only to holders of a valid call token. No permanent credential is in any JavaScript or public endpoint when you use the shared-secret mode.

## Free instance vs paid
| | Free | Starter / paid |
|---|---|---|
| Idle | sleeps after ~15 min without traffic; **cold start ≈ 30–60 s** (the app shows "Waking up…", polls `/ready`, 90 s limit, Retry/Cancel) | always on, no wake-up |
| Restart/deploy during a call | call drops (clients reconnect for 20 s, then end) | same, but less frequent |
| Resources | shared 0.1 CPU, 512 MB | more; signaling needs very little |
| Monthly hours | limited pool shared across free services | n/a |
Check Render's current pricing/limits page; they change. For daily use choose a paid always-on instance.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Build fails at `build_native.sh` | read which step; every native unit test runs there |
| Service exits: "FATAL native components not operational" | image built without libs; rebuild (clear build cache) |
| Exits: "configuration error: …" | the message names the variable |
| Browser: CORS error | `ALLOWED_ORIGINS` must equal the **exact** site origin (scheme + host, no trailing path) |
| `/ws` 401 `token_replayed` | a token opens one socket; the client fetches a new one on retry (clock skew > 30 s between hosts also fails tokens) |
| Calls connect only on Wi-Fi | TURN missing/misconfigured: check `/ice` shows `turn_configured:true` after login |
