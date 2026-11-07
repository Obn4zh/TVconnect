package dev.tvconnect.tv.input

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import dev.tvconnect.tv.service.AppState
import dev.tvconnect.tv.util.Logger

/**
 * Input injection for Native Agent Mode. Android does not let ordinary apps inject KeyEvents, so this uses
 * what AccessibilityService allows: gestures, global actions (DPAD only on API 33+), focus/click on nodes
 * and ACTION_SET_TEXT. Anything else needs ADB Mode.
 */
class RemoteInputService : AccessibilityService() {
    companion object {
        @Volatile var instance: RemoteInputService? = null
            private set

        /** Notified when the system cancels an injected gesture (some TV firmwares do this always). */
        @Volatile var onGestureCancelled: (() -> Unit)? = null
    }

    private val main = Handler(Looper.getMainLooper())
    private var cursor: CursorView? = null
    private val hideCursor = Runnable { cursor?.visibility = View.GONE }

    override fun onServiceConnected() {
        instance = this
        Logger.i("Input", "accessibility service connected")
        AppState.capsChanged()
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        cleanup()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        cleanup()
        super.onDestroy()
    }

    private fun cleanup() {
        if (instance === this) instance = null
        main.post {
            cursor?.let { runCatching { (getSystemService(WINDOW_SERVICE) as WindowManager).removeView(it) } }
            cursor = null
        }
        Logger.i("Input", "accessibility service disconnected")
        AppState.capsChanged()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    // ---- gestures (coordinates in real display pixels) ----

    private fun dispatch(path: Path, durationMs: Long): Boolean {
        val g = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs.coerceIn(1, 5000)))
            .build()
        val accepted = dispatchGesture(g, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) = Logger.d("Input", "gesture completed")
            override fun onCancelled(gestureDescription: GestureDescription?) {
                Logger.w("Input", "gesture cancelled by system")
                onGestureCancelled?.invoke()
            }
        }, main)
        if (!accepted) Logger.w("Input", "dispatchGesture rejected")
        return accepted
    }

    fun tap(x: Float, y: Float) = dispatch(Path().apply { moveTo(x, y) }, 50)
    fun longPress(x: Float, y: Float) = dispatch(Path().apply { moveTo(x, y) }, 700)
    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, ms: Long) =
        dispatch(Path().apply { moveTo(x1, y1); lineTo(x2, y2) }, ms)

    fun global(action: Int) = performGlobalAction(action)

    // ---- focus / text ----

    fun focusedNode(): AccessibilityNodeInfo? =
        findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY)

    /** D-pad emulation for Android < 13: move input focus to the nearest focusable node in [direction]. */
    fun moveFocus(direction: Int): Boolean {
        val focused = findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (focused == null) {
            val first = rootInActiveWindow?.let { findFirstFocusable(it, 0) } ?: return false
            return first.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        }
        val next = focused.focusSearch(direction) ?: return false
        return next.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
    }

    private fun findFirstFocusable(n: AccessibilityNodeInfo, depth: Int): AccessibilityNodeInfo? {
        if (depth > 30) return null
        if (n.isFocusable && n.isVisibleToUser) return n
        for (i in 0 until n.childCount) {
            val c = n.getChild(i) ?: continue
            findFirstFocusable(c, depth + 1)?.let { return it }
        }
        return null
    }

    fun clickFocused(): Boolean {
        var n = focusedNode()
        var depth = 0
        while (n != null && depth < 8) {
            if (n.isClickable && n.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true
            n = n.parent
            depth++
        }
        return false
    }

    private fun editableFocus(): AccessibilityNodeInfo? =
        findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.takeIf { it.isEditable }

    private fun currentText(n: AccessibilityNodeInfo): String =
        if (Build.VERSION.SDK_INT >= 26 && n.isShowingHintText) "" else n.text?.toString() ?: ""

    private fun setText(n: AccessibilityNodeInfo, text: String, cursor: Int = text.length): Boolean {
        val ok = n.performAction(
            AccessibilityNodeInfo.ACTION_SET_TEXT,
            Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) },
        )
        if (ok) {
            val c = cursor.coerceIn(0, text.length)
            n.performAction(
                AccessibilityNodeInfo.ACTION_SET_SELECTION,
                Bundle().apply {
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, c)
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, c)
                },
            )
        }
        return ok
    }

    /** Current selection [lo, hi) in [cur]; collapsed at the end when the field reports none. */
    private fun selection(n: AccessibilityNodeInfo, cur: String): Pair<Int, Int> {
        val s = n.textSelectionStart
        val e = n.textSelectionEnd
        if (s < 0 || e < 0 || s > cur.length || e > cur.length) return cur.length to cur.length
        return minOf(s, e) to maxOf(s, e)
    }

    /**
     * Inserts [s] at the cursor of the focused editable field, replacing the selection.
     * Falls back to ACTION_PASTE (the TV clipboard must already hold [s]) if the field rejects SET_TEXT.
     * null = no editable field in focus.
     */
    fun insertText(s: String, clipboardHoldsText: Boolean = false): Boolean? {
        val n = editableFocus() ?: return null
        val cur = currentText(n)
        val (lo, hi) = selection(n, cur)
        if (setText(n, cur.substring(0, lo) + s + cur.substring(hi), lo + s.length)) return true
        return clipboardHoldsText && n.performAction(AccessibilityNodeInfo.ACTION_PASTE)
    }

    fun deleteLastChar(): Boolean? {
        val n = editableFocus() ?: return null
        val cur = currentText(n)
        val (lo, hi) = selection(n, cur)
        if (lo != hi) return setText(n, cur.substring(0, lo) + cur.substring(hi), lo)
        if (lo == 0) return true
        val start = cur.offsetByCodePoints(lo, -1)
        return setText(n, cur.substring(0, start) + cur.substring(lo), start)
    }

    /** Text for "copy from TV": the selection in the focused field, else the whole field / focused item text. */
    fun focusedText(): String? {
        val n = focusedNode() ?: return null
        val cur = if (n.isEditable) currentText(n) else (n.text ?: n.contentDescription)?.toString() ?: ""
        if (n.isEditable) {
            val (lo, hi) = selection(n, cur)
            if (lo != hi) return cur.substring(lo, hi)
        }
        return cur.ifEmpty { null }
    }

    // ---- cursor overlay ----

    fun showCursor(x: Float, y: Float) {
        main.post {
            val wm = getSystemService(WINDOW_SERVICE) as WindowManager
            var v = cursor
            if (v == null) {
                v = CursorView(this)
                val lp = WindowManager.LayoutParams(
                    CursorView.SIZE, CursorView.SIZE,
                    WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                        WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                    PixelFormat.TRANSLUCENT,
                ).apply { gravity = Gravity.TOP or Gravity.START }
                try {
                    wm.addView(v, lp)
                } catch (e: Exception) {
                    Logger.w("Input", "cursor overlay unavailable: ${e.message}")
                    return@post
                }
                cursor = v
            }
            val lp = v.layoutParams as WindowManager.LayoutParams
            lp.x = x.toInt()
            lp.y = y.toInt()
            v.visibility = View.VISIBLE
            runCatching { wm.updateViewLayout(v, lp) }
            main.removeCallbacks(hideCursor)
            main.postDelayed(hideCursor, 3000)
        }
    }

    private class CursorView(ctx: Context) : View(ctx) {
        companion object { const val SIZE = 40 }
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; style = Paint.Style.FILL }
        private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.BLACK; style = Paint.Style.STROKE; strokeWidth = 2.5f }
        private val arrow = Path().apply {
            moveTo(2f, 2f); lineTo(2f, 30f); lineTo(10f, 23f); lineTo(16f, 36f)
            lineTo(21f, 34f); lineTo(15f, 21f); lineTo(26f, 21f); close()
        }
        override fun onDraw(canvas: Canvas) {
            canvas.drawPath(arrow, fill)
            canvas.drawPath(arrow, stroke)
        }
    }
}
