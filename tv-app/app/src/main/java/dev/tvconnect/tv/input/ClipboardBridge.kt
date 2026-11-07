package dev.tvconnect.tv.input

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import dev.tvconnect.tv.util.Logger
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Shared clipboard, TV side.
 *  PC -> TV: the text is put into the system clipboard (so the TV's own "Paste" works) and, when asked,
 *            inserted at the cursor of the focused field via the accessibility service.
 *  TV -> PC: Android 10+ forbids background apps from *reading* the clipboard, so we try it (works while
 *            TVconnect is in front) and otherwise return the selection / text of the focused element.
 */
class ClipboardBridge(ctx: Context) {
    companion object {
        const val MAX_CHARS = 65_536
    }

    private val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    private val main = Handler(Looper.getMainLooper())

    private fun <T> onMain(block: () -> T): T? {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        var result: T? = null
        val latch = CountDownLatch(1)
        main.post {
            try { result = block() } catch (e: Exception) { Logger.w("Clipboard", "main-thread op failed: ${e.message}") }
            finally { latch.countDown() }
        }
        latch.await(2, TimeUnit.SECONDS)
        return result
    }

    class SetResult(val clipboard: Boolean, val pasted: Boolean?, val reason: String?)

    fun set(text: String, paste: Boolean): SetResult {
        val t = text.take(MAX_CHARS)
        val stored = onMain {
            cm.setPrimaryClip(ClipData.newPlainText("TVconnect", t))
            true
        } ?: false
        if (!paste) return SetResult(stored, null, null)
        val svc = RemoteInputService.instance ?: return SetResult(stored, false, "accessibility_disabled")
        return when (svc.insertText(t, clipboardHoldsText = stored)) {
            null -> SetResult(stored, false, "no_focus")
            true -> SetResult(stored, true, null)
            false -> SetResult(stored, false, "failed")
        }
    }

    class GetResult(val text: String?, val source: String)

    fun get(): GetResult {
        // Readable only while our app is in the foreground (Android 10+); otherwise returns null.
        val clip = onMain { cm.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.text?.toString() }
        val focused = RemoteInputService.instance?.focusedText()
        return when {
            !focused.isNullOrEmpty() -> GetResult(focused.take(MAX_CHARS), "focused")
            !clip.isNullOrEmpty() -> GetResult(clip.take(MAX_CHARS), "clipboard")
            else -> GetResult(null, "none")
        }
    }
}
