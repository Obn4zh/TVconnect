package dev.tvconnect.tv.ui

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import dev.tvconnect.tv.service.ProjectionBroker
import dev.tvconnect.tv.util.Logger

/** Invisible activity that shows the system MediaProjection consent dialog. */
class ProjectionActivity : Activity() {
    private var delivered = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState != null) return
        val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        ProjectionBroker.dismiss = {
            runOnUiThread {
                @Suppress("DEPRECATION")
                finishActivity(1) // closes the system consent dialog we started
                finish()
            }
        }
        try {
            @Suppress("DEPRECATION")
            startActivityForResult(mpm.createScreenCaptureIntent(), 1)
        } catch (e: ActivityNotFoundException) {
            Logger.e("Projection", "no consent UI on this firmware", e)
            deliver(ProjectionBroker.Result(RESULT_CANCELED, null,
                "На этой прошивке нет системного диалога захвата экрана — используйте ADB-режим"))
            finish()
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        Logger.i("Projection", "consent result $resultCode")
        deliver(ProjectionBroker.Result(resultCode, data, null))
        finish()
        overridePendingTransition(0, 0)
    }

    private fun deliver(r: ProjectionBroker.Result) {
        if (delivered) return
        delivered = true
        ProjectionBroker.resolve(r)
    }

    override fun onDestroy() {
        ProjectionBroker.dismiss = null
        if (isFinishing) deliver(ProjectionBroker.Result(RESULT_CANCELED, null, null))
        super.onDestroy()
    }
}
