package dev.tvconnect.tv.service

import android.content.Context
import android.content.Intent
import dev.tvconnect.tv.ui.PairingActivity
import dev.tvconnect.tv.ui.ProjectionActivity
import dev.tvconnect.tv.util.Logger
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

enum class PairDecision { ALLOW_ONCE, ALLOW_REMEMBER, DENY, TIMEOUT }

/**
 * Bridges a network thread waiting for a user decision and the Activity that asks the user.
 * Only one request may be pending at a time.
 */
object PairingBroker {
    class Request(val id: Int, val clientName: String, val clientIp: String, val sas: String) {
        internal val latch = CountDownLatch(1)
        @Volatile internal var decision = PairDecision.DENY
        @Volatile var onFinished: (() -> Unit)? = null
    }

    private val ids = AtomicInteger(1)
    @Volatile var current: Request? = null
        private set

    fun request(ctx: Context, clientName: String, clientIp: String, sas: String, timeoutMs: Long): PairDecision {
        val req = Request(ids.getAndIncrement(), clientName, clientIp, sas)
        synchronized(this) {
            if (current != null) return PairDecision.DENY
            current = req
        }
        try {
            ctx.startActivity(
                Intent(ctx, PairingActivity::class.java)
                    .putExtra(PairingActivity.EXTRA_ID, req.id)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            )
        } catch (e: Exception) {
            Logger.e("Pairing", "cannot show pairing dialog", e)
        }
        val answered = req.latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        synchronized(this) { if (current === req) current = null }
        req.onFinished?.invoke()
        return if (answered) req.decision else PairDecision.TIMEOUT
    }

    fun resolve(id: Int, decision: PairDecision) {
        val req = current ?: return
        if (req.id != id) return
        req.decision = decision
        req.latch.countDown()
    }

    /** Cancels a pending request when the client disconnects. */
    fun cancelAll() {
        current?.let { it.decision = PairDecision.DENY; it.latch.countDown() }
    }
}

/** Same idea for the system MediaProjection consent dialog. */
object ProjectionBroker {
    class Result(val resultCode: Int, val data: Intent?, val error: String?)

    private var latch: CountDownLatch? = null
    @Volatile private var result: Result? = null

    fun request(ctx: Context, timeoutMs: Long): Result {
        val l = CountDownLatch(1)
        synchronized(this) {
            latch = l
            result = null
        }
        try {
            ctx.startActivity(Intent(ctx, ProjectionActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            Logger.e("Projection", "cannot start consent activity", e)
            return Result(0, null, "Не удалось открыть системный диалог захвата: ${e.message}")
        }
        if (!l.await(timeoutMs, TimeUnit.MILLISECONDS)) return Result(0, null, "Захват экрана не подтверждён вовремя")
        return result ?: Result(0, null, "no result")
    }

    fun resolve(r: Result) {
        synchronized(this) {
            result = r
            latch?.countDown()
        }
    }

    /** Set by ProjectionActivity while the system dialog is on screen. */
    @Volatile var dismiss: (() -> Unit)? = null

    /** Client went away: close the system dialog and release the waiting thread. */
    fun cancel() {
        dismiss?.invoke()
        resolve(Result(0, null, "cancelled"))
    }
}
