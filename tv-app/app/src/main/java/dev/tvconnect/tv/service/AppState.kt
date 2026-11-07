package dev.tvconnect.tv.service

import android.os.Handler
import android.os.Looper
import java.util.concurrent.CopyOnWriteArrayList

/** Process-wide observable state shown by the UI. Listeners are always invoked on the main thread. */
object AppState {
    data class Snapshot(
        val listening: Boolean = false,
        val status: String = "Служба не запущена",
        val clientName: String? = null,
        val clientIp: String? = null,
        val streaming: Boolean = false,
        val videoInfo: String? = null,
        val lastError: String? = null,
    )

    @Volatile var snapshot = Snapshot()
        private set

    private val main = Handler(Looper.getMainLooper())
    private val listeners = CopyOnWriteArrayList<(Snapshot) -> Unit>()
    private val capsListeners = CopyOnWriteArrayList<() -> Unit>()

    fun update(f: (Snapshot) -> Snapshot) {
        synchronized(this) { snapshot = f(snapshot) }
        val s = snapshot
        main.post { listeners.forEach { it(s) } }
    }

    fun observe(l: (Snapshot) -> Unit) {
        listeners.add(l)
        main.post { l(snapshot) }
    }

    fun remove(l: (Snapshot) -> Unit) {
        listeners.remove(l)
    }

    /** Fired when the accessibility (input) service connects or disconnects. Called from any thread. */
    fun onCapsChanged(l: () -> Unit) = capsListeners.add(l)
    fun removeCapsListener(l: () -> Unit) = capsListeners.remove(l)
    fun capsChanged() = capsListeners.forEach { it() }
}
