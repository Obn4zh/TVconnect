package dev.tvconnect.tv.net

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.File
import java.security.interfaces.ECPublicKey

/** Cross-checks the Kotlin implementation against vectors produced by the Python client. */
class ProtocolTest {
    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private fun vectors(): JSONObject {
        var dir: File? = File("").absoluteFile
        while (dir != null) {
            val f = File(dir, "protocol/test-vectors/crypto.json")
            if (f.exists()) return JSONObject(f.readText())
            dir = dir.parentFile
        }
        error("crypto.json not found")
    }

    @Test
    fun matchesPythonVectors() {
        val v = vectors()
        val th = Crypto.transcriptHash(hex(v.getString("hello")), hex(v.getString("tv_eph_pub")), hex(v.getString("tv_id_pub")))
        assertArrayEquals(hex(v.getString("transcript_hash")), th)
        val keys = Crypto.deriveKeys(hex(v.getString("shared")), th)
        assertArrayEquals(hex(v.getString("k_d2t")), keys.d2t)
        assertArrayEquals(hex(v.getString("k_t2d")), keys.t2d)
        assertEquals(v.getString("sas"), keys.sas)

        val rx = RecordCipher(keys.d2t)
        val recs = v.getJSONArray("records_d2t")
        for (i in 0 until recs.length()) {
            val r = recs.getJSONObject(i)
            assertArrayEquals(hex(r.getString("plain")), rx.open(hex(r.getString("ct")), hex(r.getString("aad"))))
        }
    }

    @Test
    fun ecdhRoundTrip() {
        val a = Crypto.generateEphemeral()
        val b = Crypto.generateEphemeral()
        val aPub = a.public as ECPublicKey
        val bRaw = Crypto.encodePoint(b.public as ECPublicKey)
        val bPub = Crypto.decodePoint(bRaw, aPub.params)
        assertArrayEquals(Crypto.ecdh(a.private, bPub), Crypto.ecdh(b.private, aPub))
    }

    @Test
    fun framedEncryptedRoundTrip() {
        val key = ByteArray(32) { it.toByte() }
        val buf = ByteArrayOutputStream()
        val w = FramedIO(DataInputStream(ByteArrayInputStream(ByteArray(0))), buf)
        w.tx = RecordCipher(key)
        w.write(Proto.T_CONTROL, "{\"type\":\"pong\"}".toByteArray())
        w.write(Proto.T_VIDEO, ByteArray(70000) { 7 })
        val r = FramedIO(DataInputStream(ByteArrayInputStream(buf.toByteArray())), ByteArrayOutputStream())
        r.rx = RecordCipher(key)
        val a = r.read()
        assertEquals(Proto.T_CONTROL, a.type)
        assertEquals("{\"type\":\"pong\"}", String(a.payload))
        assertEquals(70000, r.read().payload.size)
    }

    @Test
    fun tamperRejected() {
        val key = ByteArray(32)
        val ct = RecordCipher(key).seal("hi".toByteArray(), byteArrayOf(1))
        ct[0] = (ct[0].toInt() xor 1).toByte()
        try {
            RecordCipher(key).open(ct, byteArrayOf(1))
            fail("expected failure")
        } catch (_: ProtocolException) {
        }
    }
}
