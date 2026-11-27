package com.goppo.chat.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import com.getcapacitor.Logger
import com.goppo.chat.GoppoRuntime
import com.goppo.chat.calls.TelecomBridge
import org.json.JSONArray
import org.json.JSONObject

/**
 * Call audio routing: earpiece, speaker, Bluetooth headset, wired headset.
 *
 * Two back ends behind one API:
 *  - Telecom: when the call is registered with Android Telecom, the system owns the route; we read `CallAudioState` and ask
 *    for changes through the Connection (`setAudioRoute`). Telecom also does the audio focus and mode.
 *  - AudioManager: otherwise. Android 12+ uses the communication-device API (getAvailableCommunicationDevices /
 *    setCommunicationDevice, not the deprecated speakerphone/SCO calls); Android 6-11 uses the legacy calls.
 *
 * The media itself stays in the WebView's WebRTC; this only chooses where the sound goes. UNTESTED on real hardware.
 */
object AudioRouter {
    private val main = Handler(Looper.getMainLooper())
    private var context: Context? = null
    private var started = false
    private var video = false
    private var viaTelecom = false
    private var state = AudioState(emptyList(), null)
    private var telecomMask = 0
    private var telecomRoute = 0
    private var focusRequest: AudioFocusRequest? = null
    private var focusRequested = false                       // a focus request is outstanding and must be abandoned
    private var modeChanged = false                          // we switched the AudioManager mode and must switch it back
    private var previousMode = AudioManager.MODE_NORMAL
    private var routeCheck = 0                               // invalidates stale "did the route really change?" checks

    /**
     * ONE listener instance for the whole life of the object: the exact instance given to requestAudioFocus() is the one
     * given to abandonAudioFocus() (API < 26 matches the request to abandon by listener identity; passing null leaves it active).
     */
    private val focusChangeListener = AudioManager.OnAudioFocusChangeListener { change -> onFocusChange(change) }
    private var deviceCallback: AudioDeviceCallback? = null
    private var commListener: AudioManager.OnCommunicationDeviceChangedListener? = null

    private fun am(ctx: Context) = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    fun current(): AudioState = state

    // ------------------------------------------------------------------ lifecycle
    @Synchronized
    fun start(ctx: Context, isVideo: Boolean, telecom: Boolean) {
        // A previous call that was never stopped must not leave its focus request, listeners or audio mode behind.
        if (started || focusRequested || modeChanged) teardown()
        context = ctx.applicationContext
        video = isVideo
        viaTelecom = telecom
        started = true
        if (!telecom) startAudioManagerBackend(ctx.applicationContext)
        refresh(applyDefault = true)
    }

    @Synchronized
    fun stop() = teardown()

    private fun teardown() {
        val ctx = context ?: return
        routeCheck++                                          // cancel pending route verifications
        if (started && !viaTelecom || focusRequested || modeChanged) stopAudioManagerBackend(ctx)
        started = false
        state = AudioState(emptyList(), null, false)         // a mute from this call must not leak into the next one
        telecomMask = 0
        telecomRoute = 0
    }

    // ------------------------------------------------------------------ API used by the plugin
    @Synchronized
    fun select(id: String): Boolean {
        val ctx = context ?: return false
        if (!started) return false
        // Only a device that is really there right now can be chosen (a disconnected headset is refused).
        val endpoint = state.endpoints.firstOrNull { it.id == id } ?: return false
        val ok = try {
            if (viaTelecom) AudioModel.routeFor(endpoint.type)?.let { TelecomBridge.requestRoute(it) } ?: false
            else selectViaAudioManager(ctx, endpoint)
        } catch (e: Exception) {
            Logger.warn("Goppo: audio route change failed (${e.javaClass.simpleName})")
            false
        }
        if (ok) {
            main.postDelayed({ refresh(applyDefault = false) }, 150)
            verifyRoute(id)
        }
        return ok
    }

    /**
     * Asking Android for a route is not the same as getting it (Bluetooth SCO connects asynchronously, Telecom answers later).
     * After a grace period the real route is compared with the request; a mismatch is reported to the page instead of success.
     */
    private fun verifyRoute(requestedId: String) {
        val token = ++routeCheck
        main.postDelayed({
            synchronized(this) {
                if (token != routeCheck || !started) return@postDelayed
                refresh(applyDefault = false)
                if (state.selectedId != requestedId) {
                    emit("audio:routeChanged", toJson().put("error", "route_not_applied").put("requested", requestedId))
                }
            }
        }, ROUTE_VERIFY_MS)
    }

    @Synchronized
    fun setSpeaker(enable: Boolean): Boolean {
        val target = AudioModel.speakerToggle(state.endpoints, enable) ?: return false
        return select(target.id)
    }

    @Synchronized
    fun setMuted(muted: Boolean) {
        if (state.muted == muted) return
        state = state.copy(muted = muted)
        emit("audio:muteChanged", JSONObject().put("muted", muted))
    }

    @Synchronized
    fun toJson(): JSONObject = JSONObject()
        .put("devices", JSONArray().also { arr -> state.endpoints.forEach { arr.put(endpointJson(it)) } })
        .put("selected", state.selectedId ?: JSONObject.NULL)
        .put("muted", state.muted)
        .put("backend", if (viaTelecom) "telecom" else "audio_manager")

