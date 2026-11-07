package dev.tvconnect.tv.util

import android.content.Context
import android.util.Log
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/** Logcat + rotating file log in filesDir/logs (pull with `adb shell run-as dev.tvconnect.tv cat files/logs/tvconnect.log`). */
object Logger {
    private const val TAG = "TVconnect"
    private const val MAX_BYTES = 1_000_000L
    private val io = Executors.newSingleThreadExecutor { r -> Thread(r, "log-writer").apply { isDaemon = true } }
    private val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)
    @Volatile private var file: File? = null

    fun init(ctx: Context) {
        val dir = File(ctx.filesDir, "logs").apply { mkdirs() }
        file = File(dir, "tvconnect.log")
        i("Logger", "log file ${file?.absolutePath}")
    }

    fun d(where: String, msg: String) = write(Log.DEBUG, where, msg, null)
    fun i(where: String, msg: String) = write(Log.INFO, where, msg, null)
    fun w(where: String, msg: String, t: Throwable? = null) = write(Log.WARN, where, msg, t)
    fun e(where: String, msg: String, t: Throwable? = null) = write(Log.ERROR, where, msg, t)

    private fun write(level: Int, where: String, msg: String, t: Throwable?) {
        val line = "[$where] $msg"
        when (level) {
            Log.DEBUG -> Log.d(TAG, line, t)
            Log.INFO -> Log.i(TAG, line, t)
            Log.WARN -> Log.w(TAG, line, t)
            else -> Log.e(TAG, line, t)
        }
        val f = file ?: return
        val levelChar = when (level) { Log.DEBUG -> 'D'; Log.INFO -> 'I'; Log.WARN -> 'W'; else -> 'E' }
        val stamp = synchronized(fmt) { fmt.format(Date()) }
        val thread = Thread.currentThread().name
        val text = buildString {
            append(stamp).append(' ').append(levelChar).append(" [").append(thread).append("] ").append(line).append('\n')
            if (t != null) append(Log.getStackTraceString(t)).append('\n')
        }
        io.execute {
            try {
                if (f.length() > MAX_BYTES) {
                    val old = File(f.parentFile, "tvconnect.log.1")
                    old.delete()
                    f.renameTo(old)
                }
                FileWriter(f, true).use { it.write(text) }
            } catch (_: Exception) {
                // Logging must never crash the app.
            }
        }
    }
}
