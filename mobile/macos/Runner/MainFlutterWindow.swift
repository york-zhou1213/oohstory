import Cocoa
import FlutterMacOS
import ImageIO
import Vision

class MainFlutterWindow: NSWindow {
  override func awakeFromNib() {
    let flutterViewController = FlutterViewController()
    let windowFrame = self.frame
    self.contentViewController = flutterViewController
    self.setFrame(windowFrame, display: true)

    RegisterGeneratedPlugins(registry: flutterViewController)

    let ocrChannel = FlutterMethodChannel(
      name: "com.oohstory.oohstory/local_ocr",
      binaryMessenger: flutterViewController.engine.binaryMessenger
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

    super.awakeFromNib()
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
