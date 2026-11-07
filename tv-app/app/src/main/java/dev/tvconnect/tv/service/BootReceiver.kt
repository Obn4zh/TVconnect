package dev.tvconnect.tv.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import dev.tvconnect.tv.settings.Prefs
import dev.tvconnect.tv.util.Logger

/** Starts the listener after boot / app update when autostart is enabled. Screen capture still needs consent. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED && action != Intent.ACTION_MY_PACKAGE_REPLACED &&
            action != "android.intent.action.QUICKBOOT_POWERON"
        ) return
        if (!Prefs(ctx).autostart) {
            Logger.i("Boot", "autostart disabled, ignoring $action")
            return
        }
        Logger.i("Boot", "autostart on $action")
        ConnectionService.start(ctx)
    }
}
