package com.goppo.chat

import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/**
 * Asks Android for runtime permissions (microphone / camera), one request at a time. Main thread only.
 * Must be created before the activity is STARTED (it registers an ActivityResult launcher).
 * It only ever reports what Android actually granted; nothing is granted on the user's behalf.
 */
class MediaPermissionRequester(private val activity: ComponentActivity) {

    class Outcome(val granted: Set<String>, val permanentlyDenied: Set<String>)

    private class Pending(val permissions: List<String>, val callback: (Outcome) -> Unit)

    private val queue = ArrayDeque<Pending>()
    private var active: Pending? = null

    private val launcher = activity.registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { _ ->
        onResult()
    }

    fun grantedAmong(permissions: List<String>): Set<String> =
        permissions.filter { ContextCompat.checkSelfPermission(activity, it) == PackageManager.PERMISSION_GRANTED }.toSet()

    fun request(permissions: List<String>, callback: (Outcome) -> Unit) {
        val wanted = permissions.distinct()
        if (wanted.isEmpty()) { callback(Outcome(emptySet(), emptySet())); return }
        queue.addLast(Pending(wanted, callback))
        pump()
    }

    private fun pump() {
        if (active != null) return
        val next = queue.removeFirstOrNull() ?: return
        val missing = next.permissions - grantedAmong(next.permissions)
        if (missing.isEmpty()) {
            next.callback(Outcome(next.permissions.toSet(), emptySet()))
            pump()
            return
        }
        active = next
        launcher.launch(missing.toTypedArray())
    }

    private fun onResult() {
        val done = active ?: return
        active = null
        val granted = grantedAmong(done.permissions)
        // After a refusal, "no rationale to show" means "don't ask again": only Settings can fix it.
        val permanent = done.permissions
            .filter { it !in granted && !ActivityCompat.shouldShowRequestPermissionRationale(activity, it) }
            .toSet()
        done.callback(Outcome(granted, permanent))
        pump()
    }
}
