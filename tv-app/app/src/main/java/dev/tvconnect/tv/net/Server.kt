package dev.tvconnect.tv.net

import dev.tvconnect.tv.service.ConnectionService
import dev.tvconnect.tv.util.Logger
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.DataInputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket

/** Accepts desktop connections. Only one active session at a time. */
class Server(private val svc: ConnectionService) {
    private var server: ServerSocket? = null
    @Volatile var active: Session? = null
        private set
    @Volatile private var running = false

    fun start() {
        running = true
        Thread({ acceptLoop() }, "tcp-accept").start()
    }

    private fun acceptLoop() {
        var backoff = 1000L
        while (running) {
            try {
                val ss = ServerSocket().apply {
                    reuseAddress = true
                    bind(InetSocketAddress(Proto.PORT))
                }
                server = ss
                Logger.i("Server", "listening on TCP ${Proto.PORT}")
                svc.onListening(true)
                backoff = 1000L
                while (running) {
                    val s = ss.accept()
                    handle(s)
                }
            } catch (e: Exception) {
                if (!running) break
                Logger.e("Server", "server socket failed, retrying in ${backoff}ms", e)
                svc.onListening(false)
                Thread.sleep(backoff)
                backoff = (backoff * 2).coerceAtMost(30_000)
            }
        }
        svc.onListening(false)
    }

    private fun handle(s: Socket) {
        val ip = s.inetAddress.hostAddress ?: "?"
        val reject: Pair<String, String>? = when {
            !svc.prefs.remoteEnabled -> "disabled" to "Remote control is disabled on this TV"
            active != null -> "busy" to "Another computer is connected"
            svc.limiter.isBlocked(ip) -> "rate_limited" to "Too many denied requests"
            else -> null
        }
        if (reject != null) {
            Logger.i("Server", "rejecting $ip: ${reject.first}")
            Thread({ rejectWith(s, reject.first, reject.second) }, "tcp-reject").start()
            return
        }
        Logger.i("Server", "connection from $ip")
        val session = Session(svc, s) { closed -> if (active === closed) active = null }
        active = session
        session.start()
    }

    /** Reads the client's hello first so the error is delivered instead of a TCP reset. */
    private fun rejectWith(s: Socket, code: String, message: String) {
        try {
            s.soTimeout = 3000
            val io = FramedIO(DataInputStream(BufferedInputStream(s.getInputStream())), s.getOutputStream())
            runCatching { io.read() }
            io.write(Proto.T_CONTROL, JSONObject().put("type", "error").put("code", code).put("message", message).toString().toByteArray())
            s.shutdownOutput()
            Thread.sleep(200)
        } catch (_: Exception) {
        } finally {
            runCatching { s.close() }
        }
    }

    fun disconnectActive() {
        active?.close("disconnected on TV")
    }

    fun stop() {
        running = false
        runCatching { server?.close() }
        disconnectActive()
    }
}

/** Blocks an IP for 5 minutes after 3 denied pairing requests. */
class PairingRateLimiter {
    private val denials = HashMap<String, MutableList<Long>>()

    @Synchronized
    fun recordDenial(ip: String) {
        denials.getOrPut(ip) { mutableListOf() }.add(System.currentTimeMillis())
    }

    @Synchronized
    fun isBlocked(ip: String): Boolean {
        val now = System.currentTimeMillis()
        val list = denials[ip] ?: return false
        list.removeAll { now - it > 5 * 60_000 }
        return list.size >= 3
    }
}
