// Static checks of the Android project's security-relevant configuration. Run: npm run verify:config
import assert from "node:assert/strict";
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(root, "..");
const read = (p) => readFileSync(resolve(root, p), "utf8");
const PROD = "https://arnabchatweb.pythonanywhere.com";
let n = 0; let failed = 0;
const check = (name, fn) => { try { fn(); n++; console.log("  ok  " + name); } catch (e) { failed++; console.error("  FAIL " + name + "\n       " + e.message); } };

const cfg = JSON.parse(read("capacitor.config.json"));
const pkg = JSON.parse(read("package.json"));
const manifest = read("android/app/src/main/AndroidManifest.xml");
const gradle = read("android/app/build.gradle");
const rootGradle = read("android/build.gradle");
const vars = read("android/variables.gradle");
const synced = existsSync(resolve(root, "android/app/src/main/assets/capacitor.config.json"))
  ? JSON.parse(read("android/app/src/main/assets/capacitor.config.json")) : null;

console.log("verify-config");
check("server.url is the production HTTPS site (no dev URL)", () => assert.equal(cfg.server.url, PROD));
check("cleartext is off, no mixed content, no WebView debugging", () => {
  assert.equal(cfg.server.cleartext, false); assert.equal(cfg.android.allowMixedContent, false); assert.equal(cfg.android.webContentsDebuggingEnabled, false);
});
check("navigation is allowed to the production host only", () => assert.deepEqual(cfg.server.allowNavigation, ["arnabchatweb.pythonanywhere.com"]));
check("no start path is hardcoded (the site's own routing/auth decides)", () => assert.ok(!cfg.server.startPath && !JSON.stringify(cfg).includes("/messages")));
check("application id + name", () => { assert.equal(cfg.appId, "com.goppo.chat"); assert.equal(cfg.appName, "Goppo Chat"); assert.match(gradle, /applicationId "com\.goppo\.chat"/); });
check("Capacitor and plugins are pinned to exact versions", () => {
  for (const [k, v] of Object.entries({ ...pkg.dependencies, ...pkg.devDependencies })) assert.match(v, /^\d+\.\d+\.\d+$/, `${k}@${v}`);
  const caps = new Set(["@capacitor/core", "@capacitor/android", "@capacitor/cli"].map((k) => pkg.dependencies[k] || pkg.devDependencies[k]));
  assert.equal(caps.size, 1, "core/android/cli must be the same version");
  assert.ok(existsSync(resolve(root, "package-lock.json")), "package-lock.json must be committed (npm ci)");
});
check("Kotlin/AGP/Gradle versions pinned", () => {
  assert.match(rootGradle, /kotlin_version = '1\.9\.25'/); assert.match(rootGradle, /gradle:8\.7\.2/);
  assert.match(read("android/gradle/wrapper/gradle-wrapper.properties"), /gradle-8\.11\.1-all\.zip/);
});
const perms = [...manifest.matchAll(/<uses-permission android:name="([^"]+)"/g)].map((m) => m[1].replace("android.permission.", "")).sort();
check("only the documented Phase 2 + Phase 3 permissions are declared", () => assert.deepEqual(perms, [
  "CAMERA", "FOREGROUND_SERVICE", "FOREGROUND_SERVICE_CAMERA", "FOREGROUND_SERVICE_MICROPHONE", "FOREGROUND_SERVICE_PHONE_CALL", "INTERNET",
  "MANAGE_OWN_CALLS", "MODIFY_AUDIO_SETTINGS", "POST_NOTIFICATIONS", "RECORD_AUDIO", "USE_FULL_SCREEN_INTENT"]));
