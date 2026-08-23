import 'dart:async';
import 'dart:typed_data';

import '../../core/capabilities.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import '../contracts/adapter_contracts.dart';

class OcrImageLimits {
  const OcrImageLimits({
    this.maxEncodedBytes = 12 * 1024 * 1024,
    this.maxWidth = 8192,
    this.maxHeight = 8192,
    this.maxPixels = 32 * 1024 * 1024,
  });

  final int maxEncodedBytes;
  final int maxWidth;
  final int maxHeight;
  final int maxPixels;
}

abstract interface class LocalOcrEngine {
  Set<String> get supportedLanguages;

  Future<OcrResult> recognize(
    Uint8List ephemeralImageBytes, {
    required OcrCancellationToken cancellation,
    String? locale,
  });
}

class OcrCancellationToken {
  bool _cancelled = false;

  bool get isCancelled => _cancelled;

  void throwIfCancelled() {
    if (_cancelled) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Local OCR operation was cancelled',
      );
    }
  }

  void _cancel() => _cancelled = true;
}

class OcrJob {
  OcrJob._(this.result, this._token);

  final Future<OcrResult> result;
  final OcrCancellationToken _token;

  bool get isCancelled => _token.isCancelled;
  void cancel() => _token._cancel();
}

class LocalOcrAdapter implements OcrAdapter {
  LocalOcrAdapter.available({
    required LocalOcrEngine engine,
    required String platform,
    this.limits = const OcrImageLimits(),
  }) : _engine = engine,
       platform = _validatedPlatform(platform) {
    _validateLimits(limits);
    _languages = _validateLanguages(engine.supportedLanguages);
  }

  LocalOcrAdapter.unavailable({
    required String platform,
    this.limits = const OcrImageLimits(),
  }) : _engine = null,
       platform = _validatedPlatform(platform),
       _languages = const <String>[] {
    _validateLimits(limits);
  }

  final LocalOcrEngine? _engine;
  final String platform;
  final OcrImageLimits limits;
  late final List<String> _languages;

  bool get isAvailable => _engine != null;
  List<String> get supportedLanguages => _languages;

  @override
  String get providerId => 'local-ocr-$platform';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: isAvailable
        ? const <AdapterCapability>[AdapterCapability.localOcr]
        : const <AdapterCapability>[],
  );

  OcrJob start(Uint8List imageBytes, {String? locale}) {
    final engine = _engine;
    if (engine == null) {
      throw CoreException(
        CoreErrorCode.unsupported,
        'Local OCR is unavailable on $platform',
      );
    }
    final normalizedLocale = _validateLocale(locale, _languages);
    _validateImage(imageBytes, limits);
    final ephemeral = Uint8List.fromList(imageBytes);
    final token = OcrCancellationToken();
    final future = Future<OcrResult>(() async {
      try {
        token.throwIfCancelled();
        final result = await engine.recognize(
          ephemeral,
          cancellation: token,
          locale: normalizedLocale,
        );
        token.throwIfCancelled();
        if (!result.confidence.isFinite ||
            result.confidence < 0 ||
            result.confidence > 1) {
          throw const CoreException(
            CoreErrorCode.validationError,
            'Local OCR engine returned an invalid confidence value',
          );
        }
        return result;
      } finally {
        ephemeral.fillRange(0, ephemeral.length, 0);
      }
    });
    return OcrJob._(future, token);
  }

  @override
  Future<OcrResult> recognize(Uint8List imageBytes, {String? locale}) async {
    return start(imageBytes, locale: locale).result;
  }
}

String _validatedPlatform(String platform) {
  final value = platform.trim().toLowerCase();
  if (value.isEmpty || !RegExp(r'^[a-z0-9_-]+$').hasMatch(value)) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'OCR platform identifier is invalid',
    );
  }
  return value;
}

void _validateLimits(OcrImageLimits limits) {
  if (limits.maxEncodedBytes <= 0 ||
      limits.maxWidth <= 0 ||
      limits.maxHeight <= 0 ||
      limits.maxPixels <= 0) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'OCR image limits must be positive',
    );
  }
}

List<String> _validateLanguages(Set<String> values) {
  final result = <String>[];
  for (final language in values) {
    final value = language.trim();
    if (value.isEmpty ||
        !RegExp(r'^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$').hasMatch(value)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Local OCR engine reported an invalid language tag',
      );
    }
    result.add(value);
  }
  result.sort();
  return List<String>.unmodifiable(result);
}

