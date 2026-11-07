package dev.tvconnect.tv.ui

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import dev.tvconnect.tv.discovery.localIpv4Addresses
import dev.tvconnect.tv.input.RemoteInputService
import dev.tvconnect.tv.net.Proto
import dev.tvconnect.tv.service.AppState
import dev.tvconnect.tv.service.ConnectionService
import dev.tvconnect.tv.settings.Prefs

class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var tvName: TextView
    private lateinit var tvIp: TextView
    private lateinit var tvStatus: TextView
    private lateinit var tvClient: TextView
    private lateinit var tvA11y: TextView
    private lateinit var tvVideo: TextView
    private lateinit var btnDisconnect: Button
    private val listener: (AppState.Snapshot) -> Unit = { render(it) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val root = Ui.column(this)
        root.addView(Ui.text(this, "TVconnect", 34f, bold = true))
        tvName = Ui.text(this, "", 22f).also(root::addView)
        tvIp = Ui.text(this, "", 20f).also(root::addView)
        tvStatus = Ui.text(this, "", 24f, bold = true, color = 0xFF7FD17F.toInt()).also(root::addView)
        tvClient = Ui.text(this, "", 20f).also(root::addView)
        tvVideo = Ui.text(this, "", 18f, color = 0xFFB0B8C0.toInt()).also(root::addView)
        tvA11y = Ui.text(this, "", 18f, color = 0xFFFFC56B.toInt()).also(root::addView)

        val buttons = Ui.row(this)
        btnDisconnect = Ui.button(this, "Отключить клиента") {
            ConnectionService.send(this, ConnectionService.ACTION_DISCONNECT)
        }.also(buttons::addView)
        Ui.button(this, "Управление (спец. возможности)") { openAccessibility() }.also(buttons::addView)
        Ui.button(this, "Настройки") { startActivity(Intent(this, SettingsActivity::class.java)) }
            .also(buttons::addView)
        root.addView(buttons)
        root.addView(
            Ui.text(
                this,
                "Подключитесь из TVconnect Desktop на компьютере в той же сети. " +
                    "Для управления мышью и текстом включите службу «TVconnect» в Спец. возможностях.",
                16f, color = 0xFF8A949E.toInt(),
            )
        )
        setContentView(root)
        ConnectionService.start(this)
    }

    override fun onResume() {
        super.onResume()
        AppState.observe(listener)
    }

    override fun onPause() {
        AppState.remove(listener)
        super.onPause()
    }

    private fun render(s: AppState.Snapshot) {
        tvName.text = "Устройство: ${prefs.deviceName}"
        val ips = localIpv4Addresses()
        tvIp.text = if (ips.isEmpty()) "IP-адрес: нет сети" else "IP-адрес: ${ips.joinToString(", ")}  (порт ${Proto.PORT})"
        tvStatus.text = if (!prefs.remoteEnabled) "Удалённое управление выключено" else s.status
        tvClient.text = s.clientName?.let { "Клиент: $it (${s.clientIp})" } ?: "Клиент: —"
        tvVideo.text = s.videoInfo?.let { "Видео: $it" } ?: ""
        tvA11y.text = if (RemoteInputService.instance != null) "Служба управления: включена"
        else "Служба управления выключена — доступны только видео и громкость (или ADB с компьютера)"
        btnDisconnect.isEnabled = s.clientName != null
    }

    private fun openAccessibility() {
        try {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        } catch (e: Exception) {
            AlertDialog.Builder(this)
                .setTitle("Настройки спец. возможностей недоступны")
                .setMessage(
                    "На этом ТВ нельзя открыть экран специальных возможностей. Включите службу с компьютера:\n\n" +
                        "adb shell settings put secure enabled_accessibility_services " +
                        "dev.tvconnect.tv/dev.tvconnect.tv.input.RemoteInputService\n" +
                        "adb shell settings put secure accessibility_enabled 1"
                )
                .setPositiveButton("OK", null)
                .show()
        }
    }
}
