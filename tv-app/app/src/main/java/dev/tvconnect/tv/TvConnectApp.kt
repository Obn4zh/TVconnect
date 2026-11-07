package dev.tvconnect.tv

import android.app.Application
import dev.tvconnect.tv.util.Logger

class TvConnectApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Logger.init(this)
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            Logger.e("Crash", "uncaught exception in ${t.name}", e)
            Thread.sleep(300) // let the log writer flush
            previous?.uncaughtException(t, e)
        }
    }
}
