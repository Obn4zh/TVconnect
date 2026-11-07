package dev.tvconnect.tv.service

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import dev.tvconnect.tv.R
import dev.tvconnect.tv.auth.TrustStore
import dev.tvconnect.tv.discovery.NsdAdvertiser
import dev.tvconnect.tv.discovery.UdpResponder
import dev.tvconnect.tv.net.PairingRateLimiter
import dev.tvconnect.tv.net.Server
import dev.tvconnect.tv.settings.Prefs
import dev.tvconnect.tv.ui.MainActivity
import dev.tvconnect.tv.util.Logger
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Foreground service that owns the TCP server and discovery. Screen capture is attached to it on demand
 * (foreground type mediaProjection) after the user accepts the system consent dialog.
 */
class ConnectionService : Service() {
    companion object {
        private const val NOTIF_ID = 1
        private const val CHANNEL = "tvconnect"
        const val ACTION_DISCONNECT = "dev.tvconnect.tv.DISCONNECT"
        const val ACTION_STOP = "dev.tvconnect.tv.STOP"

        @Volatile var instance: ConnectionService? = null
            private set

        fun start(ctx: Context) {
            val i = Intent(ctx, ConnectionService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
            } catch (e: Exception) {
                Logger.e("Service", "cannot start service", e)
                AppState.update { it.copy(status = "Не удалось запустить службу", lastError = e.message) }
            }
        }

        fun send(ctx: Context, action: String) {
            ctx.startService(Intent(ctx, ConnectionService::class.java).setAction(action))
        }
    }

    lateinit var prefs: Prefs
        private set
    lateinit var trust: TrustStore
        private set
    val limiter = PairingRateLimiter()

    private val main = Handler(Looper.getMainLooper())
    private lateinit var server: Server
    private lateinit var nsd: NsdAdvertiser
    private lateinit var udp: UdpResponder
    private var multicastLock: WifiManager.MulticastLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        trust = TrustStore(prefs)
        createChannel()
        startForegroundCompat(projection = false)
        instance = this
        Logger.i("Service", "starting, sdk ${Build.VERSION.SDK_INT}, ${Build.MANUFACTURER} ${Build.MODEL}")
        multicastLock = (applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager)
            ?.createMulticastLock("tvconnect-discovery")?.apply {
                setReferenceCounted(false)
                runCatching { acquire() }
            }
        server = Server(this).also { it.start() }
        nsd = NsdAdvertiser(this, prefs).also { it.start() }
        udp = UdpResponder(prefs).also { it.start() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_DISCONNECT -> server.disconnectActive()
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        Logger.i("Service", "stopping")
        instance = null
        server.stop()
        nsd.stop()
        udp.stop()
        runCatching { multicastLock?.release() }
        AppState.update { it.copy(listening = false, status = "Служба остановлена") }
        super.onDestroy()
    }

    fun onListening(listening: Boolean) {
        AppState.update {
            it.copy(listening = listening, status = if (listening) (if (it.clientName != null) it.status else "Ожидание подключения") else "Сервер недоступен")
        }
    }

    fun activeSession() = server.active

    // ---- foreground / notification ----

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "TVconnect", NotificationManager.IMPORTANCE_LOW))
        }
    }

    private fun notification(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else @Suppress("DEPRECATION") Notification.Builder(this)
        return b.setContentTitle("TVconnect")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    private fun startForegroundCompat(projection: Boolean) {
        val n = notification(if (projection) "Идёт трансляция экрана" else "Ожидание подключения в локальной сети")
        try {
            when {
                Build.VERSION.SDK_INT >= 34 -> startForeground(
                    NOTIF_ID, n,
                    if (projection) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION else ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
                )
                Build.VERSION.SDK_INT >= 29 -> startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
                else -> startForeground(NOTIF_ID, n)
            }
        } catch (e: Exception) {
            Logger.e("Service", "startForeground failed (projection=$projection)", e)
        }
    }

    private fun onMainBlocking(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        val l = CountDownLatch(1)
        main.post { try { block() } finally { l.countDown() } }
        l.await(5, TimeUnit.SECONDS)
    }

    // ---- screen capture ----

    /**
     * Asks the user for screen-capture consent (system dialog) and returns a MediaProjection.
     * Blocks the calling (non-main) thread. [onFail] receives null if the user denied, or an error text.
     */
    fun obtainProjection(onFail: (String?) -> Unit): MediaProjection? {
        val r = ProjectionBroker.request(this, 90_000)
        if (r.resultCode != Activity.RESULT_OK || r.data == null) {
            Logger.w("Service", "projection not granted: ${r.error ?: "denied by user"}")
            onFail(r.error)
            return null
        }
        if (Build.VERSION.SDK_INT >= 34) onMainBlocking { startForegroundCompat(projection = true) }
        val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val mp = try {
            mpm.getMediaProjection(r.resultCode, r.data)
        } catch (e: Exception) {
            Logger.e("Service", "getMediaProjection failed", e)
            onFail(e.message ?: "getMediaProjection failed")
            return null
        }
        if (mp == null) {
            onFail("MediaProjection unavailable")
            return null
        }
        mp.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                Logger.i("Service", "media projection stopped")
            }
        }, main)
        Logger.i("Service", "media projection granted")
        return mp
    }

    fun onProjectionReleased() {
        if (Build.VERSION.SDK_INT >= 34) main.post { startForegroundCompat(projection = false) }
    }
}
