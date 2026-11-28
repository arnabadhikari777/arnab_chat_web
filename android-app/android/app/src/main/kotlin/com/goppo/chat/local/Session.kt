package com.goppo.chat.local

import android.content.Context

/** Which account the local database belongs to. Only a number: no cookie, no token, no password is kept here. */
object Session {
    private const val P = "goppo_session"
    fun userId(context: Context): Long? = context.applicationContext.getSharedPreferences(P, Context.MODE_PRIVATE).getLong("user_id", 0L).takeIf { it > 0 }
    fun begin(context: Context, userId: Long) { context.applicationContext.getSharedPreferences(P, Context.MODE_PRIVATE).edit().putLong("user_id", userId).apply() }
    fun end(context: Context) { context.applicationContext.getSharedPreferences(P, Context.MODE_PRIVATE).edit().remove("user_id").apply() }
    fun store(context: Context): LocalStore? = userId(context)?.let { LocalStore.forUser(context, it) }
}
