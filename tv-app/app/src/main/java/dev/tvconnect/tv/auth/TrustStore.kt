package dev.tvconnect.tv.auth

import dev.tvconnect.tv.net.Crypto
import dev.tvconnect.tv.settings.Prefs
import org.json.JSONObject
import java.security.MessageDigest
import java.security.SecureRandom

class TrustedClient(val clientId: String, val name: String, val addedAt: Long, val lastSeen: Long)

/** Trusted desktop clients. Only SHA-256(token) is stored, never the token itself. */
class TrustStore(private val prefs: Prefs) {
    private val lock = Any()

    private fun load(): JSONObject =
        try { JSONObject(prefs.sp.getString("trusted_clients", "{}") ?: "{}") } catch (_: Exception) { JSONObject() }

    private fun save(o: JSONObject) = prefs.sp.edit().putString("trusted_clients", o.toString()).apply()

    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }

    fun list(): List<TrustedClient> = synchronized(lock) {
        val o = load()
        o.keys().asSequence().map { id ->
            val c = o.getJSONObject(id)
            TrustedClient(id, c.optString("name"), c.optLong("added"), c.optLong("last_seen"))
        }.sortedBy { it.name }.toList()
    }

    /** Creates a fresh token for [clientId] and returns it (to be sent once to the client). */
    fun add(clientId: String, name: String): ByteArray = synchronized(lock) {
        val token = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val o = load()
        val now = System.currentTimeMillis()
        o.put(clientId, JSONObject().put("name", name).put("hash", hex(Crypto.sha256(token))).put("added", now).put("last_seen", now))
        save(o)
        token
    }

    fun verify(clientId: String, token: ByteArray): Boolean = synchronized(lock) {
        val o = load()
        val c = o.optJSONObject(clientId) ?: return false
        val expected = c.optString("hash")
        val ok = MessageDigest.isEqual(expected.toByteArray(), hex(Crypto.sha256(token)).toByteArray())
        if (ok) {
            c.put("last_seen", System.currentTimeMillis())
            save(o)
        }
        ok
    }

    fun remove(clientId: String) = synchronized(lock) {
        val o = load()
        o.remove(clientId)
        save(o)
    }

    fun clear() = synchronized(lock) { save(JSONObject()) }
}
