package dev.tvconnect.tv.auth

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import dev.tvconnect.tv.util.Logger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * Long-term TV identity: a non-exportable P-256 key in AndroidKeyStore. Desktop clients pin
 * SHA-256(public key) after pairing, so a different device on the same IP is detected.
 */
object IdentityKey {
    private const val ALIAS = "tvconnect_identity_v1"
    private val lock = Any()

    private fun keyStore() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    private fun ensure(): KeyStore {
        val ks = keyStore()
        if (!ks.containsAlias(ALIAS)) {
            Logger.i("IdentityKey", "generating identity key")
            val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
            gen.initialize(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .build()
            )
            gen.generateKeyPair()
        }
        return ks
    }

    fun publicDer(): ByteArray = synchronized(lock) {
        ensure().getCertificate(ALIAS).publicKey.encoded
    }

    fun sign(data: ByteArray): ByteArray = synchronized(lock) {
        val priv = ensure().getKey(ALIAS, null) as PrivateKey
        Signature.getInstance("SHA256withECDSA").run {
            initSign(priv)
            update(data)
            sign()
        }
    }

    /** Re-creating the key makes every desktop show an identity-mismatch warning. */
    fun reset() = synchronized(lock) {
        keyStore().deleteEntry(ALIAS)
    }
}
