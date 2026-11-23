# syntax=docker/dockerfile:1
# =====================================================================================================================
# Goppo calling server (Render). Build context = repository root (the folder that contains this file).
#   Stage 1  native : C (Opus audio) + C++ (media/quality engine) + Rust (secure media) static libraries, with unit tests
#   Stage 2  gobuild: Go server linked with CGO against those libraries, Go tests, and a native self-test
#   Stage 3  runtime: small Debian image; same glibc family as the build stages (no musl/glibc mix)
# The build FAILS if any native library is missing, any native unit test fails, CGO is off, or the self-test fails.
# =====================================================================================================================

# ---------- 1) native libraries ----------
FROM rust:1.82-bookworm AS native
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ make libopus-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY audio-c ./audio-c
COPY media-engine ./media-engine
COPY secure-media-rs ./secure-media-rs
COPY scripts/build_native.sh ./scripts/build_native.sh
RUN chmod +x scripts/build_native.sh && ./scripts/build_native.sh \
    && for f in libgoppo_audio.a libgoppo_media.a libgoppo_secure_media.a; do test -s native/lib/$f || exit 1; done

# ---------- 2) Go server with CGO ----------
FROM golang:1.22-bookworm AS gobuild
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ libopus-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
ENV CGO_ENABLED=1 GOFLAGS=-mod=mod
WORKDIR /src
COPY calling-server/go.mod calling-server/go.sum ./calling-server/
RUN cd calling-server && go mod download
COPY audio-c/include ./audio-c/include
COPY media-engine/include ./media-engine/include
COPY --from=native /src/native/lib ./native/lib
COPY calling-server ./calling-server
RUN cd calling-server \
    && test "$(go env CGO_ENABLED)" = "1" \
    && go vet ./... \
    && go test -count=1 ./... \
    && go build -trimpath -ldflags="-s -w" -o /out/goppo-calling ./cmd/server \
    && /out/goppo-calling -selftest

# ---------- 3) runtime ----------
FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libopus0 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home goppo
COPY --from=gobuild --chown=root:root --chmod=0755 /out/goppo-calling /usr/local/bin/goppo-calling
# Fail the image build if the binary cannot load its shared libraries or the native components do not verify.
RUN ldd /usr/local/bin/goppo-calling | grep -q "not found" && exit 1 || true
RUN /usr/local/bin/goppo-calling -selftest
USER goppo
ENV PORT=8080 REQUIRE_NATIVE=true
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s CMD curl -fsS "http://127.0.0.1:${PORT}/ready" || exit 1
ENTRYPOINT ["/usr/local/bin/goppo-calling"]
