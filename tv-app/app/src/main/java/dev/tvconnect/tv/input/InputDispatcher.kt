package dev.tvconnect.tv.input

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.media.AudioManager
import android.os.Build
import android.view.KeyEvent
import android.view.View
import dev.tvconnect.tv.capture.ScreenInfo
import org.json.JSONObject

class InputResult(val ok: Boolean, val reason: String? = null) {
    companion object {
        val OK = InputResult(true)
        val NO_A11Y = InputResult(false, "accessibility_disabled")
        val UNSUPPORTED = InputResult(false, "unsupported")
        val NO_FOCUS = InputResult(false, "no_focus")
        val FAILED = InputResult(false, "failed")
    }
}

/** Maps protocol input messages (docs/PROTOCOL.md §4) to Android actions. */
class InputDispatcher(private val ctx: Context) {
    private val audio = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    @Volatile private var screen = ScreenInfo.read(ctx)

    fun refreshScreen() {
        screen = ScreenInfo.read(ctx)
    }

    private fun px(o: JSONObject, kx: String, ky: String): Pair<Float, Float> {
        val x = o.optDouble(kx, 0.5).coerceIn(0.0, 1.0)
        val y = o.optDouble(ky, 0.5).coerceIn(0.0, 1.0)
        return (x * (screen.width - 1)).toFloat() to (y * (screen.height - 1)).toFloat()
    }

    fun handle(o: JSONObject): InputResult {
        val t = o.optString("t")
        if (t == "key") return key(o.optInt("code", -1))
        val svc = RemoteInputService.instance ?: return InputResult.NO_A11Y
        return when (t) {
            "tap" -> px(o, "x", "y").let { (x, y) -> svc.tap(x, y) }.asResult()
            "long_press" -> px(o, "x", "y").let { (x, y) -> svc.longPress(x, y) }.asResult()
            "swipe" -> {
                val (x1, y1) = px(o, "x1", "y1")
                val (x2, y2) = px(o, "x2", "y2")
                svc.swipe(x1, y1, x2, y2, o.optLong("ms", 300)).asResult()
            }
            "scroll" -> {
                val (x, y) = px(o, "x", "y")
                val dy = o.optInt("dy", 0).coerceIn(-5, 5)
                if (dy == 0) return InputResult.OK
                val dist = (screen.height * 0.2f * dy).coerceIn(-screen.height * 0.6f, screen.height * 0.6f)
                val y1 = (y - dist / 2).coerceIn(screen.height * 0.05f, screen.height * 0.95f)
                val y2 = (y + dist / 2).coerceIn(screen.height * 0.05f, screen.height * 0.95f)
                svc.swipe(x, y1, x, y2, 250).asResult()
            }
            "text" -> {
                val s = o.optString("s").take(1000)
                when (svc.insertText(s)) { null -> InputResult.NO_FOCUS; true -> InputResult.OK; false -> InputResult.FAILED }
            }
            "cursor" -> {
                px(o, "x", "y").let { (x, y) -> svc.showCursor(x, y) }
                InputResult.OK
            }
            else -> InputResult.UNSUPPORTED
        }
    }

    private fun Boolean.asResult() = if (this) InputResult.OK else InputResult.FAILED

    private fun key(code: Int): InputResult {
        when (code) {
            KeyEvent.KEYCODE_VOLUME_UP -> return volume(AudioManager.ADJUST_RAISE)
            KeyEvent.KEYCODE_VOLUME_DOWN -> return volume(AudioManager.ADJUST_LOWER)
            KeyEvent.KEYCODE_VOLUME_MUTE -> return volume(AudioManager.ADJUST_TOGGLE_MUTE)
        }
        val svc = RemoteInputService.instance ?: return InputResult.NO_A11Y
        val global = when (code) {
            KeyEvent.KEYCODE_BACK, KeyEvent.KEYCODE_ESCAPE -> AccessibilityService.GLOBAL_ACTION_BACK
            KeyEvent.KEYCODE_HOME -> AccessibilityService.GLOBAL_ACTION_HOME
            KeyEvent.KEYCODE_APP_SWITCH -> AccessibilityService.GLOBAL_ACTION_RECENTS
            KeyEvent.KEYCODE_NOTIFICATION -> AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS
            else -> null
        }
        if (global != null) return svc.global(global).asResult()

        if (code == KeyEvent.KEYCODE_DEL) {
            return when (svc.deleteLastChar()) { null -> InputResult.NO_FOCUS; true -> InputResult.OK; false -> InputResult.FAILED }
        }

        if (Build.VERSION.SDK_INT >= 33) {
            val a = when (code) {
                KeyEvent.KEYCODE_DPAD_UP -> AccessibilityService.GLOBAL_ACTION_DPAD_UP
                KeyEvent.KEYCODE_DPAD_DOWN -> AccessibilityService.GLOBAL_ACTION_DPAD_DOWN
                KeyEvent.KEYCODE_DPAD_LEFT -> AccessibilityService.GLOBAL_ACTION_DPAD_LEFT
                KeyEvent.KEYCODE_DPAD_RIGHT -> AccessibilityService.GLOBAL_ACTION_DPAD_RIGHT
                KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER ->
                    AccessibilityService.GLOBAL_ACTION_DPAD_CENTER
                else -> null
            }
            if (a != null) return svc.global(a).asResult()
        } else {
            val dir = when (code) {
                KeyEvent.KEYCODE_DPAD_UP -> View.FOCUS_UP
                KeyEvent.KEYCODE_DPAD_DOWN -> View.FOCUS_DOWN
                KeyEvent.KEYCODE_DPAD_LEFT -> View.FOCUS_LEFT
                KeyEvent.KEYCODE_DPAD_RIGHT -> View.FOCUS_RIGHT
                else -> null
            }
            if (dir != null) return svc.moveFocus(dir).asResult()
            if (code == KeyEvent.KEYCODE_DPAD_CENTER || code == KeyEvent.KEYCODE_ENTER || code == KeyEvent.KEYCODE_NUMPAD_ENTER) {
                return svc.clickFocused().asResult()
            }
        }
        // MENU, TAB, F-keys etc. cannot be injected by a normal app: use ADB Mode.
        return InputResult.UNSUPPORTED
    }

    private fun volume(direction: Int): InputResult {
        audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, direction, AudioManager.FLAG_SHOW_UI)
        return InputResult.OK
    }
}
