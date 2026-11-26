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
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.goppo.chat.audio.AudioRouter
import com.goppo.chat.calls.CommandBus
import com.goppo.chat.calls.NativeCallManager
import com.goppo.chat.notify.CallNotifications
import com.goppo.chat.notify.NotificationChannels
import com.goppo.chat.push.FirebaseSupport
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
            currentCapabilities().forEach { caps.put(it) }
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

    private fun currentCapabilities(): List<String> = Capabilities.compute(
        Capabilities.Inputs(
            firebaseOperational = FirebaseSupport.operational(context),
            callsChannelUsable = NotificationChannels.usable(context, NotificationChannels.CALLS),
            activeCallChannelUsable = NotificationChannels.usable(context, NotificationChannels.ACTIVE_CALL),
            messagesChannelUsable = NotificationChannels.usable(context, NotificationChannels.MESSAGES),
        ),
    )

    /**
     * The FCM registration token, only to the website's own pages (the bridge exists for that origin only) and only when
     * the page asks. The website then registers it with POST/PATCH /api/devices (session + CSRF); the server never returns it.
     */
    @PluginMethod
    fun getPushToken(call: PluginCall) {
        if (!FirebaseSupport.operational(context)) {
            call.resolve(JSObject().put("configured", false).put("token", JSONObject.NULL).put("reason", FirebaseSupport.reason(context)))
            return
        }
        FirebaseSupport.fetchToken(context) { token ->
            if (token == null) call.resolve(JSObject().put("configured", true).put("token", JSONObject.NULL).put("reason", "token_unavailable"))
            else call.resolve(JSObject().put("configured", true).put("token", token))
        }
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
        GoppoRuntime.state.onWebEvent(event)
        if (event == WebEvents.READY) mainActivity()?.onWebReady()
        if (event.startsWith("call:")) NativeCallManager.onWebEvent(context, event, (detail as? JSONObject) ?: JSONObject())
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

    // ------------------------------------------------------------------ notifications (Phase 3)

    private fun notificationStatus(): JSObject {
        val nm = NotificationManagerCompat.from(context)
        val perm = when {
            Build.VERSION.SDK_INT < 33 -> "not_required"
            ContextCompat.checkSelfPermission(context, android.Manifest.permission.POST_NOTIFICATIONS) == android.content.pm.PackageManager.PERMISSION_GRANTED -> "granted"
            else -> "denied"
        }
        fun channel(id: String) = JSObject().put("importance", NotificationChannels.importance(context, id)).put("usable", NotificationChannels.usable(context, id))
        return JSObject()
            .put("enabled", nm.areNotificationsEnabled())
            .put("permission", perm)
            .put("fullScreenIntent", when {
                Build.VERSION.SDK_INT < 34 -> "not_required"
                CallNotifications.canUseFullScreenIntent(context) -> "granted"
                else -> "denied"
            })
            .put("channels", JSObject().put("calls", channel(NotificationChannels.CALLS)).put("messages", channel(NotificationChannels.MESSAGES))
                .put("missed_calls", channel(NotificationChannels.MISSED_CALLS)))
            .put("telecom", com.goppo.chat.calls.TelecomBridge.supported(context))
    }

    @PluginMethod
    fun getNotificationStatus(call: PluginCall) {
        NotificationChannels.ensure(context)
        call.resolve(notificationStatus())
    }

    /** Asks for the Android 13+ notification permission. The website calls this at a sensible moment, never at app start. */
    @PluginMethod
    fun requestNotificationPermission(call: PluginCall) {
        val act = mainActivity()
        if (act == null) { call.reject("App is not ready", "NOT_READY"); return }
        NotificationChannels.ensure(context)
        if (Build.VERSION.SDK_INT < 33) { call.resolve(notificationStatus()); return }
        act.runOnUiThread {
            act.mediaPermissions.request(listOf(android.Manifest.permission.POST_NOTIFICATIONS)) { outcome ->
                call.resolve(notificationStatus().put("permanentlyDenied", outcome.permanentlyDenied.isNotEmpty()))
            }
        }
    }

    /** Opens the Android settings screen for this app's notifications: all, one channel ("calls"|"messages"|"missed_calls"), or the full-screen-intent switch. */
    @PluginMethod
    fun openNotificationSettings(call: PluginCall) {
        val act = mainActivity()
        if (act == null) { call.reject("App is not ready", "NOT_READY"); return }
        val channelArg = arg(call, "channel")
        val channelId = if (channelArg == null) null else NotificationChannels.idForName(channelArg as? String)
        if (channelArg != null && channelId == null) { call.reject("Unknown channel", "BAD_ARGUMENT"); return }
        val target = arg(call, "target") as? String
        if (target != null && target != "fullScreenIntent") { call.reject("Unknown target", "BAD_ARGUMENT"); return }
        val pkg = act.packageName
        val intent = when {
            target == "fullScreenIntent" && Build.VERSION.SDK_INT >= 34 ->
                Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:$pkg"))
            channelId != null && Build.VERSION.SDK_INT >= 26 ->
                Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, pkg).putExtra(Settings.EXTRA_CHANNEL_ID, channelId)
            Build.VERSION.SDK_INT >= 26 -> Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, pkg)
            else -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", pkg, null))
        }
        act.runOnUiThread {
            try { act.startActivity(intent); call.resolve(JSObject().put("opened", true)) }
            catch (e: Exception) { call.reject("Settings could not be opened", "NO_HANDLER") }
        }
    }

    // ------------------------------------------------------------------ calls + audio (Phase 3)

    private fun withUi(call: PluginCall, block: () -> Unit) {
        val act = mainActivity()
        if (act == null) { call.reject("App is not ready", "NOT_READY"); return }
        act.runOnUiThread {
            try { block() } catch (e: Exception) { call.reject("Operation failed", "INTERNAL_ERROR") }
        }
    }

    /** A call id argument, if given, must be the call the app is handling. */
    private fun callIdOk(call: PluginCall): Boolean {
        val given = arg(call, "call_id")
        if (given != null && (given as? String)?.lowercase() != NativeCallManager.session()?.info?.callId) {
            call.reject("That call is not the current call", "WRONG_CALL")
            return false
        }
        return true
    }

    @PluginMethod
    fun getActiveCallState(call: PluginCall) = withUi(call) { call.resolve(JSObject(NativeCallManager.stateJson().toString())) }

    @PluginMethod
    fun acceptIncomingCall(call: PluginCall) = withUi(call) {
        if (!callIdOk(call)) return@withUi
        if (NativeCallManager.session() == null) { call.reject("No incoming call", "NO_CALL"); return@withUi }
        NativeCallManager.accept(context)
        call.resolve(JSObject().put("ok", true))
    }

    @PluginMethod
    fun rejectIncomingCall(call: PluginCall) = withUi(call) {
        if (!callIdOk(call)) return@withUi
        if (NativeCallManager.session() == null) { call.reject("No incoming call", "NO_CALL"); return@withUi }
        NativeCallManager.reject(context)
        call.resolve(JSObject().put("ok", true))
    }

    @PluginMethod
    fun endCall(call: PluginCall) = withUi(call) {
        if (!callIdOk(call)) return@withUi
        if (NativeCallManager.session() == null) { call.reject("No call", "NO_CALL"); return@withUi }
        NativeCallManager.endCall(context)
        call.resolve(JSObject().put("ok", true))
    }

    @PluginMethod
    fun setMicrophoneMuted(call: PluginCall) {
        val muted = try { Args.optionalBoolean(arg(call, "muted"), "muted") } catch (e: IllegalArgumentException) { null }
        if (muted == null) { call.reject("muted must be a boolean", "BAD_ARGUMENT"); return }
        withUi(call) {
            if (NativeCallManager.session() == null) { call.reject("No call", "NO_CALL"); return@withUi }
            NativeCallManager.setMuted(muted)                 // the website owns the microphone track; it answers with call:muted-changed
            call.resolve(JSObject().put("ok", true))
        }
    }

    @PluginMethod
    fun getAudioDevices(call: PluginCall) = withUi(call) { call.resolve(JSObject(AudioRouter.toJson().toString())) }

    @PluginMethod
    fun selectAudioDevice(call: PluginCall) {
        val id = arg(call, "id") as? String
        if (id == null || id.isEmpty() || id.length > 64 || !id.all { it.isLetterOrDigit() || it == ':' || it == '_' || it == '-' || it == '.' }) {
            call.reject("id must be one of the ids from getAudioDevices()", "BAD_ARGUMENT"); return
        }
        withUi(call) {
            if (AudioRouter.select(id)) call.resolve(JSObject(AudioRouter.toJson().toString()))
            else call.reject("That audio device is not available", "NO_SUCH_DEVICE")
        }
    }

    @PluginMethod
    fun setSpeakerEnabled(call: PluginCall) {
        val enabled = try { Args.optionalBoolean(arg(call, "enabled"), "enabled") } catch (e: IllegalArgumentException) { null }
        if (enabled == null) { call.reject("enabled must be a boolean", "BAD_ARGUMENT"); return }
        withUi(call) {
            if (AudioRouter.setSpeaker(enabled)) call.resolve(JSObject(AudioRouter.toJson().toString()))
            else call.reject("No speaker/earpiece to switch to", "NO_SUCH_DEVICE")
        }
    }

    /** Internal: the page's answer to a command queued by CommandBus. */
    @PluginMethod
    fun commandResult(call: PluginCall) {
        val id = call.getLong("id", -1L) ?: -1L
        CommandBus.result(id, call.getBoolean("handled", false) ?: false, call.getBoolean("ok", false) ?: false)
        call.resolve()
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