    private fun endpointJson(e: AudioEndpoint) = JSONObject().put("id", e.id).put("type", e.type.wire).put("name", e.name)

    // ------------------------------------------------------------------ Telecom back end
    @Synchronized
    fun onTelecomAudioState(mask: Int, route: Int) {
        telecomMask = mask
        telecomRoute = route
        if (started && viaTelecom) refresh(applyDefault = false)
    }

    // ------------------------------------------------------------------ AudioManager back end
    private fun startAudioManagerBackend(ctx: Context) {
        val audio = am(ctx)
        try {
            previousMode = audio.mode
            if (previousMode != AudioManager.MODE_IN_COMMUNICATION) {   // speakerphone / SCO / communication device need call mode
                audio.mode = AudioManager.MODE_IN_COMMUNICATION
                modeChanged = true
            }
        } catch (e: Exception) {
            Logger.warn("Goppo: could not set the call audio mode (${e.javaClass.simpleName})")
        }
        requestFocus(audio)
        try {
            deviceCallback = object : AudioDeviceCallback() {
                override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>?) = refresh(applyDefault = true)
                override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>?) = refresh(applyDefault = true)
            }.also { audio.registerAudioDeviceCallback(it, main) }
            if (Build.VERSION.SDK_INT >= 31) {
                commListener = AudioManager.OnCommunicationDeviceChangedListener { refresh(applyDefault = false) }
                    .also { audio.addOnCommunicationDeviceChangedListener(ContextCompat.getMainExecutor(ctx), it) }
            }
        } catch (e: Exception) {
            Logger.warn("Goppo: audio device listeners could not be registered (${e.javaClass.simpleName})")
        }
    }

    /** Undoes everything startAudioManagerBackend did. Every step is independent: one failure never skips the rest. */
    private fun stopAudioManagerBackend(ctx: Context) {
        val audio = am(ctx)
        deviceCallback?.let { runCatching { audio.unregisterAudioDeviceCallback(it) } }
        deviceCallback = null
        if (Build.VERSION.SDK_INT >= 31) {
            commListener?.let { runCatching { audio.removeOnCommunicationDeviceChangedListener(it) } }
            commListener = null
            runCatching { audio.clearCommunicationDevice() }              // back to the system's normal routing
        } else {
            @Suppress("DEPRECATION")
            runCatching {
                audio.isSpeakerphoneOn = false
                if (audio.isBluetoothScoOn) { audio.stopBluetoothSco(); audio.isBluetoothScoOn = false }
            }
        }
        abandonFocus(audio)
        if (modeChanged) {
            runCatching { audio.mode = previousMode }
            modeChanged = false
        }
    }

    private fun requestFocus(audio: AudioManager) {
        if (focusRequested) return                                         // never stack a second request
        val result = try {
            if (Build.VERSION.SDK_INT >= 26) {
                val attrs = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
                val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT).setAudioAttributes(attrs)
                    .setOnAudioFocusChangeListener(focusChangeListener, main).build()
                focusRequest = req
                focusRequested = true
                audio.requestAudioFocus(req)
            } else {
                focusRequested = true
                @Suppress("DEPRECATION")
                audio.requestAudioFocus(focusChangeListener, AudioManager.STREAM_VOICE_CALL, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            }
        } catch (e: Exception) {
            Logger.warn("Goppo: audio focus request failed (${e.javaClass.simpleName})")
            AudioManager.AUDIOFOCUS_REQUEST_FAILED
        }
        // Not granted (another call owns audio, or the request threw): the call carries on; abandon stays safe and idempotent.
        if (result != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) Logger.warn("Goppo: audio focus not granted ($result); continuing without it")
    }

    private fun abandonFocus(audio: AudioManager) {
        if (!focusRequested) return
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                focusRequest?.let { audio.abandonAudioFocusRequest(it) }
            } else {
                @Suppress("DEPRECATION") audio.abandonAudioFocus(focusChangeListener)   // the SAME listener that was requested
            }
        } catch (e: Exception) {
            Logger.warn("Goppo: abandoning audio focus failed (${e.javaClass.simpleName})")
        } finally {
            focusRequest = null
            focusRequested = false
        }
    }

    private fun onFocusChange(change: Int) {
        // A regular phone call (or another app) took audio: tell the website so the person sees it; the call itself stays up.
        when (change) {
            AudioManager.AUDIOFOCUS_LOSS, AudioManager.AUDIOFOCUS_LOSS_TRANSIENT -> emit("audio:routeChanged", toJson().put("focus", "lost"))
            AudioManager.AUDIOFOCUS_GAIN -> emit("audio:routeChanged", toJson().put("focus", "gained"))
        }
    }

    private fun toEndpoint(d: AudioDeviceInfo): AudioEndpoint? = when (d.type) {
        AudioDeviceInfo.TYPE_BUILTIN_EARPIECE -> AudioEndpoint("earpiece", EndpointType.EARPIECE, "Phone")
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> AudioEndpoint("speaker", EndpointType.SPEAKER, "Speaker")
        AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_USB_HEADSET ->
            AudioEndpoint("wired_headset:${d.id}", EndpointType.WIRED_HEADSET, d.productName?.toString()?.ifBlank { null } ?: "Wired headset")
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_HEARING_AID ->
            AudioEndpoint("bluetooth:${d.id}", EndpointType.BLUETOOTH, d.productName?.toString()?.ifBlank { null } ?: "Bluetooth")
        else -> if (Build.VERSION.SDK_INT >= 31 && d.type == AudioDeviceInfo.TYPE_BLE_HEADSET)
            AudioEndpoint("bluetooth:${d.id}", EndpointType.BLUETOOTH, d.productName?.toString()?.ifBlank { null } ?: "Bluetooth") else null
    }

    private fun communicationDevices(audio: AudioManager): List<AudioDeviceInfo> =
        if (Build.VERSION.SDK_INT >= 31) audio.availableCommunicationDevices else audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).toList()

    private fun selectViaAudioManager(ctx: Context, endpoint: AudioEndpoint): Boolean {
        val audio = am(ctx)
        if (Build.VERSION.SDK_INT >= 31) {
            val device = communicationDevices(audio).firstOrNull { toEndpoint(it)?.id == endpoint.id } ?: return false
            return audio.setCommunicationDevice(device)         // false = Android refused: reported, never faked
        }
        @Suppress("DEPRECATION") run {
            when (endpoint.type) {
                EndpointType.SPEAKER -> { audio.stopBluetoothSco(); audio.isBluetoothScoOn = false; audio.isSpeakerphoneOn = true }
                EndpointType.BLUETOOTH -> { audio.isSpeakerphoneOn = false; audio.startBluetoothSco(); audio.isBluetoothScoOn = true }
                else -> { audio.stopBluetoothSco(); audio.isBluetoothScoOn = false; audio.isSpeakerphoneOn = false }
            }
        }
        return true
    }

    private fun selectedFromAudioManager(ctx: Context, endpoints: List<AudioEndpoint>): String? {
        val audio = am(ctx)
        if (Build.VERSION.SDK_INT >= 31) return audio.communicationDevice?.let { toEndpoint(it)?.id }
        @Suppress("DEPRECATION") run {
            if (audio.isSpeakerphoneOn) return "speaker"
            if (audio.isBluetoothScoOn) return endpoints.firstOrNull { it.type == EndpointType.BLUETOOTH }?.id
        }
        return endpoints.firstOrNull { it.type == EndpointType.WIRED_HEADSET }?.id ?: "earpiece"
    }

    // ------------------------------------------------------------------ state + events
    private fun refresh(applyDefault: Boolean) {
        val ctx = context ?: return
        if (!started) return
        val read: Pair<List<AudioEndpoint>, String?> = try {
            if (viaTelecom) {
                val eps = AudioModel.fromRouteMask(telecomMask)
                eps to AudioModel.typeForRoute(telecomRoute)?.let { t -> eps.firstOrNull { it.type == t }?.id }
            } else {
                val seen = HashSet<String>()
                val eps = communicationDevices(am(ctx)).mapNotNull { toEndpoint(it) }.filter { seen.add(it.id) }
                    .let { list ->                                              // Android < 12 reports every output: keep one of each kind
                        if (Build.VERSION.SDK_INT >= 31) list else list.distinctBy { it.type }
                    }
                eps to selectedFromAudioManager(ctx, eps)
            }
        } catch (e: Exception) {
            Logger.warn("Goppo: audio state could not be read (${e.javaClass.simpleName})")
            return
        }
        val endpoints = read.first
        var selected: String? = read.second
        // A device that is no longer connected can never stay selected.
        if (selected != null && endpoints.none { it.id == selected }) selected = null
        val previous = state
        // Publish the new state FIRST: select() validates against it (the default route is chosen from the fresh device list).
        state = AudioState(endpoints, selected, previous.muted)
        if (previous.endpoints != endpoints) emit("audio:devicesChanged", toJson())
        if (previous.selectedId != selected) emit("audio:routeChanged", toJson())
        if (!applyDefault) return

        val newHeadset = endpoints.firstOrNull { e ->
            (e.type == EndpointType.BLUETOOTH || e.type == EndpointType.WIRED_HEADSET) && previous.endpoints.none { it.id == e.id }
        }
        val selectedType = endpoints.firstOrNull { it.id == selected }?.type
        val target: AudioEndpoint? = when {
            selected == null -> AudioModel.defaultEndpoint(endpoints, video)       // start of the call, or the chosen device vanished
            previous.endpoints.isNotEmpty() && newHeadset != null && selectedType == EndpointType.EARPIECE -> newHeadset   // a headset was just connected
            !viaTelecom && video && selectedType == EndpointType.EARPIECE -> endpoints.firstOrNull { it.type == EndpointType.SPEAKER }
            else -> null
        }
        if (target != null && target.id != selected) select(target.id)
    }

    private fun emit(command: String, detail: JSONObject) = main.post { GoppoRuntime.emitToWeb(command, detail) }

    private const val ROUTE_VERIFY_MS = 2_500L
}
