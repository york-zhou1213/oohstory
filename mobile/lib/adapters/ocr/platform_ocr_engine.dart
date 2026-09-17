import 'package:flutter/services.dart';

import '../../core/errors.dart';
import '../../core/models.dart';
import 'local_ocr_adapter.dart';

/// Bridges to the operating-system/bundled OCR implementation.
///
/// Encoded image bytes only cross the in-process Flutter platform channel. No
/// network transport or durable file is exposed to Dart.
class PlatformOcrEngine implements LocalOcrEngine {
  PlatformOcrEngine({MethodChannel channel = const MethodChannel(_channelName)})
    : _channel = channel;

  static const _channelName = 'com.oohstory.oohstory/local_ocr';
  static int _nextRequestId = 0;

  final MethodChannel _channel;

  @override
  Set<String> get supportedLanguages => const <String>{'en', 'zh-Hans'};

  @override
  Future<OcrResult> recognize(
    Uint8List ephemeralImageBytes, {
    required OcrCancellationToken cancellation,
    String? locale,
  }) async {
    cancellation.throwIfCancelled();
    final requestId =
        '${DateTime.now().microsecondsSinceEpoch}-${_nextRequestId++}';
    try {
      final payload = await _channel
          .invokeMapMethod<String, Object?>('recognize', <String, Object?>{
            'requestId': requestId,
            'bytes': ephemeralImageBytes,
            'locale': locale ?? 'zh-Hans',
          });
      cancellation.throwIfCancelled();
      final text = payload?['text'];
      final confidence = payload?['confidence'];
      if (text is! String || confidence is! num) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Local OCR platform response is malformed',
        );
      }
      return OcrResult(text: text, confidence: confidence.toDouble());
    } on PlatformException catch (error) {
      if (error.code == 'OCR_UNAVAILABLE') {
        throw CoreException(
          CoreErrorCode.unsupported,
          error.message ?? 'Local OCR is unavailable on this device',
        );
      }
      if (error.code == 'OCR_CANCELLED') {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Local OCR operation was cancelled',
        );
      }
      throw CoreException(
        CoreErrorCode.validationError,
        error.message ?? 'Local OCR failed',
      );
    } on MissingPluginException {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Local OCR platform implementation is unavailable',
      );
    }
  }
}
