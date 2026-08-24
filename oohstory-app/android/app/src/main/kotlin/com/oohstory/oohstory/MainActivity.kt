package com.oohstory.oohstory

import android.content.Intent
import android.net.Uri
import com.ryanheise.audioservice.AudioServiceActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : AudioServiceActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "com.oohstory.oohstory/app")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "openUrl" -> {
                        val rawUrl = call.argument<String>("url")
                        val uri = rawUrl?.let { Uri.parse(it) }
                        if (uri == null || uri.scheme != "https") {
                            result.error("INVALID_URL", "Only https update URLs are supported", null)
                            return@setMethodCallHandler
                        }
                        try {
                            val intent = Intent(Intent.ACTION_VIEW, uri)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            startActivity(intent)
                            result.success(true)
                        } catch (error: Exception) {
                            result.error("OPEN_URL_FAILED", error.message, null)
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }
}
