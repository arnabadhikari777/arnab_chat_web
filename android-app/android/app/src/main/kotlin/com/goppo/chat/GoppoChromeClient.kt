package com.goppo.chat

import android.view.View
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient.CustomViewCallback
import com.getcapacitor.Bridge
import com.getcapacitor.BridgeWebChromeClient

/**
 * WebChromeClient for the website:
 *  - getUserMedia (microphone / camera) permission requests: only from the website origin, only audio/video capture,
 *    only after Android itself has granted the matching runtime permission.
 *  - real full-screen video (Capacitor's default hides it immediately).
 * File choosing (gallery, camera, documents, cancel, multiple) is Capacitor's own implementation, inherited.
 */
class GoppoChromeClient(
    bridge: Bridge,
    private val policy: UrlPolicy,
    private val activity: MainActivity,
) : BridgeWebChromeClient(bridge) {

    private var customView: View? = null
    private var customViewCallback: CustomViewCallback? = null

    override fun onPermissionRequest(request: PermissionRequest) {
        val origin = request.origin?.toString()
        val needed = MediaPolicy.requiredPermissions(request.resources)
        if (!policy.isAppOrigin(origin) || needed.isEmpty()) {
            request.deny()
            return
        }
        activity.mediaPermissions.request(needed) { outcome ->
            val allowed = MediaPolicy.grantableResources(request.resources, outcome.granted)
            try {
                if (allowed.isEmpty()) request.deny() else request.grant(allowed)
            } catch (_: IllegalStateException) {
                // The page cancelled the request meanwhile.
            }
        }
    }

    override fun onShowCustomView(view: View?, callback: CustomViewCallback?) {
        if (view == null) { callback?.onCustomViewHidden(); return }
        if (customView != null) { callback?.onCustomViewHidden(); return }
        customView = view
        customViewCallback = callback
        activity.showFullscreenView(view)
    }

    override fun onHideCustomView() {
        if (customView == null) return
        activity.hideFullscreenView()
        customView = null
        customViewCallback?.onCustomViewHidden()
        customViewCallback = null
    }

    /** @return true if a full-screen video was showing and has been closed. */
    fun exitFullscreen(): Boolean {
        if (customView == null) return false
        onHideCustomView()
        return true
    }
}
