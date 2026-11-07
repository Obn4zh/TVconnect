package dev.tvconnect.tv.ui

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** Tiny programmatic UI kit tuned for 10-foot D-pad navigation (large text, obvious focus). */
object Ui {
    const val BG = 0xFF101418.toInt()
    private const val ACCENT = 0xFF3D8BFF.toInt()
    private const val BTN = 0xFF263040.toInt()

    fun dp(ctx: Context, v: Int) = (v * ctx.resources.displayMetrics.density).toInt()

    fun column(ctx: Context, padding: Int = 48) = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        setBackgroundColor(BG)
        val p = dp(ctx, padding)
        setPadding(p, p / 2, p, p / 2)
    }

    fun row(ctx: Context) = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }

    fun text(ctx: Context, s: String, sizeSp: Float = 18f, bold: Boolean = false, color: Int = Color.WHITE) =
        TextView(ctx).apply {
            text = s
            textSize = sizeSp
            setTextColor(color)
            if (bold) typeface = Typeface.DEFAULT_BOLD
            setPadding(0, dp(ctx, 4), 0, dp(ctx, 4))
        }

    fun button(ctx: Context, label: String, onClick: () -> Unit) = Button(ctx).apply {
        text = label
        isAllCaps = false
        textSize = 18f
        setTextColor(Color.WHITE)
        isFocusable = true
        background = StateListDrawable().apply {
            val r = dp(ctx, 8).toFloat()
            addState(intArrayOf(android.R.attr.state_focused), GradientDrawable().apply { setColor(ACCENT); cornerRadius = r })
            addState(intArrayOf(android.R.attr.state_pressed), GradientDrawable().apply { setColor(ACCENT); cornerRadius = r })
            addState(intArrayOf(), GradientDrawable().apply { setColor(BTN); cornerRadius = r })
        }
        setPadding(dp(ctx, 24), dp(ctx, 10), dp(ctx, 24), dp(ctx, 10))
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, dp(ctx, 6), dp(ctx, 16), dp(ctx, 6))
        }
        setOnClickListener { onClick() }
    }
}
