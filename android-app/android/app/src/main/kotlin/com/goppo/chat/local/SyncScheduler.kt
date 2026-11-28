package com.goppo.chat.local

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Handler
import android.os.Looper
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.Data
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/** WorkManager job: one synchronisation pass. Survives app close / process death; runs only with a network connection. */
class SyncWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result = when (SyncEngine.run(applicationContext)) {
        SyncEngine.Outcome.RETRY -> if (runAttemptCount < 8) Result.retry() else Result.success()   // exponential back-off, bounded
        else -> Result.success()                          // done, or signed out (nothing to retry until the person logs in)
    }
}

/** WorkManager job: download one photo / video (automatic for NEW media, or asked for by the person). */
class DownloadWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val id = inputData.getLong("media_id", 0L)
        val store = Session.store(applicationContext) ?: return Result.success()
        return when (MediaManager.download(applicationContext, store, id)) {
            "failed" -> if (runAttemptCount < 3) Result.retry() else Result.success()
            else -> Result.success()
        }
    }
}

object SyncScheduler {
    private val net = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
    private val handler = Handler(Looper.getMainLooper())
    @Volatile private var pending: Runnable? = null
    @Volatile private var registered = false

    /** Run a pass as soon as there is a network. Several calls close together collapse into one queued job. */
    fun enqueueNow(context: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>().setConstraints(net).setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork("goppo-sync", ExistingWorkPolicy.APPEND_OR_REPLACE, req)
    }

    fun enqueueDownload(context: Context, mediaId: Long) {
        val req = OneTimeWorkRequestBuilder<DownloadWorker>().setConstraints(net).setInputData(Data.Builder().putLong("media_id", mediaId).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork("goppo-media-$mediaId", ExistingWorkPolicy.KEEP, req)
    }

    /** A safety net every ~15 minutes (Android's minimum), only on a network and not on a nearly empty battery. */
    fun schedulePeriodic(context: Context) {
        val c = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).setRequiresBatteryNotLow(true).build()
        val req = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES).setConstraints(c).build()
        WorkManager.getInstance(context.applicationContext).enqueueUniquePeriodicWork("goppo-sync-periodic", ExistingPeriodicWorkPolicy.KEEP, req)
    }

    /**
     * When a network (re)appears - Wi-Fi <-> mobile data, airplane mode off - wake every waiting outbox item (its back-off does
     * not matter any more) and sync. Debounced: a flapping connection causes one job, not twenty.
     */
    fun observeNetwork(context: Context) {
        if (registered) return
        registered = true
        val app = context.applicationContext
        val cm = app.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return
        runCatching {
            cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    pending?.let { handler.removeCallbacks(it) }
                    val r = Runnable { Thread { runCatching { Session.store(app)?.outboxWakeAll() } ; enqueueNow(app) }.start() }
                    pending = r; handler.postDelayed(r, 1500)
                }
            })
        }.onFailure { registered = false }
    }
}
