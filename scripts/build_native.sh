#!/usr/bin/env bash
# Builds the three native static libraries into native/lib/ and runs their unit tests.
# Mandatory: any failure aborts (set -e). Requires gcc, g++, cargo and libopus-dev (Debian/Ubuntu) or opus-dev (Alpine).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/native/lib"; TMP="$ROOT/native/obj"
mkdir -p "$OUT" "$TMP"

echo "== C: audio-c =="
gcc -std=c11 -O2 -Wall -Wextra -Werror -fPIC -Iaudio-c/include -c audio-c/src/goppo_audio.c -o "$TMP/goppo_audio.o"
ar rcs "$OUT/libgoppo_audio.a" "$TMP/goppo_audio.o"
gcc -std=c11 -O2 -Wall -Wextra -Werror -Iaudio-c/include audio-c/src/goppo_audio.c audio-c/tests/test_audio.c -o "$TMP/test_audio" -lopus -lm
"$TMP/test_audio"

echo "== C++: media-engine =="
g++ -std=c++17 -O2 -Wall -Wextra -Werror -fPIC -Iaudio-c/include -Imedia-engine/include -c media-engine/src/media_engine.cpp -o "$TMP/media_engine.o"
ar rcs "$OUT/libgoppo_media.a" "$TMP/media_engine.o"
g++ -std=c++17 -O2 -Wall -Wextra -Werror -Imedia-engine/include -c media-engine/tests/test_media.cpp -o "$TMP/test_media.o"
g++ "$TMP/test_media.o" "$TMP/media_engine.o" "$TMP/goppo_audio.o" -o "$TMP/test_media" -lopus -lm
"$TMP/test_media"

echo "== Rust: secure-media-rs =="
( cd secure-media-rs && cargo test --release --locked 2>&1 | tail -4 && cargo build --release --locked )
cp secure-media-rs/target/release/libgoppo_secure_media.a "$OUT/"

for f in libgoppo_audio.a libgoppo_media.a libgoppo_secure_media.a; do
  test -s "$OUT/$f" || { echo "MISSING native library $f" >&2; exit 1; }
done
echo "native libraries ready in $OUT"
