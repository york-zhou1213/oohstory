import AVFAudio
import Flutter
import ImageIO
import UIKit
import Vision

@main
@objc class AppDelegate: FlutterAppDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    GeneratedPluginRegistrant.register(with: self)
    if let controller = window?.rootViewController as? FlutterViewController {
      let ocrChannel = FlutterMethodChannel(
        name: "com.oohstory.oohstory/local_ocr",
        binaryMessenger: controller.binaryMessenger
      )
      ocrChannel.setMethodCallHandler { call, result in
        guard call.method == "recognize" else {
          result(FlutterMethodNotImplemented)
          return
        }
        guard
          let arguments = call.arguments as? [String: Any],
          let typedData = arguments["bytes"] as? FlutterStandardTypedData,
          !typedData.data.isEmpty
        else {
          result(FlutterError(code: "OCR_INVALID_IMAGE", message: "OCR image is empty", details: nil))
          return
        }
        let locale = arguments["locale"] as? String ?? "zh-Hans"
        Self.recognizeText(data: typedData.data, locale: locale, result: result)
      }
    }
    let audioSession = AVAudioSession.sharedInstance()
    try? audioSession.setCategory(
      .playback,
      mode: .spokenAudio,
      options: [.allowAirPlay, .allowBluetoothA2DP]
    )
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  private static func recognizeText(
    data: Data,
    locale: String,
    result: @escaping FlutterResult
  ) {
    DispatchQueue.global(qos: .userInitiated).async {
      guard
        let source = CGImageSourceCreateWithData(data as CFData, nil),
        let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
      else {
        DispatchQueue.main.async {
          result(FlutterError(code: "OCR_INVALID_IMAGE", message: "Image could not be decoded", details: nil))
        }
        return
      }

      let request = VNRecognizeTextRequest()
      request.recognitionLevel = .accurate
      request.usesLanguageCorrection = true
      request.recognitionLanguages = locale.lowercased().hasPrefix("zh")
        ? ["zh-Hans", "en-US"]
        : ["en-US", "zh-Hans"]
      do {
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        let candidates = (request.results ?? []).compactMap { $0.topCandidates(1).first }
        let text = candidates.map(\.string).joined(separator: "\n")
        let confidence = candidates.isEmpty
          ? 1.0
          : candidates.reduce(0.0) { $0 + Double($1.confidence) } / Double(candidates.count)
        DispatchQueue.main.async {
          result(["text": text, "confidence": confidence])
        }
      } catch {
        DispatchQueue.main.async {
          result(FlutterError(code: "OCR_FAILED", message: error.localizedDescription, details: nil))
        }
      }
    }
  }
}
