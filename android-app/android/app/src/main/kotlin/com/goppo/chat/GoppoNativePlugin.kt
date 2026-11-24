package com.goppo.chat

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import com.getcapacitor.JSArray
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import org.json.JSONObject

/**
 * Capacitor plugin "GoppoNative": the Android side of the contract in chat-web/app/static/js/platform.js.
 *
 *   getCapabilities()                      -> {capabilities[], appVersion, appBuild, deviceId, platform, ...}
 *   getPushToken()                         -> {configured:false, token:null, reason:"not_configured"}   (Phase 3)
 *   notify({event, detail})                -> web -> native events (allow-listed)
 *   openExternal({url})                    -> system browser, http/https only
 *   requestMediaPermissions({audio,video}) -> Android runtime permission prompts
 *   openAppSettings()                      -> Settings screen of this app (recovery after "don't ask again")
 *   addListener("command", fn)             -> native -> web commands: {command, detail}
 *   backResult({seq, answered, consumed})  -> internal: the page's answer to an app:back command
 *
 * Every argument is validated; failures reject with a short code and never expose stack traces.
 */
@CapacitorPlugin(name = "GoppoNative")
class GoppoNativePlugin : Plugin() {

    private fun mainActivity(): MainActivity? = activity as? MainActivity

    private fun arg(call: PluginCall, key: String): Any? = call.data.opt(key).takeUnless { it === JSONObject.NULL }

    // ------------------------------------------------------------------ capabilities

    @PluginMethod
    fun getCapabilities(call: PluginCall) {
        try {
            val info = context.packageManager.getPackageInfo(context.packageName, 0)
            val build = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
            val caps = JSArray()
            Capabilities.IMPLEMENTED.forEach { caps.put(it) }
            call.resolve(
                JSObject()
                    .put("capabilities", caps)
                    .put("appVersion", info.versionName ?: "")
                    .put("appBuild", build)
                    .put("deviceId", DeviceId.get(context))
                    .put("platform", "android")
                    .put("sdkInt", Build.VERSION.SDK_INT)
                    .put("nativeApiVersion", 1),
            )
        } catch (e: Exception) {
            call.reject("Could not read app information", "INTERNAL_ERROR")
        }
    }

    @PluginMethod
    fun getPushToken(call: PluginCall) {
        // Phase 2: no Firebase, no token. An explicit, non-error answer; the website treats a missing token as "no native push".
        call.resolve(JSObject().put("configured", false).put("token", JSONObject.NULL).put("reason", "not_configured"))
    }

    // ------------------------------------------------------------------ web -> native events

    @PluginMethod
    fun notify(call: PluginCall) {
        val event = WebEvents.validate(arg(call, "event"))
        if (event == null) { call.reject("Unknown event", "BAD_EVENT"); return }
        val detail = arg(call, "detail")
        if (detail != null) {
            if (detail !is JSONObject) { call.reject("detail must be an object", "BAD_DETAIL"); return }
            if (detail.toString().length > WebEvents.MAX_DETAIL_CHARS) { call.reject("detail is too large", "BAD_DETAIL"); return }
        }
        GoppoRuntime.state.onWebEvent(event)      // only the event name is used; detail (ids/status) is never logged or stored
        if (event == WebEvents.READY) mainActivity()?.onWebReady()
        call.resolve(JSObject().put("ok", true))
    }

    // ------------------------------------------------------------------ links

    @PluginMethod
    fun openExternal(call: PluginCall) {
        val raw = arg(call, "url") as? String
        val safe = GoppoRuntime.policy?.externalUrlOrNull(raw)
        if (safe == null) { call.reject("This link cannot be opened", "URL_NOT_ALLOWED"); return }
        val act = mainActivity()
        if (act == null) { call.reject("App is not ready", "NOT_READY"); return }
        act.runOnUiThread {
            if (ExternalLinks.open(act, safe)) call.resolve(JSObject().put("opened", true))
            else call.reject("No app can open this link", "NO_HANDLER")
        }
    }

    // ------------------------------------------------------------------ permissions

    @PluginMethod
    fun requestMediaPermissions(call: PluginCall) {
        val audio: Boolean
        val video: Boolean
        try {
            audio = Args.optionalBoolean(arg(call, "audio"), "audio") ?: false
            video = Args.optionalBoolean(arg(call, "video"), "video") ?: false
        } catch (e: IllegalArgumentException) {
            call.reject("audio and video must be booleans", "BAD_ARGUMENT")
            return
        }
        val wanted = MediaPolicy.permissionsFor(audio, video)
        if (wanted.isEmpty()) { call.reject("Ask for audio, video or both", "BAD_ARGUMENT"); return }
        val act = mainActivity()
        if (act == null) { call.reject("App is not ready", "NOT_READY"); return }
        act.runOnUiThread {
            act.mediaPermissions.request(wanted) { outcome ->
                fun state(perm: String, asked: Boolean) = when {
                    !asked -> "not_requested"
                    perm in outcome.granted -> "granted"
                    else -> "denied"
                }
                call.resolve(
                    JSObject()
                        .put("audio", state(MediaPolicy.PERM_AUDIO, audio))
                        .put("video", state(MediaPolicy.PERM_VIDEO, video))
                        .put("permanentlyDenied", wanted.any { it in outcome.permanentlyDenied }),
                )
            }
        }
    }

    @PluginMethod
    fun openAppSettings(call: PluginCall) {
        val act = mainActivity()
        if (act == null) { call.reject("App is not ready", "NOT_READY"); return }
        act.runOnUiThread {
            try {
                act.startActivity(
                    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", act.packageName, null)),
                )
                call.resolve(JSObject().put("opened", true))
            } catch (e: Exception) {
                call.reject("Settings could not be opened", "NO_HANDLER")
            }
        }
    }

    // ------------------------------------------------------------------ back button (internal)

    @PluginMethod
    fun backResult(call: PluginCall) {
        val seq = call.getInt("seq", -1) ?: -1
        val answered = call.getBoolean("answered", false) ?: false
        val consumed = call.getBoolean("consumed", false) ?: false
        mainActivity()?.onBackAnswer(seq, if (answered) consumed else null)
        call.resolve()
    }

    // ------------------------------------------------------------------ native -> web

    /** Delivers a command to the page's `addListener("command")` handler. Dropped when the page has no listener. */
    fun sendCommand(command: String, detail: JSObject = JSObject()) {
        if (!hasListeners("command")) return
        notifyListeners("command", JSObject().put("command", command).put("detail", detail))
    }
}
