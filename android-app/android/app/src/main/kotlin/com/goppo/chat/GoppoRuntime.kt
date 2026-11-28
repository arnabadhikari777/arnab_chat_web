package com.goppo.chat

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import com.getcapacitor.Logger
import org.json.JSONObject
import java.util.UUID

/** Process-wide Phase 2 state shared by the activity, the WebView clients and the GoppoNative plugin. */
object GoppoRuntime {
    val state = NativeState()
    val lifecycle = LifecycleGate()

    @Volatile
    var policy: UrlPolicy? = null
        private set

    /** Activities currently on screen (started, not stopped). Used to decide whether a message needs a notification. */
    @Volatile var startedActivities: Int = 0
    val appVisible: Boolean get() = startedActivities > 0

    /** Path of the page the WebView is showing ("/chat/12"), or null. */
    @Volatile var currentPath: String? = null

    /** Like [configure] but reads the backend URL from the bundled goppo-backend.json, so it works with no activity (FCM, receivers). */
    fun ensureConfigured(context: Context): UrlPolicy? {
        policy?.let { return it }
        return try {
            // The UI is bundled in the APK (no server.url). The BACKEND the pages talk to comes from assets/goppo-backend.json,
            // written by the frontend build (tools/build_android_web.py). It is a public URL, never a secret.
            val text = context.applicationContext.assets.open("goppo-backend.json").bufferedReader().use { it.readText() }
            configure(JSONObject(text).getString("backendUrl"))
        } catch (e: Exception) {
            Logger.error("Goppo: cannot read backendUrl from goppo-backend.json", e)
            null
        }
    }

    /** Native -> website event on the plugin's "command" channel. Dropped if the page is not there (events are informational). */
    fun emitToWeb(command: String, detail: JSONObject = JSONObject()) {
        MainActivity.current?.get()?.sendCommand(command, com.getcapacitor.JSObject(detail.toString()))
    }

    /** Builds the URL policy from Capacitor's `server.url`. A missing or non-https URL leaves the policy null: nothing loads or opens. */
    @Synchronized
    fun configure(serverUrl: String?): UrlPolicy? {
        policy?.let { return it }
        if (serverUrl == null) return null
        return try {
            UrlPolicy(serverUrl).also { policy = it }
        } catch (e: IllegalArgumentException) {
            Logger.error("Goppo: server.url is not a valid https URL; native bridge disabled", e)
            null
        }
    }
}

/** Opens validated http/https links in the Android system browser. */
object ExternalLinks {
    fun open(context: Context, safeUrl: String): Boolean {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(safeUrl))
            .addCategory(Intent.CATEGORY_BROWSABLE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            context.startActivity(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        } catch (_: SecurityException) {
            false
        }
    }
}

/** A random id created on first use, stored privately; it changes only if app data is cleared or the app is reinstalled. */
object DeviceId {
    private const val PREFS = "goppo_native"
    private const val KEY = "device_id"
    private val VALID = Regex("^[A-Za-z0-9._-]{8,64}$")   // same rule as the server's /api/devices

    @Synchronized
    fun get(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val existing = prefs.getString(KEY, null)
        if (existing != null && VALID.matches(existing)) return existing
        val fresh = UUID.randomUUID().toString()
        prefs.edit().putString(KEY, fresh).commit()
        return fresh
    }
}