check("no SMS / contacts / call-log / phone-state / location / storage / dialer permissions", () => {
  for (const bad of ["READ_SMS", "READ_CONTACTS", "CALL_PHONE", "READ_CALL_LOG", "READ_PHONE_STATE", "ANSWER_PHONE_CALLS", "ACCESS_FINE_LOCATION", "ACCESS_BACKGROUND_LOCATION",
    "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE", "SYSTEM_ALERT_WINDOW", "BLUETOOTH_CONNECT", "RECEIVE_BOOT_COMPLETED"]) assert.ok(!perms.includes(bad), bad);
  assert.ok(!/default.?dialer|ROLE_DIALER|InCallService/i.test(manifest));
});
check("Phase 3 components: services/receivers not exported; only Telecom (system-bound) and the launcher activity are", () => {
  const exported = [...manifest.matchAll(/<(activity|service|receiver)\s[^>]*?android:name="([^"]+)"[^>]*?android:exported="true"/gs)].map((m) => m[2]).sort();
  assert.deepEqual(exported, [".MainActivity", ".calls.GoppoConnectionService"]);
  assert.match(manifest, /BIND_TELECOM_CONNECTION_SERVICE/);
  assert.match(manifest, /android:foregroundServiceType="phoneCall\|microphone\|camera"/);
  assert.match(manifest, /com\.google\.firebase\.MESSAGING_EVENT/);
});
check("no bundled ringtone or sound file; channels use the system defaults and are never recreated", () => {
  const files = []; const walk = (d) => { for (const f of readdirSync(d)) { const p = join(d, f); statSync(p).isDirectory() ? walk(p) : files.push(p); } };
  walk(resolve(root, "android/app/src/main"));
  assert.deepEqual(files.filter((f) => /\.(mp3|ogg|wav|m4a|aac|flac|opus)$/i.test(f) || /\/res\/raw\//.test(f)), []);
  const ch = readFileSync(resolve(root, "android/app/src/main/kotlin/com/goppo/chat/notify/NotificationChannels.kt"), "utf8");
  assert.match(ch, /RingtoneManager\.getDefaultUri\(RingtoneManager\.TYPE_RINGTONE\)/); assert.match(ch, /DEFAULT_NOTIFICATION_URI/);
  assert.match(ch, /getNotificationChannel\(id\) == null/); assert.ok(!/deleteNotificationChannel/.test(ch));
});
check("Firebase: google-services.json is applied only if present and is git-ignored; no service-account key anywhere", () => {
  assert.match(gradle, /file\('google-services\.json'\)\.exists\(\)/); assert.match(gradle, /firebase-bom/);
  assert.match(read("android/app/.gitignore"), /google-services\.json/);
  const hits = []; const walk = (d) => { for (const f of readdirSync(d)) { if (["node_modules", "build", ".gradle", ".git", "__pycache__"].includes(f)) continue; const p = join(d, f); const st = statSync(p);
    if (st.isDirectory()) walk(p); else if (st.size < 400000 && /\.(kt|java|js|json|xml|py|md|yml|yaml|txt|gradle|properties|example)$/.test(f)) { const t = readFileSync(p, "utf8"); if (/-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+\/]{20}/.test(t)) hits.push(p); } } };
  walk(repo); assert.deepEqual(hits, []);
});
check("manifest: HTTPS only, no backup, portrait, adjustResize, launcher activity + Telecom service are the only exports", () => {
  assert.match(manifest, /usesCleartextTraffic="false"/); assert.match(manifest, /allowBackup="false"/);
  assert.match(manifest, /screenOrientation="portrait"/); assert.match(manifest, /windowSoftInputMode="adjustResize"/);
  assert.equal((manifest.match(/android:exported="true"/g) || []).length, 2);
  assert.ok(!/android:debuggable/.test(manifest));
});
check("network security config: no cleartext, system CAs only, no SSL bypass", () => {
  const n = read("android/app/src/main/res/xml/network_security_config.xml");
  assert.match(n, /cleartextTrafficPermitted="false"/); assert.ok(!/src="user"/.test(n)); assert.ok(!/<domain-config/.test(n));
});
const kt = (() => { const dir = resolve(root, "android/app/src/main/kotlin/com/goppo/chat"); return readdirSync(dir).filter((f) => f.endsWith(".kt")).map((f) => readFileSync(join(dir, f), "utf8")).join("\n"); })();
check("no SSL error bypass, no addJavascriptInterface, no blanket permission grant", () => {
  assert.ok(!/override\s+fun\s+onReceivedSslError|SslErrorHandler|\.proceed\(\)/.test(kt)); assert.ok(!/addJavascriptInterface/.test(kt));
  assert.ok(!/request\.grant\(request\.resources\)/.test(kt));
});
check("capabilities: only 3 unconditional; push/incoming-call/notification ones depend on real state", () => {
  const m = kt.match(/val ALWAYS: List<String> = listOf\(([^)]*)\)/);
  assert.ok(m); assert.equal(m[1].split(",").length, 3);
  assert.match(kt, /if \(i\.firebaseOperational\) add\(NATIVE_PUSH\)/);
});
check("release signing comes from environment variables only; no keystore or secret in the tree", () => {
  assert.match(gradle, /System\.getenv\('GOPPO_KEYSTORE_FILE'\)/);
  const bad = [];
  const walk = (d) => { for (const f of readdirSync(d)) { if (["node_modules", "build", ".gradle", ".git"].includes(f)) continue; const p = join(d, f); statSync(p).isDirectory() ? walk(p) : /\.(jks|keystore|p12)$|google-services\.json$/.test(f) && bad.push(p); } };
  walk(repo); assert.deepEqual(bad, []);
});
check("the bridge asset exists and is current (npm run prepare:bridge)", () => {
  const js = read("android/app/src/main/assets/goppo-bridge.js");
  assert.ok(js.includes('registerPlugin("GoppoNative")') && js.includes("@capacitor/core " + pkg.dependencies["@capacitor/core"]));
});
check("synced Android config matches (run `npx cap sync android` if this fails)", () => {
  if (!synced) return console.log("       (not synced yet: skipped)");
  assert.equal(synced.server.url, PROD); assert.equal(synced.server.cleartext, false);
});
check("workflow exists and never prints secrets", () => {
  const wf = readFileSync(resolve(repo, ".github/workflows/android-build.yml"), "utf8");
  assert.match(wf, /workflow_dispatch/); assert.match(wf, /assembleDebug/); assert.match(wf, /upload-artifact/);
  assert.ok(!/echo\s+.*secrets\./.test(wf));
  assert.match(wf, /GOOGLE_SERVICES_JSON_BASE64/); assert.ok(!/setup-java@v4/.test(wf));
});
console.log(failed ? `verify-config: ${failed} FAILED` : `verify-config: all ${n} checks passed`);
process.exit(failed ? 1 : 0);
