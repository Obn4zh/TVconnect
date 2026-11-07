package dev.tvconnect.tv.net

import java.io.DataInputStream
import java.io.EOFException
import java.io.IOException
import java.io.OutputStream
import java.math.BigInteger
import java.nio.ByteBuffer
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPublicKeySpec
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Wire constants — must match desktop/tvconnect_desktop/protocol/constants.py and docs/PROTOCOL.md. */
object Proto {
    const val VERSION = 1
    const val PORT = 47800
    const val DISCOVERY_PORT = 47801
    const val SERVICE_TYPE = "_tvconnect._tcp."
    const val DISCOVER_MAGIC = "TVC_DISCOVER 1"

    const val T_CONTROL = 0x01
    const val T_VIDEO = 0x02
    const val T_INPUT = 0x03

    const val VF_KEY = 0x01
    const val VF_CONFIG = 0x02

    const val MAX_RECORD = 16 * 1024 * 1024
    const val GCM_TAG = 16
    val LABEL = "TVconnect-v1".toByteArray(Charsets.US_ASCII)
    val HKDF_INFO = "tvc1 session keys".toByteArray(Charsets.US_ASCII)
}

class ProtocolException(msg: String) : IOException(msg)

class SessionKeys(val d2t: ByteArray, val t2d: ByteArray, val sas: String)

object Crypto {
    private fun u32(n: Int) = ByteBuffer.allocate(4).putInt(n).array()

    fun sha256(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(data)

    fun transcriptHash(hello: ByteArray, tvEphPub: ByteArray, tvIdPub: ByteArray): ByteArray {
        val md = MessageDigest.getInstance("SHA-256")
        md.update(Proto.LABEL)
        for (p in arrayOf(hello, tvEphPub, tvIdPub)) {
            md.update(u32(p.size))
            md.update(p)
        }
        return md.digest()
    }

    private fun hmac(key: ByteArray, vararg parts: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(if (key.isEmpty()) ByteArray(32) else key, "HmacSHA256"))
        for (p in parts) mac.update(p)
        return mac.doFinal()
    }

    /** RFC 5869 HKDF-SHA256. */
    fun hkdf(ikm: ByteArray, salt: ByteArray, info: ByteArray, length: Int): ByteArray {
        val prk = hmac(salt, ikm)
        val out = ByteArray(length)
        var t = ByteArray(0)
        var pos = 0
        var i = 1
        while (pos < length) {
            t = hmac(prk, t, info, byteArrayOf(i.toByte()))
            val n = minOf(t.size, length - pos)
            System.arraycopy(t, 0, out, pos, n)
            pos += n
            i++
        }
        return out
    }

    fun deriveKeys(shared: ByteArray, th: ByteArray): SessionKeys {
        val okm = hkdf(shared, th, Proto.HKDF_INFO, 68)
        val sasNum = (ByteBuffer.wrap(okm, 64, 4).int.toLong() and 0xFFFFFFFFL) % 1_000_000L
        return SessionKeys(okm.copyOfRange(0, 32), okm.copyOfRange(32, 64), "%06d".format(sasNum))
    }

    fun generateEphemeral(): KeyPair =
        KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()

    private fun fixed32(v: BigInteger): ByteArray {
        val b = v.toByteArray()
        return when {
            b.size == 32 -> b
            b.size > 32 -> b.copyOfRange(b.size - 32, b.size)
            else -> ByteArray(32 - b.size) + b
        }
    }

    fun encodePoint(pub: ECPublicKey): ByteArray =
        byteArrayOf(0x04) + fixed32(pub.w.affineX) + fixed32(pub.w.affineY)

    fun decodePoint(raw: ByteArray, params: ECParameterSpec): ECPublicKey {
        if (raw.size != 65 || raw[0] != 0x04.toByte()) throw ProtocolException("invalid EC point")
        val x = BigInteger(1, raw.copyOfRange(1, 33))
        val y = BigInteger(1, raw.copyOfRange(33, 65))
        // KeyFactory validates that the point lies on the curve.
        return KeyFactory.getInstance("EC").generatePublic(ECPublicKeySpec(ECPoint(x, y), params)) as ECPublicKey
    }

    fun ecdh(priv: PrivateKey, peer: ECPublicKey): ByteArray =
        KeyAgreement.getInstance("ECDH").run {
            init(priv)
            doPhase(peer, true)
            generateSecret()
        }
}

/** AES-256-GCM with nonce = 0x00000000 || counter64, one instance per direction. */
class RecordCipher(key: ByteArray) {
    private val keySpec = SecretKeySpec(key, "AES")
    private var counter = 0L

    private fun nonce(): ByteArray {
        if (counter == -1L) throw ProtocolException("nonce counter exhausted")
        val n = ByteBuffer.allocate(12).putInt(0).putLong(counter).array()
        counter++
        return n
    }

    fun seal(plain: ByteArray, aad: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, keySpec, GCMParameterSpec(128, nonce()))
        c.updateAAD(aad)
        return c.doFinal(plain)
    }

    fun open(ct: ByteArray, aad: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, keySpec, GCMParameterSpec(128, nonce()))
        c.updateAAD(aad)
        return try {
            c.doFinal(ct)
        } catch (e: javax.crypto.AEADBadTagException) {
            throw ProtocolException("record authentication failed")
        }
    }
}

class Record(val type: Int, val payload: ByteArray)

/** Length-prefixed records, encrypted once [tx]/[rx] are set. Writes are serialized. */
class FramedIO(private val input: DataInputStream, private val output: OutputStream) {
    @Volatile var tx: RecordCipher? = null
    @Volatile var rx: RecordCipher? = null
    private val writeLock = Any()

    fun read(): Record {
        val header = ByteArray(4)
        try {
            input.readFully(header)
        } catch (e: EOFException) {
            throw EOFException("connection closed by peer")
        }
        val len = ByteBuffer.wrap(header).int
        if (len < 1 || len > Proto.MAX_RECORD) throw ProtocolException("invalid record length $len")
        var body = ByteArray(len)
        input.readFully(body)
        rx?.let { body = it.open(body, header) }
        if (body.isEmpty()) throw ProtocolException("empty record")
        return Record(body[0].toInt() and 0xFF, body.copyOfRange(1, body.size))
    }

    fun write(type: Int, payload: ByteArray) {
        val plain = ByteArray(payload.size + 1)
        plain[0] = type.toByte()
        System.arraycopy(payload, 0, plain, 1, payload.size)
        synchronized(writeLock) {
            val cipher = tx
            val out: ByteArray
            if (cipher != null) {
                val header = ByteBuffer.allocate(4).putInt(plain.size + Proto.GCM_TAG).array()
                out = header + cipher.seal(plain, header)
            } else {
                out = ByteBuffer.allocate(4).putInt(plain.size).array() + plain
            }
            output.write(out)
            output.flush()
        }
    }
}
