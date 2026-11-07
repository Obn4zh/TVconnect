package dev.tvconnect.tv.settings

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.provider.Settings
import java.util.UUID

class Prefs(ctx: Context) {
    private val app = ctx.applicationContext
    val sp: SharedPreferences = app.getSharedPreferences("tvconnect", Context.MODE_PRIVATE)

    var remoteEnabled: Boolean
        get() = sp.getBoolean("remote_enabled", true)
        set(v) = sp.edit().putBoolean("remote_enabled", v).apply()

    var autostart: Boolean
        get() = sp.getBoolean("autostart", true)
        set(v) = sp.edit().putBoolean("autostart", v).apply()

    val tvId: String
        get() {
            sp.getString("tv_id", null)?.let { return it }
            val id = UUID.randomUUID().toString().replace("-", "")
            sp.edit().putString("tv_id", id).apply()
            return id
        }

    val deviceName: String
        get() = (Settings.Global.getString(app.contentResolver, "device_name")
            ?: Settings.Global.getString(app.contentResolver, Settings.Global.DEVICE_NAME)
            ?: Build.MODEL ?: "Android TV").take(64)
}
