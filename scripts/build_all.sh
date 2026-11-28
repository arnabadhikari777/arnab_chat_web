#!/usr/bin/env bash
# Builds the native libraries, then the Go calling server linked with CGO. Output: calling-server/bin/goppo-calling
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
bash "$ROOT/scripts/build_native.sh"
cd "$ROOT/calling-server"
export CGO_ENABLED=1
mkdir -p bin
go vet ./...
go build -trimpath -ldflags="-s -w" -o bin/goppo-calling ./cmd/server
./bin/goppo-calling -selftest
echo "built calling-server/bin/goppo-calling"
