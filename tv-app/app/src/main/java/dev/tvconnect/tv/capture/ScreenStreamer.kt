package dev.tvconnect.tv.capture

import android.content.Context
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.os.Build
import android.os.Bundle
import android.util.DisplayMetrics
import android.view.Display
import android.view.Surface
import dev.tvconnect.tv.net.Proto
import dev.tvconnect.tv.util.Logger

data class ScreenInfo(val width: Int, val height: Int, val dpi: Int) {
    companion object {
        @Suppress("DEPRECATION")
        fun read(ctx: Context): ScreenInfo {
            val dm = DisplayMetrics()
            val d = (ctx.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager).getDisplay(Display.DEFAULT_DISPLAY)
            d.getRealMetrics(dm)
            return ScreenInfo(dm.widthPixels, dm.heightPixels, dm.densityDpi)
        }
    }
}

data class VideoParams(val width: Int, val height: Int, val fps: Int, val bitrate: Int)

/**
 * MediaProjection -> VirtualDisplay -> MediaCodec (H.264, surface input) -> [sink].
 * The VirtualDisplay is kept for the whole projection and re-targeted when quality changes, because on
 * Android 14+ a MediaProjection may create only one VirtualDisplay.
 */
class ScreenStreamer(
    private val ctx: Context,
    private val projection: MediaProjection,
    private val sink: (flags: Int, ptsUs: Long, data: ByteArray) -> Unit,
) {
    private var vd: VirtualDisplay? = null
    private var codec: MediaCodec? = null
    private var surface: Surface? = null
    private var drainThread: Thread? = null
    @Volatile private var running = false
    var params: VideoParams? = null
        private set

    @Synchronized
    fun start(maxW: Int, maxH: Int, fps: Int, bitrate: Int): VideoParams {
        stopEncoder()
        val screen = ScreenInfo.read(ctx)
        var p = chooseParams(screen, maxW, maxH, fps, bitrate)
        var c: MediaCodec? = null
        for ((name, caps) in encoders()) {
            // Capability tables are often incomplete on TV firmwares; if nothing "fits", still try configure().
            val cand = caps?.let { fitParams(screen, it, maxW, maxH, fps, bitrate) } ?: p
            Logger.i("Streamer", "trying $name: ${cand.width}x${cand.height}@${cand.fps} " +
                (caps?.let { "(widths ${it.supportedWidths}, heights ${it.supportedHeights}, bitrate ${it.bitrateRange})" } ?: "(unlisted)"))
            val codec = try {
                MediaCodec.createByCodecName(name)
            } catch (e: Exception) {
                Logger.w("Streamer", "cannot create $name: ${e.message}")
                continue
            }
            val ok = listOf(true, false).any { full ->
                try {
                    codec.configure(buildFormat(cand, full), null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
                    true
                } catch (e: Exception) {
                    Logger.w("Streamer", "$name rejected format (full=$full): ${e.message}")
                    codec.reset()
                    false
                }
            }
            if (ok) {
                c = codec
                p = cand
                break
            }
            codec.release()
        }
        val enc = c ?: throw IllegalStateException("no H.264 encoder accepted ${p.width}x${p.height}")
        val s = enc.createInputSurface()
        enc.start()
        val display = vd
        if (display == null) {
            vd = projection.createVirtualDisplay(
                "TVconnect", p.width, p.height, screen.dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, s, null, null,
            )
        } else {
            display.resize(p.width, p.height, screen.dpi)
            display.surface = s
        }
        codec = enc
        surface = s
        params = p
        running = true
        drainThread = Thread({ drain(enc) }, "h264-drain").apply { start() }
        Logger.i("Streamer", "encoding ${p.width}x${p.height}@${p.fps} ${p.bitrate / 1000} kbps via ${enc.name}")
        return p
    }

    private fun buildFormat(p: VideoParams, full: Boolean): MediaFormat =
        MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, p.width, p.height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, p.bitrate)
            setInteger(MediaFormat.KEY_FRAME_RATE, p.fps)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 5)
            // Re-send the last frame while the screen is static so the client never stalls.
            setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 100_000L)
            if (full) {
                setInteger(MediaFormat.KEY_PRIORITY, 0) // realtime
                setFloat("max-fps-to-encoder", p.fps.toFloat())
                if (Build.VERSION.SDK_INT >= 29) setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0)
                if (Build.VERSION.SDK_INT >= 30) setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
                setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR)
            }
        }

    /** H.264 encoders, hardware first (software ones are a last resort: slow and CPU-hungry on TV SoCs). */
    private fun encoders(): List<Pair<String, MediaCodecInfo.VideoCapabilities?>> {
        val all = MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos.filter { info ->
            info.isEncoder && info.supportedTypes.any { it.equals(MediaFormat.MIMETYPE_VIDEO_AVC, ignoreCase = true) }
        }
        fun isSoftware(i: MediaCodecInfo): Boolean =
            if (Build.VERSION.SDK_INT >= 29) i.isSoftwareOnly
            else i.name.startsWith("OMX.google.") || i.name.startsWith("c2.android.")
        val (sw, hw) = all.partition(::isSoftware)
        val listed = all.map { it.name }.toSet()
        // Some TV firmwares declare a vendor encoder in media_codecs.xml but hide it from MediaCodecList.
        val hidden = HIDDEN_VENDOR_ENCODERS.filter { it !in listed }.map { it to null }
        fun caps(i: MediaCodecInfo) = i.name to i.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC).videoCapabilities
        return hw.map(::caps) + hidden + sw.map(::caps)
    }

    companion object {
        private val HIDDEN_VENDOR_ENCODERS = listOf(
            "OMX.amlogic.video.encoder.avc", "c2.amlogic.avc.encoder",
            "OMX.MTK.VIDEO.ENCODER.AVC", "OMX.realtek.video.encoder.avc",
        )
    }

    private fun chooseParams(screen: ScreenInfo, maxW: Int, maxH: Int, fps: Int, bitrate: Int): VideoParams {
        val scale = minOf(1.0, maxW.toDouble() / screen.width, maxH.toDouble() / screen.height)
        return VideoParams(
            (screen.width * scale).toInt() / 8 * 8, (screen.height * scale).toInt() / 8 * 8,
            fps.coerceIn(5, 60), bitrate.coerceIn(500_000, 40_000_000),
        )
    }

    private fun fitParams(
        screen: ScreenInfo, caps: MediaCodecInfo.VideoCapabilities, maxW: Int, maxH: Int, fps: Int, bitrate: Int,
    ): VideoParams? {
        val reqFps = fps.coerceIn(5, 60)
        val br = bitrate.coerceIn(500_000, 40_000_000).coerceIn(caps.bitrateRange.lower, caps.bitrateRange.upper)
        for ((mw, mh) in listOf(maxW to maxH, 1280 to 720, 960 to 540)) {
            val scale = minOf(1.0, mw.toDouble() / screen.width, mh.toDouble() / screen.height)
            val w = (screen.width * scale).toInt() / 16 * 16
            val h = (screen.height * scale).toInt() / 8 * 8
            if (!caps.isSizeSupported(w, h)) continue
            val f = when {
                caps.areSizeAndRateSupported(w, h, reqFps.toDouble()) -> reqFps
                caps.areSizeAndRateSupported(w, h, 30.0) -> minOf(30, reqFps)
                else -> continue
            }
            return VideoParams(w, h, f, br)
        }
        return null
    }

    private fun drain(c: MediaCodec) {
        val info = MediaCodec.BufferInfo()
        try {
            while (running) {
                val idx = c.dequeueOutputBuffer(info, 100_000)
                if (idx < 0) continue
                val buf = c.getOutputBuffer(idx)
                if (buf != null && info.size > 0) {
                    val data = ByteArray(info.size)
                    buf.position(info.offset)
                    buf.get(data, 0, info.size)
                    val flags = when {
                        info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0 -> Proto.VF_CONFIG
                        info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0 -> Proto.VF_KEY
                        else -> 0
                    }
                    sink(flags, info.presentationTimeUs, data)
                }
                c.releaseOutputBuffer(idx, false)
                if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) break
            }
        } catch (e: IllegalStateException) {
            if (running) Logger.e("Streamer", "encoder failed", e)
        } catch (e: Exception) {
            Logger.e("Streamer", "drain failed", e)
        }
    }

    fun requestKeyframe() {
        try {
            codec?.setParameters(Bundle().apply { putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0) })
        } catch (e: IllegalStateException) {
            Logger.d("Streamer", "keyframe request ignored: ${e.message}")
        }
    }

    @Synchronized
    fun stopEncoder() {
        running = false
        vd?.surface = null
        drainThread?.join(1000)
        drainThread = null
        codec?.let {
            try { it.stop() } catch (_: Exception) {}
            it.release()
        }
        codec = null
        surface?.release()
        surface = null
    }

    @Synchronized
    fun release() {
        stopEncoder()
        vd?.release()
        vd = null
        try { projection.stop() } catch (_: Exception) {}
        Logger.i("Streamer", "released")
    }
}
