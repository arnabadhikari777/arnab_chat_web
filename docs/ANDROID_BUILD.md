# Goppo Chat Android app: build with GitHub Actions

The app (`android-app/`) is a thin Capacitor 7 + Kotlin shell around the **live website**
`https://arnabchatweb.pythonanywhere.com` (`server.url`). No copy of the website is bundled; the Flask app, login,
sessions, APIs and the Render calling server are unchanged.

## Get a Debug APK (no secrets needed)
1. Push this project to GitHub (the folder that contains `android-app/` and `.github/` must be the repository root).
2. GitHub → **Actions** → **Android build** → **Run workflow** (or push to `main`/`master`).
3. When the run is green, open it → **Artifacts** → download **goppo-chat-debug-apk** → unzip → `app-debug.apk`.
4. Copy it to the phone and install (allow "install unknown apps" for the file manager/browser).
   Or with USB debugging: `adb install -r app-debug.apk`.

If the run fails, open the failed step; the first red line is the cause. Send me that step's log.

## Get a signed Release APK (optional)
Create a keystore once, on your own computer (keep it and its passwords safe; losing it means you cannot update the app):
```
keytool -genkeypair -v -keystore goppo-release.jks -alias goppo -keyalg RSA -keysize 2048 -validity 10000
base64 -w0 goppo-release.jks > goppo-release.jks.b64      # macOS: base64 -i goppo-release.jks
```
GitHub → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `GOPPO_KEYSTORE_BASE64` | contents of `goppo-release.jks.b64` |
| `GOPPO_KEYSTORE_PASSWORD` | keystore password |
| `GOPPO_KEY_ALIAS` | `goppo` (the alias you used) |
| `GOPPO_KEY_PASSWORD` | key password |

Then run the workflow with **build_release = true** (or push to `main`/`master`). All four secrets must exist, otherwise the
Release step is skipped and the Debug APK is still produced. Download **goppo-chat-release-apk**.
Nothing is committed (`*.jks` is git-ignored and checked by `npm test`); the keystore only exists on the runner during the
job and is deleted at the end. Nothing is published to Google Play.

## Firebase (native push, Phase 3)
Native push needs the **Android client config** `google-services.json`, injected at build time from a secret. Without it the APK
still builds and works; it just reports `nativePush = false` (no native push, calls only ring while the page is open).
1. Firebase console → your project → *Add app → Android* → package name **`com.goppo.chat`** → download `google-services.json`.
2. `base64 -w0 google-services.json` (macOS: `base64 -i google-services.json`) → GitHub secret **`GOOGLE_SERVICES_JSON_BASE64`**.
3. Run the workflow. The step "Add Firebase client config" checks the file is for `com.goppo.chat` and is not a service-account key.
The server-side **service-account key** is a different file and a different place: PythonAnywhere `.env`, never GitHub, never the APK
(see `docs/PHASE3_REPORT.md`).

## Changing things before release
* **Application id**: `android-app/android/app/build.gradle` → `applicationId` and `android-app/capacitor.config.json` → `appId`
  (the Kotlin package `com.goppo.chat` can stay).
* **Website address**: `android-app/capacitor.config.json` → `server.url` and `server.allowNavigation`, then `npm run sync`.
* **Version**: automatic (`versionCode` = run number, `versionName` = 1.0.<run>).

## Local commands (optional, need Node 22 + JDK 21 + Android SDK 35)
```
cd android-app && npm ci && npm run sync        # Capacitor sync + rebuild assets/goppo-bridge.js
npm test                                        # static config checks + JS bridge check (no Android SDK needed)
cd android && ./gradlew testDebugUnitTest assembleDebug
```
