package com.goppo.chat.local

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.io.File

/**
 * The local message database: one SQLite file per signed-in account, in the app's PRIVATE storage
 * (Context.getDatabasePath -> /data/data/<app>/databases). Never on shared storage, never a public file URL, no tokens inside.
 *
 * Migrations: [MIGRATIONS] holds one list of SQL statements per schema version; onCreate runs all of them, onUpgrade runs the
 * missing ones, in order, inside Android's transaction. A new version only ever ADDS (new table / new column) so an app update
 * keeps every stored message. onDowngrade is refused (never silently drops data).
 */
class LocalDb private constructor(context: Context, val userId: Long) :
    SQLiteOpenHelper(context.applicationContext, fileName(userId), null, VERSION) {

    override fun onConfigure(db: SQLiteDatabase) {
        db.setForeignKeyConstraintsEnabled(false)
        db.enableWriteAheadLogging()                       // readers (the page) never block the sync writer
    }

    override fun onCreate(db: SQLiteDatabase) {
        MIGRATIONS.forEach { statements -> statements.forEach(db::execSQL) }
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        for (v in oldVersion until newVersion) MIGRATIONS[v].forEach(db::execSQL)     // MIGRATIONS[v] upgrades v -> v+1
    }

    override fun onDowngrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        throw IllegalStateException("The local database is newer than this app; refusing to downgrade ($oldVersion -> $newVersion)")
    }

    companion object {
        const val VERSION = 2
        fun fileName(userId: Long) = "goppo_u$userId.db"

        /** index 0 creates version 1; index 1 upgrades 1 -> 2 ... */
        val MIGRATIONS: List<List<String>> = listOf(
            listOf(
                """CREATE TABLE messages (
                    local_id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER, uuid TEXT NOT NULL UNIQUE,
                    conv_id INTEGER NOT NULL, sender_id INTEGER, mine INTEGER NOT NULL DEFAULT 0, type TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, local_created_at INTEGER NOT NULL, server_at TEXT,
                    delivered_at TEXT, seen_at TEXT, deleted INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
                    rev INTEGER NOT NULL DEFAULT 0, reply_to INTEGER, delete_pending INTEGER NOT NULL DEFAULT 0)""",
                "CREATE UNIQUE INDEX ux_messages_server ON messages(server_id) WHERE server_id IS NOT NULL",
                "CREATE INDEX ix_messages_conv ON messages(conv_id, server_id, local_created_at)",
                """CREATE TABLE attachments (
                    media_id INTEGER PRIMARY KEY AUTOINCREMENT, message_uuid TEXT NOT NULL UNIQUE, server_att_id INTEGER,
                    url TEXT, thumb_url TEXT, name TEXT, size INTEGER, mime TEXT, duration REAL, width INTEGER, height INTEGER,
                    local_path TEXT, state TEXT NOT NULL DEFAULT 'none', downloaded_at INTEGER, source TEXT NOT NULL DEFAULT 'remote')""",
                "CREATE INDEX ix_att_server ON attachments(server_att_id)",
                """CREATE TABLE conversations (
                    id INTEGER PRIMARY KEY, peer_id INTEGER, peer_name TEXT, peer_username TEXT, peer_avatar TEXT,
                    last_message_at INTEGER NOT NULL DEFAULT 0, unread INTEGER NOT NULL DEFAULT 0)""",
                """CREATE TABLE outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, message_uuid TEXT NOT NULL, server_id INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
                    last_error TEXT, UNIQUE(kind, message_uuid))""",
                "CREATE TABLE sync_state (k TEXT PRIMARY KEY, v TEXT)",
            ),
            // 1 -> 2: remember which conversations still owe the server a "seen" receipt (never lost on process death)
            listOf("CREATE TABLE seen_queue (conv_id INTEGER PRIMARY KEY, upto INTEGER NOT NULL)"),
        )

        private val open = HashMap<Long, LocalDb>()

        @Synchronized
        fun forUser(context: Context, userId: Long): LocalDb = open.getOrPut(userId) { LocalDb(context, userId) }

        /** Closes and deletes one account's database and media files (logout / account switch). Server history is untouched. */
        @Synchronized
        fun wipe(context: Context, userId: Long) {
            open.remove(userId)?.close()
            context.applicationContext.deleteDatabase(fileName(userId))
            File(context.applicationContext.filesDir, "media/$userId").deleteRecursively()
            File(context.applicationContext.filesDir, "outbox/$userId").deleteRecursively()
        }
    }
}
