# Goppo Chat — Stabilization Audit (partial pass)

Honest scope: this pass fixed and verified the CI/build/native-library problems and re-ran every test suite that can run in this sandbox.
The full 9-phase audit (website bug hunt, local-first APK review, calling lifecycle review, security review) is NOT finished.

## Fixed (verified)
| Priority | Problem | Root cause | Fix | Evidence |
|---|---|---|---|---|
| Critical (CI) | Static Checks fail: "test_all.sh / build_native.sh must be executable" | The ZIP/web upload stores every file as mode 644, so git records the scripts without the executable bit (`verify-config.mjs` correctly checks it) | Mode restored in the delivered ZIP; added `scripts/fix_permissions.sh` (`git update-index --chmod=+x` for scripts/*.sh and gradlew). The check itself is untouched | `npm test` in android-app: 23/23 config checks + 14/14 bridge checks pass with modes restored |
| High | Deleted `native/lib/*.a` | They are generated, not sources. `scripts/build_native.sh` creates them; the Dockerfile builds them in stage 1 from source; only `calling-server` cgo links them | Not restored. `.gitignore` was broken (CRLF, bogus `/.a`, and it listed `native.go` as ignored) and is rewritten to ignore `native/lib/*.a`, `native/obj`, `secure-media-rs/target`, `calling-server/bin`; `native/lib/.gitkeep` added | Rebuilt from source: C, C++ and Rust unit tests pass; Go vet clean; `go test -race` passes for auth, config, native, session, signaling |

Size note: `libgoppo_secure_media.a` is 54 MB when built (Rust std is bundled) vs 12-13 KB for the C/C++ libs; that is why the repo grew. Never commit it.

## Test results (actual)
- Flask: 769 passed, 49 failed. The failing list is identical to `tests/KNOWN_PREEXISTING_FAILURES.txt`. Not fixed, not new.
- JS (jsdom): 72/72 pass.
- Native C / C++ / Rust unit tests: pass. Go vet + race tests: pass (modules fetched directly; `go.sum` unchanged).
- Android static checks: 23/23 + 14/14.

## Not done / unverified
- Gradle `testDebugUnitTest` and `assembleDebug` (no Android SDK in the sandbox; run via GitHub Actions).
- Release APK signing (needs your secrets).
- Integration e2e (`integration-tests`) and `scripts/test_all.sh` end to end were not run here.
- The 49 pre-existing Flask failures were not root-caused. One sample (`test_announcement_switch`): `site_gate` exempts admins from every switch, yet the test expects the announcements switch to block an admin's POST. This is a code-vs-test contradiction that needs your decision.
- Not reviewed yet: local SQLite/outbox/Delete-for-Everyone logic, native call lifecycle (stale timer, remote reject), notification settings, security review, DB migrations.
- Two-phone calling tests: unverified.
