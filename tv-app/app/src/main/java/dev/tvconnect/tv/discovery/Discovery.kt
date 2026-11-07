package dev.tvconnect.tv.discovery

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import dev.tvconnect.tv.net.Proto
import dev.tvconnect.tv.settings.Prefs
import dev.tvconnect.tv.util.Logger
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface

/** mDNS/DNS-SD advertisement via NsdManager. */
class NsdAdvertiser(private val ctx: Context, private val prefs: Prefs) {
    private val nsd = ctx.getSystemService(Context.NSD_SERVICE) as NsdManager
    private var listener: NsdManager.RegistrationListener? = null

    fun start() {
        val info = NsdServiceInfo().apply {
            serviceName = prefs.deviceName
            serviceType = Proto.SERVICE_TYPE
            port = Proto.PORT
            setAttribute("id", prefs.tvId)
            setAttribute("name", prefs.deviceName)
            setAttribute("model", Build.MODEL.take(40))
            setAttribute("proto", Proto.VERSION.toString())
        }
        val l = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(si: NsdServiceInfo) = Logger.i("NSD", "registered ${si.serviceName}")
            override fun onRegistrationFailed(si: NsdServiceInfo, err: Int) = Logger.w("NSD", "registration failed: $err")
            override fun onServiceUnregistered(si: NsdServiceInfo) = Logger.i("NSD", "unregistered")
            override fun onUnregistrationFailed(si: NsdServiceInfo, err: Int) = Logger.w("NSD", "unregistration failed: $err")
        }
        try {
            nsd.registerService(info, NsdManager.PROTOCOL_DNS_SD, l)
            listener = l
        } catch (e: Exception) {
            Logger.e("NSD", "cannot register", e)
        }
    }

    fun stop() {
        listener?.let { runCatching { nsd.unregisterService(it) } }
        listener = null
    }
}

/** Answers UDP broadcast probes (fallback when multicast is filtered by the router). */
class UdpResponder(private val prefs: Prefs) {
    @Volatile private var socket: DatagramSocket? = null
    @Volatile private var running = false

    fun start() {
        running = true
        Thread({ loop() }, "udp-discovery").start()
    }

    private fun loop() {
        while (running) {
            try {
                DatagramSocket(null).use { s ->
                    s.reuseAddress = true
                    s.broadcast = true
                    s.bind(InetSocketAddress(Proto.DISCOVERY_PORT))
                    socket = s
                    Logger.i("UDP", "discovery responder on ${Proto.DISCOVERY_PORT}")
                    val buf = ByteArray(512)
                    while (running) {
                        val p = DatagramPacket(buf, buf.size)
                        s.receive(p)
                        val text = String(p.data, 0, p.length, Charsets.US_ASCII)
                        if (!text.startsWith(Proto.DISCOVER_MAGIC)) continue
                        val reply = JSONObject()
                            .put("type", "tvc_announce")
                            .put("proto", Proto.VERSION)
                            .put("tv_id", prefs.tvId)
                            .put("name", prefs.deviceName)
                            .put("model", Build.MODEL.take(40))
                            .put("port", Proto.PORT)
                            .toString().toByteArray()
                        s.send(DatagramPacket(reply, reply.size, p.socketAddress))
                    }
                }
            } catch (e: Exception) {
                if (!running) break
                Logger.w("UDP", "responder error: ${e.message}, restarting")
                Thread.sleep(3000)
            }
        }
    }

    fun stop() {
        running = false
        runCatching { socket?.close() }
    }
}

fun localIpv4Addresses(): List<String> = try {
    NetworkInterface.getNetworkInterfaces().toList()
        .filter { it.isUp && !it.isLoopback }
        .flatMap { ni -> ni.inetAddresses.toList().filterIsInstance<Inet4Address>().map { it.hostAddress ?: "" } }
        .filter { it.isNotEmpty() }
} catch (e: Exception) {
    emptyList()
}
