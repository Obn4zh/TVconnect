package dev.tvconnect.tv.ui

import android.app.Activity
import android.os.Bundle
import android.view.WindowManager
import dev.tvconnect.tv.service.PairDecision
import dev.tvconnect.tv.service.PairingBroker

/** Full-screen confirmation for a first-time connection. Default focus is on "Deny". */
class PairingActivity : Activity() {
    companion object { const val EXTRA_ID = "id" }

    private var reqId = -1
    private var decided = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        val req = PairingBroker.current
        reqId = intent.getIntExtra(EXTRA_ID, -1)
        if (req == null || req.id != reqId) {
            finish()
            return
        }
        req.onFinished = { runOnUiThread { if (!isFinishing) finish() } }

        val root = Ui.column(this, 64)
        root.addView(Ui.text(this, "Запрос на подключение", 32f, bold = true))
        root.addView(Ui.text(this, "Компьютер «${req.clientName}» (${req.clientIp}) хочет управлять этим телевизором.", 22f))
        root.addView(Ui.text(this, "Код подтверждения — должен совпадать с кодом на компьютере:", 20f))
        root.addView(Ui.text(this, "${req.sas.substring(0, 3)} ${req.sas.substring(3)}", 56f, bold = true, color = 0xFF3D8BFF.toInt()))
        val row = Ui.row(this)
        row.addView(Ui.button(this, "Разрешить и запомнить") { decide(PairDecision.ALLOW_REMEMBER) })
        row.addView(Ui.button(this, "Разрешить один раз") { decide(PairDecision.ALLOW_ONCE) })
        val deny = Ui.button(this, "Отклонить") { decide(PairDecision.DENY) }
        row.addView(deny)
        root.addView(row)
        root.addView(Ui.text(this, "Если вы не узнаёте этот компьютер — нажмите «Отклонить».", 16f, color = 0xFF8A949E.toInt()))
        setContentView(root)
        deny.requestFocus()
    }

    private fun decide(d: PairDecision) {
        decided = true
        PairingBroker.resolve(reqId, d)
        finish()
    }

    override fun onDestroy() {
        if (!decided && isFinishing) PairingBroker.resolve(reqId, PairDecision.DENY)
        super.onDestroy()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        decide(PairDecision.DENY)
    }
}
