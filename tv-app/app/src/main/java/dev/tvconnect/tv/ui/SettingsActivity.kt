package dev.tvconnect.tv.ui

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.os.Bundle
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import dev.tvconnect.tv.auth.TrustStore
import dev.tvconnect.tv.service.ConnectionService
import dev.tvconnect.tv.settings.Prefs
import java.text.DateFormat
import java.util.Date

class SettingsActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var trust: TrustStore
    private lateinit var content: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        trust = TrustStore(prefs)
        content = Ui.column(this)
        setContentView(ScrollView(this).apply { setBackgroundColor(Ui.BG); addView(content) })
        build()
    }

    private fun build() {
        content.removeAllViews()
        content.addView(Ui.text(this, "Настройки", 30f, bold = true))
        content.addView(Ui.button(this, "Удалённое управление: " + if (prefs.remoteEnabled) "ВКЛ" else "ВЫКЛ") {
            prefs.remoteEnabled = !prefs.remoteEnabled
            if (!prefs.remoteEnabled) ConnectionService.send(this, ConnectionService.ACTION_DISCONNECT)
            build()
        })
        content.addView(Ui.button(this, "Автозапуск при включении ТВ: " + if (prefs.autostart) "ВКЛ" else "ВЫКЛ") {
            prefs.autostart = !prefs.autostart
            build()
        })

        content.addView(Ui.text(this, "Доверенные компьютеры", 24f, bold = true))
        val list = trust.list()
        if (list.isEmpty()) content.addView(Ui.text(this, "Нет доверенных компьютеров", 18f, color = 0xFF8A949E.toInt()))
        val fmt = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        for (c in list) {
            content.addView(Ui.button(this, "✕  ${c.name}   (последний вход ${fmt.format(Date(c.lastSeen))})") {
                AlertDialog.Builder(this)
                    .setTitle("Удалить «${c.name}»?")
                    .setMessage("Компьютеру придётся снова запросить разрешение на подключение.")
                    .setPositiveButton("Удалить") { _, _ -> trust.remove(c.clientId); build() }
                    .setNegativeButton("Отмена", null)
                    .show()
            })
        }
        if (list.isNotEmpty()) {
            content.addView(Ui.button(this, "Удалить все доверенные") {
                trust.clear()
                ConnectionService.send(this, ConnectionService.ACTION_DISCONNECT)
                build()
            })
        }
        content.addView(Ui.text(this, "Проверка ввода и буфера обмена", 24f, bold = true))
        content.addView(EditText(this).apply {
            hint = "Выберите поле и нажмите Ctrl+V на компьютере"
            textSize = 18f
            setTextColor(Color.WHITE)
            setHintTextColor(0xFF8A949E.toInt())
            setSingleLine(false)
            minLines = 2
            contentDescription = "tvconnect_test_field"
        })
        content.addView(Ui.text(this, "ID телевизора: ${prefs.tvId}", 14f, color = 0xFF8A949E.toInt()))
        content.getChildAt(1)?.requestFocus()
    }
}
