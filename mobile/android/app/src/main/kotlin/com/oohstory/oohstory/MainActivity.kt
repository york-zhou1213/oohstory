package com.oohstory.oohstory

import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import com.ryanheise.audioservice.AudioServiceActivity
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
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

        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "com.oohstory.oohstory/local_ocr",
        ).setMethodCallHandler { call, result ->
            if (call.method != "recognize") {
                result.notImplemented()
                return@setMethodCallHandler
            }
            val bytes = call.argument<ByteArray>("bytes")
            val locale = call.argument<String>("locale") ?: "zh-Hans"
            if (bytes == null || bytes.isEmpty()) {
                result.error("OCR_INVALID_IMAGE", "OCR image is empty", null)
                return@setMethodCallHandler
            }
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            if (bitmap == null) {
                result.error("OCR_INVALID_IMAGE", "Image could not be decoded", null)
                return@setMethodCallHandler
            }
            val recognizer = if (locale.startsWith("zh", ignoreCase = true)) {
                TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
            } else {
                TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
            }
            recognizer.process(InputImage.fromBitmap(bitmap, 0))
                .addOnSuccessListener { recognized ->
                    val confidence = if (recognized.text.isBlank()) 1.0 else 0.9
                    result.success(
                        mapOf(
                            "text" to recognized.text,
                            "confidence" to confidence,
                        ),
                    )
                }
                .addOnFailureListener { error ->
                    result.error("OCR_FAILED", error.message ?: "On-device OCR failed", null)
                }
                .addOnCompleteListener {
                    bitmap.recycle()
                    recognizer.close()
                }
        }
    }
}
