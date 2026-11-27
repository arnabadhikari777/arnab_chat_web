package com.goppo.chat.calls

import android.app.Activity
import android.app.KeyguardManager
import android.content.Intent
import android.content.res.Resources
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Outline
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.ViewOutlineProvider
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.goppo.chat.R
import com.goppo.chat.notify.AvatarLoader

/**
 * The incoming-call screen. Shown over the lock screen (full-screen intent) or while the app is open. It only displays what
 * NativeCallManager already holds and only asks the manager to accept / reject; it never talks to the network itself.
 * Accepting from a locked phone first asks Android to unlock (the chat app is never shown over the lock screen).
 */
class IncomingCallActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private var callId: String = ""
    private var expiresAt = 0L
    private var avatarView: ImageView? = null
    private var countdown: TextView? = null
    private var done = false

    private val tick = object : Runnable {
        override fun run() {
            val left = ((expiresAt - System.currentTimeMillis()) / 1000).coerceAtLeast(0)
            countdown?.text = getString(R.string.call_ringing_seconds, left.toInt())
            if (left <= 0) finish() else ui.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val session = NativeCallManager.session()
        callId = intent.getStringExtra(EXTRA_CALL_ID) ?: ""
        if (session == null || session.info.callId != callId || session.phase != CallPhase.RINGING) {
            finish()
            return
        }
        current = this
        expiresAt = session.expiresAtMs
        showOverLockScreen()
        setContentView(buildUi(session))
        ui.post(tick)
        val cached = NativeCallManager.avatar(callId)
        if (cached != null) setAvatar(cached) else AvatarLoader.load(session.info.avatarUrl, 384) { bmp -> if (bmp != null && !isDestroyed && !isFinishing) setAvatar(bmp) }
        if (intent.getBooleanExtra(EXTRA_AUTO_ACCEPT, false)) accept()               // the "Answer" button of the notification
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        // A late or stale intent (an old notification button) may only act on the call this screen is showing.
        val live = NativeCallManager.session()
        if (live == null || live.phase != CallPhase.RINGING) { finish(); return }
        val target = intent?.getStringExtra(EXTRA_CALL_ID)
        if (live.info.callId != callId || (target != null && target != callId)) return
        if (intent?.getBooleanExtra(EXTRA_AUTO_ACCEPT, false) == true) accept()      // the "Answer" button of the notification
    }

    override fun onDestroy() {
        ui.removeCallbacksAndMessages(null)
        if (current === this) current = null
        super.onDestroy()
    }

    /** Back must not silently drop a ringing call. */
    @Suppress("OVERRIDE_DEPRECATION")
    override fun onBackPressed() = Unit

    private fun showOverLockScreen() {
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    private fun dp(v: Int) = (v * Resources.getSystem().displayMetrics.density).toInt()

    private fun buildUi(session: CallSession): View {
        val root = FrameLayout(this).apply {
            background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(0xFF0B6E78.toInt(), 0xFF052B30.toInt()))
        }
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(24), dp(72), dp(24), dp(56))
        }
        val kind = TextView(this).apply {
            text = getString(if (session.info.video) R.string.call_incoming_video else R.string.call_incoming_voice)
            setTextColor(0xCCFFFFFF.toInt()); textSize = 16f; gravity = Gravity.CENTER
        }
        val avatar = ImageView(this).apply {
            scaleType = ImageView.ScaleType.CENTER_CROP
            setImageResource(R.drawable.ic_stat_goppo)
            setBackgroundColor(0x33FFFFFF)
            setPadding(dp(36), dp(36), dp(36), dp(36))
            outlineProvider = object : ViewOutlineProvider() {
                override fun getOutline(view: View, outline: Outline) { outline.setOval(0, 0, view.width, view.height) }
            }
            clipToOutline = true
            contentDescription = session.info.peerName
        }
        avatarView = avatar
        val name = TextView(this).apply {
            text = session.info.peerName
            setTextColor(Color.WHITE); textSize = 30f; gravity = Gravity.CENTER
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }
        val seconds = TextView(this).apply { setTextColor(0x99FFFFFF.toInt()); textSize = 14f; gravity = Gravity.CENTER }
        countdown = seconds
        val wide = ViewGroup.LayoutParams.MATCH_PARENT
        val wrap = ViewGroup.LayoutParams.WRAP_CONTENT
        column.addView(kind, LinearLayout.LayoutParams(wide, wrap))
        column.addView(avatar, LinearLayout.LayoutParams(dp(160), dp(160)).apply { topMargin = dp(40); gravity = Gravity.CENTER_HORIZONTAL })
        column.addView(name, LinearLayout.LayoutParams(wide, wrap).apply { topMargin = dp(28) })
        column.addView(seconds, LinearLayout.LayoutParams(wide, wrap).apply { topMargin = dp(8) })
        column.addView(View(this), LinearLayout.LayoutParams(0, 0, 1f))

        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        buttons.addView(roundButton(0xFFD93025.toInt(), R.drawable.ic_call_decline, getString(R.string.call_decline)) { reject() }, buttonParams())
        buttons.addView(roundButton(0xFF1E9E5A.toInt(), R.drawable.ic_call_accept, getString(R.string.call_accept)) { accept() }, buttonParams())
        column.addView(buttons, LinearLayout.LayoutParams(wide, wrap))
        root.addView(column, FrameLayout.LayoutParams(wide, wide))
        return root
    }

    private fun buttonParams() = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)

    private fun roundButton(color: Int, icon: Int, label: String, onClick: () -> Unit): View {
        val holder = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER }
        val button = ImageButton(this).apply {
            setImageResource(icon)
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(color) }
            contentDescription = label
            setOnClickListener { onClick() }
        }
        val text = TextView(this).apply { this.text = label; setTextColor(Color.WHITE); textSize = 14f; gravity = Gravity.CENTER }
        holder.addView(button, LinearLayout.LayoutParams(dp(72), dp(72)))
        holder.addView(text, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) })
        return holder
    }

    fun setAvatar(bitmap: Bitmap) {
        avatarView?.apply { setPadding(0, 0, 0, 0); setBackgroundColor(Color.TRANSPARENT); setImageBitmap(bitmap) }
    }

    private fun accept() {
        if (done) return
        val keyguard = getSystemService(KeyguardManager::class.java)
        if (Build.VERSION.SDK_INT >= 26 && keyguard != null && keyguard.isKeyguardLocked) {
            keyguard.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
                override fun onDismissSucceeded() = proceedAccept()
                override fun onDismissCancelled() { /* still ringing: the person can try again or decline */ }
                override fun onDismissError() { /* same */ }
            })
        } else {
            proceedAccept()
        }
    }

    private fun proceedAccept() {
        if (done) return
        done = true
        NativeCallManager.accept(this)
        finish()
    }

    private fun reject() {
        if (done) return
        done = true
        NativeCallManager.reject(this)
        finish()
    }

    companion object {
        const val EXTRA_CALL_ID = "goppo.call_id"
        const val EXTRA_AUTO_ACCEPT = "goppo.auto_accept"
        const val ACTION_ACCEPT = "com.goppo.chat.action.ACCEPT_CALL"

        @Volatile
        var current: IncomingCallActivity? = null
            private set

        /** The call stopped ringing (cancelled, answered elsewhere, timed out): close the screen. */
        fun dismiss() {
            val a = current ?: return
            a.runOnUiThread { a.done = true; a.finish() }
        }
    }
}
