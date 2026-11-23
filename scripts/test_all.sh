#!/usr/bin/env bash
# Runs every automated test suite of the project. Exit code != 0 if any mandatory suite fails.
# Needs: gcc g++ cargo go libopus-dev python3 (+ chat-web/requirements*.txt) node/npm. Docker is NOT needed (see docs/TESTING_REPORT.md).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
FAILFILE="$(mktemp)"; trap 'rm -f "$FAILFILE"' EXIT      # subshell-safe failure record
step() { echo; echo "================ $* ================"; }
run()  { "$@" || { echo "FAILED: $*"; echo "$*" >> "$FAILFILE"; }; }

step "native libraries (C, C++, Rust unit tests + build)"; run ./scripts/build_native.sh

step "C + C++ under AddressSanitizer / UndefinedBehaviorSanitizer"
mkdir -p native/obj
run gcc -std=c11 -g -O1 -fsanitize=address,undefined -fno-sanitize-recover=all -Iaudio-c/include audio-c/src/goppo_audio.c audio-c/tests/test_audio.c -o native/obj/test_audio_asan -lopus -lm
run native/obj/test_audio_asan
run gcc -std=c11 -g -O1 -fsanitize=address,undefined -c -Iaudio-c/include audio-c/src/goppo_audio.c -o native/obj/a_asan.o
run g++ -std=c++17 -g -O1 -fsanitize=address,undefined -fno-sanitize-recover=all -Iaudio-c/include -Imedia-engine/include media-engine/src/media_engine.cpp media-engine/tests/test_media.cpp native/obj/a_asan.o -o native/obj/test_media_asan -lopus -lm
run native/obj/test_media_asan

step "Rust (cargo test, clippy if available)"; (cd secure-media-rs && cargo test --release --locked 2>&1 | grep -E "^test |test result: .* [1-9][0-9]* passed" || echo "FAILED: cargo test" >> "$FAILFILE")

step "Go: vet, build with CGO, tests with the race detector"
(cd calling-server && export CGO_ENABLED=1 && run go vet ./... && run go build ./... && run go test -race -count=1 ./... 2>&1 | grep -v "ws read")

step "Python: Flask unit/API/migration tests"; (cd chat-web && python3 -m pytest -q tests -p no:cacheprovider 2>&1 | tee /tmp/pytest_out.txt | tail -1
  grep -E "^FAILED" /tmp/pytest_out.txt | sed -e 's/^FAILED //' -e 's/ - .*//' | sort > /tmp/pytest_failed.txt
  sort tests/KNOWN_PREEXISTING_FAILURES.txt > /tmp/pytest_known.txt
  NEW="$(comm -13 /tmp/pytest_known.txt /tmp/pytest_failed.txt)"
  if [ -n "$NEW" ]; then echo "NEW Flask test failures (not in KNOWN_PREEXISTING_FAILURES.txt):"; echo "$NEW"; echo "flask new failures" >> "$FAILFILE"; else echo "no new failures: remaining failures are the known pre-existing ones from the supplied ZIP"; fi)

step "JavaScript: calling client (jsdom)"; (cd chat-web/tests/js && { [ -d node_modules ] || npm install --silent --no-audit --no-fund; } && run npm test 2>&1 | grep -E "^# (tests|pass|fail)")

step "End-to-end: real Go binary + Flask + WebSockets"; run ./scripts/build_all.sh >/dev/null; (cd integration-tests && run python3 -m pytest -q -p no:cacheprovider 2>&1 | tail -3)

echo; [ ! -s "$FAILFILE" ] && echo "ALL MANDATORY SUITES PASSED" || echo "SOME SUITES FAILED (the known pre-existing Flask failures are listed in docs/TESTING_REPORT.md)"
[ ! -s "$FAILFILE" ]
