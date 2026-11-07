package dev.tvconnect.tv.net

import android.os.Build
import android.util.Base64
import dev.tvconnect.tv.capture.ScreenInfo
import dev.tvconnect.tv.capture.ScreenStreamer
import dev.tvconnect.tv.input.ClipboardBridge
import dev.tvconnect.tv.input.InputDispatcher
import dev.tvconnect.tv.input.RemoteInputService
import dev.tvconnect.tv.service.AppState
import dev.tvconnect.tv.service.ConnectionService
import dev.tvconnect.tv.service.PairDecision
import dev.tvconnect.tv.service.PairingBroker
import dev.tvconnect.tv.service.ProjectionBroker
import dev.tvconnect.tv.auth.IdentityKey
import dev.tvconnect.tv.util.Logger
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.EOFException
import java.io.IOException
import java.net.Socket
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.security.interfaces.ECPublicKey
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** One desktop connection: handshake, pairing/auth, control loop, video and input. */
class Session(
    private val svc: ConnectionService,
    private val sock: Socket,
    private val onClosed: (Session) -> Unit,
) {
    private val tag = "Session"
    val remoteIp: String = sock.inetAddress.hostAddress ?: "?"
    private val io = FramedIO(
        DataInputStream(BufferedInputStream(sock.getInputStream(), 64 * 1024)),
        BufferedOutputStream(sock.getOutputStream(), 256 * 1024),
    )
    @Volatile private var closed = false
    @Volatile var clientName = "?"
        private set
    private var clientId = ""

    private val worker = Executors.newSingleThreadExecutor { Thread(it, "session-worker") }
    private val input = InputDispatcher(svc)
    private val clipboard = ClipboardBridge(svc)
    @Volatile private var streamer: ScreenStreamer? = null

    private val videoQueue = ArrayBlockingQueue<ByteArray>(8)
    @Volatile private var dropping = false
    private var senderThread: Thread? = null
    private var lastInputError = 0L

    private val capsListener: () -> Unit = { if (!closed) runCatching { sendControl(capsJson().put("type", "caps")) } }

    fun start() {
        Thread({ run() }, "session-$remoteIp").start()
    }

    private fun run() {
        var reason = "closed"
        try {
            sock.tcpNoDelay = true
            sock.sendBufferSize = 1 shl 20
            sock.soTimeout = 15_000
            handshake()
            AppState.onCapsChanged(capsListener)
            sock.soTimeout = 20_000
            loop()
        } catch (e: EOFException) {
            reason = "client disconnected"
        } catch (e: SocketTimeoutException) {
            reason = "timeout"
            Logger.w(tag, "client $remoteIp timed out")
        } catch (e: RejectException) {
            reason = e.message ?: "rejected"
        } catch (e: IOException) {
            reason = if (closed) "closed" else "io: ${e.message}"
            if (!closed) Logger.w(tag, "connection error: ${e.message}")
        } catch (e: Exception) {
            reason = "error: ${e.message}"
            Logger.e(tag, "session failed", e)
        } finally {
            Logger.i(tag, "session with $clientName ($remoteIp) ended: $reason")
            close()
        }
    }

    private class RejectException(msg: String) : Exception(msg)

    private fun json(r: Record): JSONObject {
        if (r.type != Proto.T_CONTROL) throw ProtocolException("expected control record")
        val o = try { JSONObject(String(r.payload, Charsets.UTF_8)) } catch (e: Exception) { throw ProtocolException("bad json") }
        if (!o.has("type")) throw ProtocolException("message without type")
        return o
    }

    private fun b64(s: String): ByteArray = try {
        Base64.decode(s, Base64.NO_WRAP)
    } catch (e: IllegalArgumentException) {
        throw ProtocolException("bad base64")
    }

    private fun enc(b: ByteArray) = Base64.encodeToString(b, Base64.NO_WRAP)

    fun sendControl(o: JSONObject) = io.write(Proto.T_CONTROL, o.toString().toByteArray(Charsets.UTF_8))

    private fun sendError(code: String, message: String) {
        runCatching { sendControl(JSONObject().put("type", "error").put("code", code).put("message", message)) }
    }

    private fun handshake() {
        val helloRec = io.read()
        val hello = json(helloRec)
        if (hello.getString("type") != "hello") throw ProtocolException("expected hello")
        if (hello.optInt("proto") != Proto.VERSION) {
            sendError("proto_mismatch", "TV speaks protocol ${Proto.VERSION}")
            throw RejectException("protocol mismatch")
        }
        clientId = hello.optString("client_id").filter { it.isLetterOrDigit() }.take(64)
        clientName = hello.optString("client_name", "PC").replace(Regex("[\\p{Cntrl}]"), "").trim().take(64).ifEmpty { "PC" }
        if (clientId.length < 16) throw ProtocolException("bad client_id")

        val eph = Crypto.generateEphemeral()
        val ephPub = eph.public as ECPublicKey
        val tvEphRaw = Crypto.encodePoint(ephPub)
        val idPub = IdentityKey.publicDer()
        val th = Crypto.transcriptHash(helloRec.payload, tvEphRaw, idPub)
        val prefs = svc.prefs
        io.write(
            Proto.T_CONTROL,
            JSONObject()
                .put("type", "hello_ack")
                .put("proto", Proto.VERSION)
                .put("tv_id", prefs.tvId)
                .put("tv_name", prefs.deviceName)
                .put("model", "${Build.MANUFACTURER} ${Build.MODEL}".take(64))
                .put("eph_pub", enc(tvEphRaw))
                .put("id_pub", enc(idPub))
                .put("sig", enc(IdentityKey.sign(th)))
                .toString().toByteArray(Charsets.UTF_8),
        )
        val peer = Crypto.decodePoint(b64(hello.getString("eph_pub")), ephPub.params)
        val keys = Crypto.deriveKeys(Crypto.ecdh(eph.private, peer), th)
        io.rx = RecordCipher(keys.d2t)
        io.tx = RecordCipher(keys.t2d)

        var msg = json(io.read())
        if (msg.getString("type") == "auth") {
            val ok = svc.trust.verify(clientId, b64(msg.optString("token")))
            sendControl(JSONObject().put("type", "auth_result").put("ok", ok).apply { if (!ok) put("reason", "unknown_client") })
            if (ok) {
                Logger.i(tag, "trusted client $clientName ($remoteIp) authenticated")
            } else {
                Logger.w(tag, "unknown token from $clientName ($remoteIp)")
                msg = json(io.read())
            }
            if (ok) {
                ready()
                return
            }
        }
        if (msg.getString("type") != "pair_request") throw ProtocolException("expected pair_request")
        if (svc.limiter.isBlocked(remoteIp)) {
            sendControl(JSONObject().put("type", "pair_result").put("accepted", false).put("reason", "rate_limited"))
            throw RejectException("rate limited")
        }
        AppState.update { it.copy(status = "Запрос подключения от $clientName") }
        Logger.i(tag, "pairing request from $clientName ($remoteIp), code ${keys.sas}")
        sock.soTimeout = 0 // the desktop waits silently while the user decides
        val decision = PairingBroker.request(svc, clientName, remoteIp, keys.sas, 60_000)
        sock.soTimeout = 15_000
        Logger.i(tag, "pairing decision: $decision")
        when (decision) {
            PairDecision.ALLOW_REMEMBER -> {
                val token = svc.trust.add(clientId, clientName)
                sendControl(JSONObject().put("type", "pair_result").put("accepted", true).put("token", enc(token)))
            }
            PairDecision.ALLOW_ONCE ->
                sendControl(JSONObject().put("type", "pair_result").put("accepted", true).put("token", JSONObject.NULL))
            PairDecision.DENY, PairDecision.TIMEOUT -> {
                if (decision == PairDecision.DENY) svc.limiter.recordDenial(remoteIp)
                sendControl(JSONObject().put("type", "pair_result").put("accepted", false)
                    .put("reason", if (decision == PairDecision.DENY) "denied" else "timeout"))
                throw RejectException("pairing ${decision.name.lowercase()}")
            }
        }
        ready()
    }

    private fun capsJson(): JSONObject = JSONObject()
        .put("accessibility", RemoteInputService.instance != null)
        .put("dpad_native", Build.VERSION.SDK_INT >= 33)
        .put("dpad_fallback", true)
        .put("text_native", true)
        .put("clipboard", true)

    private fun ready() {
        val s = ScreenInfo.read(svc)
        sendControl(
            JSONObject().put("type", "session_ready")
                .put("screen", JSONObject().put("w", s.width).put("h", s.height))
                .put("sdk", Build.VERSION.SDK_INT)
                .put("caps", capsJson()),
        )
        AppState.update { it.copy(status = "Подключён", clientName = clientName, clientIp = remoteIp) }
        RemoteInputService.onGestureCancelled = {
            if (!closed) runCatching {
                sendControl(JSONObject().put("type", "input_result").put("t", "gesture").put("ok", false).put("reason", "gesture_cancelled"))
            }
        }
        senderThread = Thread({ sendLoop() }, "video-sender").apply { start() }
    }

    private fun loop() {
        while (!closed) {
            val r = io.read()
            when (r.type) {
                Proto.T_INPUT -> handleInput(r.payload)
                Proto.T_CONTROL -> handleControl(json(r))
                else -> Logger.d(tag, "ignoring record type ${r.type}")
            }
        }
    }

    private fun handleInput(payload: ByteArray) {
        val o = try { JSONObject(String(payload, Charsets.UTF_8)) } catch (e: Exception) { return }
        val res = try {
            input.handle(o)
        } catch (e: Exception) {
            Logger.w(tag, "input failed: ${e.message}")
            null
        }
        if (res != null && !res.ok) {
            val now = System.currentTimeMillis()
            if (now - lastInputError > 2000) {
                lastInputError = now
                runCatching { sendControl(JSONObject().put("type", "input_result").put("t", o.optString("t")).put("ok", false).put("reason", res.reason)) }
            }
        }
    }

    private fun handleControl(o: JSONObject) {
        when (o.getString("type")) {
            "ping" -> sendControl(JSONObject().put("type", "pong").put("t", o.opt("t")))
            "start_video" -> {
                val maxW = o.optInt("max_w", 1280).coerceIn(320, 3840)
                val maxH = o.optInt("max_h", 720).coerceIn(180, 2160)
                val fps = o.optInt("fps", 30).coerceIn(5, 60)
                val br = o.optInt("bitrate", 6_000_000).coerceIn(500_000, 40_000_000)
                worker.execute { startVideo(maxW, maxH, fps, br) }
            }
            "stop_video" -> worker.execute {
                streamer?.stopEncoder()
                status("stopped")
            }
            "request_keyframe" -> streamer?.requestKeyframe()
            "clipboard_set" -> {
                val text = o.optString("text")
                val paste = o.optBoolean("paste", false)
                worker.execute {
                    val r = clipboard.set(text, paste)
                    Logger.i(tag, "clipboard_set ${text.length} chars, paste=$paste -> stored=${r.clipboard} pasted=${r.pasted} ${r.reason ?: ""}")
                    runCatching {
                        sendControl(JSONObject().put("type", "clipboard_result").put("clipboard", r.clipboard)
                            .put("paste", paste).put("pasted", r.pasted ?: JSONObject.NULL).put("reason", r.reason ?: JSONObject.NULL))
                    }
                }
            }
            "clipboard_get" -> worker.execute {
                val r = clipboard.get()
                Logger.i(tag, "clipboard_get -> ${r.source}, ${r.text?.length ?: 0} chars")
                runCatching {
                    sendControl(JSONObject().put("type", "clipboard").put("text", r.text ?: JSONObject.NULL).put("source", r.source))
                }
            }
            "bye" -> {
                Logger.i(tag, "client said bye: ${o.optString("reason")}")
                close()
            }
            else -> Logger.d(tag, "unknown control ${o.optString("type")}")
        }
    }

    private fun status(state: String, extra: JSONObject.() -> Unit = {}) {
        runCatching { sendControl(JSONObject().put("type", "video_status").put("state", state).apply(extra)) }
    }

    private fun startVideo(maxW: Int, maxH: Int, fps: Int, bitrate: Int) {
        if (closed) return
        try {
            var s = streamer
            if (s == null) {
                status("consent_pending")
                AppState.update { it.copy(status = "Ожидание разрешения на захват экрана") }
                val proj = svc.obtainProjection { error ->
                    status(if (error == null) "denied" else "error") { error?.let { put("message", it) } }
                }
                if (proj == null || closed) {
                    proj?.stop()
                    AppState.update { it.copy(status = "Подключён (без видео)") }
                    return
                }
                s = ScreenStreamer(svc, proj, ::onEncoded)
                streamer = s
            }
            input.refreshScreen()
            videoQueue.clear()
            val p = s.start(maxW, maxH, fps, bitrate)
            status("running") { put("w", p.width).put("h", p.height).put("fps", p.fps) }
            AppState.update { it.copy(status = "Трансляция экрана", streaming = true, videoInfo = "${p.width}×${p.height} @ ${p.fps} FPS") }
        } catch (e: Exception) {
            Logger.e(tag, "cannot start video", e)
            status("error") { put("message", e.message ?: e.javaClass.simpleName) }
        }
    }

    /** Encoder thread: never blocks on the network. Drops to the next keyframe when the queue is full. */
    private fun onEncoded(flags: Int, ptsUs: Long, data: ByteArray) {
        if (closed) return
        val isKey = flags and Proto.VF_KEY != 0
        val isConfig = flags and Proto.VF_CONFIG != 0
        if (dropping && !isKey && !isConfig) return
        val rec = ByteBuffer.allocate(9 + data.size).put(flags.toByte()).putLong(ptsUs).put(data).array()
        if (isKey) dropping = false
        if (!videoQueue.offer(rec)) {
            videoQueue.clear()
            if (isConfig || isKey) {
                videoQueue.offer(rec)
            } else {
                dropping = true
                streamer?.requestKeyframe()
            }
        }
    }

    private fun sendLoop() {
        try {
            while (!closed) {
                val rec = videoQueue.poll(500, TimeUnit.MILLISECONDS) ?: continue
                io.write(Proto.T_VIDEO, rec)
            }
        } catch (e: InterruptedException) {
            // closing
        } catch (e: IOException) {
            if (!closed) Logger.w(tag, "video send failed: ${e.message}")
            close()
        }
    }

    /** Disconnects the client (from UI or on error). Safe to call multiple times from any thread. */
    fun close(reason: String = "closed") {
        synchronized(this) {
            if (closed) return
            closed = true
        }
        AppState.removeCapsListener(capsListener)
        RemoteInputService.onGestureCancelled = null
        PairingBroker.cancelAll()
        if (streamer == null) ProjectionBroker.cancel()
        runCatching { if (io.tx != null) sendControl(JSONObject().put("type", "bye").put("reason", reason)) }
        runCatching { sock.close() }
        senderThread?.interrupt()
        worker.execute {
            val hadStreamer = streamer != null
            streamer?.release()
            streamer = null
            if (hadStreamer) svc.onProjectionReleased()
        }
        worker.shutdown()
        AppState.update { it.copy(status = "Ожидание подключения", clientName = null, clientIp = null, streaming = false, videoInfo = null) }
        onClosed(this)
    }
}