String? _validateLocale(String? locale, List<String> languages) {
  if (locale == null) return null;
  final value = locale.trim();
  if (value.isEmpty) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'OCR locale must not be blank',
    );
  }
  final supported = languages.any(
    (language) => language.toLowerCase() == value.toLowerCase(),
  );
  if (!supported) {
    throw CoreException(
      CoreErrorCode.unsupported,
      'OCR language is unavailable: $value',
    );
  }
  return languages.firstWhere(
    (language) => language.toLowerCase() == value.toLowerCase(),
  );
}

void _validateImage(Uint8List bytes, OcrImageLimits limits) {
  if (bytes.isEmpty) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'OCR image must not be empty',
    );
  }
  if (bytes.length > limits.maxEncodedBytes) {
    throw const CoreException(
      CoreErrorCode.payloadTooLarge,
      'OCR image exceeds the configured encoded-size limit',
    );
  }
  final dimensions = _readImageDimensions(bytes);
  if (dimensions.width > limits.maxWidth ||
      dimensions.height > limits.maxHeight ||
      dimensions.width > limits.maxPixels ~/ dimensions.height) {
    throw const CoreException(
      CoreErrorCode.payloadTooLarge,
      'OCR image dimensions exceed the configured limit',
    );
  }
}

_ImageDimensions _readImageDimensions(Uint8List bytes) {
  const pngSignature = <int>[137, 80, 78, 71, 13, 10, 26, 10];
  if (_startsWith(bytes, pngSignature)) {
    if (bytes.length < 24 ||
        !_matchesAt(bytes, 12, const <int>[73, 72, 68, 82])) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'PNG image header is malformed',
      );
    }
    return _ImageDimensions(_uint32Be(bytes, 16), _uint32Be(bytes, 20));
  }
  if (bytes.length >= 4 && bytes[0] == 0xff && bytes[1] == 0xd8) {
    var offset = 2;
    while (offset + 4 <= bytes.length) {
      while (offset < bytes.length && bytes[offset] == 0xff) {
        offset++;
      }
      if (offset >= bytes.length) break;
      final marker = bytes[offset++];
      if (marker == 0xd8 ||
          marker == 0xd9 ||
          (marker >= 0xd0 && marker <= 0xd7)) {
        continue;
      }
      if (offset + 2 > bytes.length) break;
      final segmentLength = (bytes[offset] << 8) | bytes[offset + 1];
      if (segmentLength < 2 || offset + segmentLength > bytes.length) break;
      if (_isJpegStartOfFrame(marker)) {
        if (segmentLength < 7) break;
        final height = (bytes[offset + 3] << 8) | bytes[offset + 4];
        final width = (bytes[offset + 5] << 8) | bytes[offset + 6];
        return _ImageDimensions(width, height);
      }
      offset += segmentLength;
    }
    throw const CoreException(
      CoreErrorCode.validationError,
      'JPEG image dimensions are missing or malformed',
    );
  }
  throw const CoreException(
    CoreErrorCode.unsupported,
    'Local OCR accepts bounded PNG or JPEG images only',
  );
}

bool _isJpegStartOfFrame(int marker) =>
    (marker >= 0xc0 && marker <= 0xc3) ||
    (marker >= 0xc5 && marker <= 0xc7) ||
    (marker >= 0xc9 && marker <= 0xcb) ||
    (marker >= 0xcd && marker <= 0xcf);

bool _startsWith(List<int> bytes, List<int> prefix) =>
    bytes.length >= prefix.length && _matchesAt(bytes, 0, prefix);

bool _matchesAt(List<int> bytes, int offset, List<int> expected) {
  if (offset < 0 || offset + expected.length > bytes.length) return false;
  for (var index = 0; index < expected.length; index++) {
    if (bytes[offset + index] != expected[index]) return false;
  }
  return true;
}

int _uint32Be(List<int> bytes, int offset) {
  if (offset < 0 || offset + 4 > bytes.length) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'Image header is truncated',
    );
  }
  final value =
      (bytes[offset] << 24) |
      (bytes[offset + 1] << 16) |
      (bytes[offset + 2] << 8) |
      bytes[offset + 3];
  if (value <= 0) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'Image dimensions must be positive',
    );
  }
  return value;
}

class _ImageDimensions {
  const _ImageDimensions(this.width, this.height);
  final int width;
  final int height;
}
