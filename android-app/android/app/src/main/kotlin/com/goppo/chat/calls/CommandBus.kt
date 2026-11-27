package com.goppo.chat.calls

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import com.goppo.chat.GoppoRuntime
import com.goppo.chat.MainActivity
import org.json.JSONObject

/**
 * Native -> website commands that must be ANSWERED (accept / reject / hang up / mute). They are queued until the page is ready
 * (`app:ready`), delivered with `GoppoPlatform.receive()` (which retries until calling.js has registered its handlers), and the
 * outcome comes back through `GoppoNative.commandResult()`. This is the only path from the Android UI into the existing calling
 * code: native never speaks the signaling protocol itself.
 */
object CommandBus {
    class Pending(val id: Long, val command: String, val detail: JSONObject, val expiresAt: Long, val onResult: ((Boolean) -> Unit)?)

    private val main = Handler(Looper.getMainLooper())
    private val queue = ArrayDeque<Pending>()
    private val inFlight = HashMap<Long, Pending>()
    private var nextId = 1L

    fun enqueue(command: String, detail: JSONObject, ttlMs: Long = 30_000L, onResult: ((Boolean) -> Unit)? = null) {
        main.post {
            val p = Pending(nextId++, command, detail, SystemClock.elapsedRealtime() + ttlMs, onResult)
            queue.addLast(p)
            // The page may never become ready (killed WebView, no network): the command must still end, or the caller waits forever.
            main.postDelayed({ if (queue.remove(p)) p.onResult?.invoke(false) }, ttlMs)
            drain()
        }
    }

    /** Sends everything that can be sent now. Call when the page becomes ready or the activity comes back. */
    fun drain() {
        main.post {
            val activity = MainActivity.current?.get() ?: return@post
            if (!GoppoRuntime.state.webReady) return@post
            val now = SystemClock.elapsedRealtime()
            while (queue.isNotEmpty()) {
                val p = queue.removeFirst()
                if (p.expiresAt < now) { p.onResult?.invoke(false); continue }
                inFlight[p.id] = p
                activity.runCommand(p.id, p.command, p.detail)
                main.postDelayed({ result(p.id, handled = false, ok = false) }, RESULT_TIMEOUT_MS)
            }
        }
    }

    /** Called by the plugin when the page answered (or by the timeout above). */
    fun result(id: Long, handled: Boolean, ok: Boolean) {
        main.post { inFlight.remove(id)?.onResult?.invoke(handled && ok) }
    }

    fun clear() {
        main.post {
            val dropped = queue.toList(); queue.clear()
            dropped.forEach { it.onResult?.invoke(false) }          // a dropped accept must not leave the call stuck in "accepting"
        }
    }

    private const val RESULT_TIMEOUT_MS = 12_000L
}
