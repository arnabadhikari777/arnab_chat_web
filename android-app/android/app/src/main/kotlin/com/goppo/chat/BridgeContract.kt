package com.goppo.chat

/** Which capabilities the GoppoNative plugin reports. A capability appears only when the feature can really work right now. */
object Capabilities {
    const val NATIVE_PERMISSIONS = "nativePermissions"
    const val APP_LIFECYCLE = "appLifecycle"
    const val OPEN_EXTERNAL = "openExternal"
    const val NATIVE_PUSH = "nativePush"
    const val NATIVE_INCOMING_CALL = "nativeIncomingCall"
    const val NATIVE_CALL_NOTIFICATIONS = "nativeCallNotifications"
    const val NATIVE_MESSAGE_NOTIFICATIONS = "nativeMessageNotifications"
    const val AUDIO_ROUTING = "audioRouting"

    /** Always there (Phase 2 features). */
    val ALWAYS: List<String> = listOf(NATIVE_PERMISSIONS, APP_LIFECYCLE, OPEN_EXTERNAL)

    /** What is true on this phone right now. */
    data class Inputs(
        val firebaseOperational: Boolean,      // google-services.json in the build AND Google Play services on the phone
        val callsChannelUsable: Boolean,       // notifications on for the app and the Incoming Calls channel not switched off
        val activeCallChannelUsable: Boolean,
        val messagesChannelUsable: Boolean,
    )

    fun compute(i: Inputs): List<String> = buildList {
        addAll(ALWAYS)
        if (i.firebaseOperational) add(NATIVE_PUSH)
        // The website stops ringing itself when it sees this, so it must only appear when Android can really ring.
        if (i.callsChannelUsable) add(NATIVE_INCOMING_CALL)
        if (i.activeCallChannelUsable) add(NATIVE_CALL_NOTIFICATIONS)
        if (i.firebaseOperational && i.messagesChannelUsable) add(NATIVE_MESSAGE_NOTIFICATIONS)
        add(AUDIO_ROUTING)
    }
}

/** Web -> native events accepted by `notify({event, detail})`. */
object WebEvents {
    const val READY = "app:ready"
    const val CALL_INCOMING = "call:incoming"
    const val CALL_CONNECTED = "call:connected"
    const val CALL_ENDED = "call:ended"
    const val CALL_MUTED = "call:muted-changed"

    val ALLOWED: Set<String> = setOf(READY, CALL_INCOMING, CALL_CONNECTED, CALL_ENDED, CALL_MUTED)
    const val MAX_DETAIL_CHARS = 2000

    /** The event name if it is an allowed string, else null. */
    fun validate(event: Any?): String? = (event as? String)?.takeIf { it in ALLOWED }
}

/** Native -> web commands (delivered through the plugin's "command" listener). */
object Commands {
    const val BACK = "app:back"
    const val RESUME = "app:resume"
    const val PAUSE = "app:pause"
}

/** Strict argument checks for plugin methods. */
object Args {
    /** `null` when the key is absent, else the boolean; throws for any other type. */
    fun optionalBoolean(value: Any?, name: String): Boolean? = when (value) {
        null -> null
        is Boolean -> value
        else -> throw IllegalArgumentException("$name must be a boolean")
    }
}

/** What the app knows about the web page's call. Reset whenever a new page starts loading. */
class NativeState {
    @Volatile var webReady: Boolean = false
        private set
    @Volatile var callActive: Boolean = false
        private set

    fun onPageStarted() { webReady = false; callActive = false }

    /** @return true if the event was a known one and was applied. */
    fun onWebEvent(event: String): Boolean {
        when (event) {
            WebEvents.READY -> webReady = true
            WebEvents.CALL_CONNECTED -> callActive = true
            WebEvents.CALL_ENDED -> callActive = false
            WebEvents.CALL_INCOMING, WebEvents.CALL_MUTED -> Unit
            else -> return false
        }
        return true
    }
}

/**
 * Decides when app:resume / app:pause reach the website. Each real transition is delivered once, nothing is delivered
 * before the page said app:ready, and a page that loads while the app is in the background learns the state once.
 */
class LifecycleGate {
    private enum class Phase { RESUMED, PAUSED }

    private var current = Phase.RESUMED
    private var delivered = Phase.RESUMED
    private var ready = false

    @Synchronized fun onPageStarted() { ready = false; delivered = Phase.RESUMED }
    @Synchronized fun onWebReady(): String? { ready = true; return sync() }
    @Synchronized fun onActivityResumed(): String? { current = Phase.RESUMED; return sync() }
    @Synchronized fun onActivityPaused(): String? { current = Phase.PAUSED; return sync() }

    private fun sync(): String? {
        if (!ready || current == delivered) return null
        delivered = current
        return if (current == Phase.RESUMED) Commands.RESUME else Commands.PAUSE
    }
}

enum class BackAction {
    /** The website handled it (closed a dialog, went back, protected a call). */
    NONE,

    /** Website gave no answer: use WebView history. */
    WEBVIEW_BACK,

    /** Nothing left to go back to. */
    EXIT,

    /** Nothing left, but a call is active: keep it alive and send the app to the background instead of closing it. */
    BACKGROUND,
}

object BackPolicy {
    /**
     * @param webConsumed true = website consumed the press; false = website has nothing left to go back to;
     *                    null = no usable answer (no platform script, timeout, error).
     */
    fun decide(webConsumed: Boolean?, webViewCanGoBack: Boolean, callActive: Boolean): BackAction = when {
        webConsumed == true -> BackAction.NONE
        webConsumed == false -> if (callActive) BackAction.BACKGROUND else BackAction.EXIT
        webViewCanGoBack -> BackAction.WEBVIEW_BACK
        callActive -> BackAction.BACKGROUND
        else -> BackAction.EXIT
    }
}

/** Which Android permissions / WebView resources the Phase 2 media bridge handles. */
object MediaPolicy {
    const val RES_AUDIO = "android.webkit.resource.AUDIO_CAPTURE"
    const val RES_VIDEO = "android.webkit.resource.VIDEO_CAPTURE"
    const val PERM_AUDIO = "android.permission.RECORD_AUDIO"
    const val PERM_VIDEO = "android.permission.CAMERA"

    /** OS permissions needed for the supported resources in a WebView request. Unknown resources add nothing. */
    fun requiredPermissions(resources: Array<String>): List<String> = buildList {
        if (RES_AUDIO in resources) add(PERM_AUDIO)
        if (RES_VIDEO in resources) add(PERM_VIDEO)
    }

    /** Only supported resources whose OS permission is granted; everything else (MIDI, protected media...) is refused. */
    fun grantableResources(resources: Array<String>, grantedPermissions: Set<String>): Array<String> = buildList {
        if (RES_AUDIO in resources && PERM_AUDIO in grantedPermissions) add(RES_AUDIO)
        if (RES_VIDEO in resources && PERM_VIDEO in grantedPermissions) add(RES_VIDEO)
    }.toTypedArray()

    fun permissionsFor(audio: Boolean, video: Boolean): List<String> = buildList {
        if (audio) add(PERM_AUDIO)
        if (video) add(PERM_VIDEO)
    }
}
