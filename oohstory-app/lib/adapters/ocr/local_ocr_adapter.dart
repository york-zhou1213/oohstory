import 'dart:async';
import 'dart:typed_data';

import '../../core/capabilities.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import '../contracts/adapter_contracts.dart';
import '../dictionary/_zlib_decoder.dart';

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
  final Completer<void> _cancelledSignal = Completer<void>();

  bool get isCancelled => _cancelled;

  void throwIfCancelled() {
    if (_cancelled) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Local OCR operation was cancelled',
      );
    }
  }

  Future<OcrResult> get _cancelledResult async {
    await _cancelledSignal.future;
    throw const CoreException(
      CoreErrorCode.validationError,
      'Local OCR operation was cancelled',
    );
  }

  void _cancel() {
    if (_cancelled) return;
    _cancelled = true;
    _cancelledSignal.complete();
  }
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

  LocalOcrAdapter.portable({
    required String platform,
    this.limits = const OcrImageLimits(),
  }) : _engine = BitmapOcrEngine(limits: limits),
       platform = _validatedPlatform(platform) {
    _validateLimits(limits);
    _languages = _validateLanguages(_engine!.supportedLanguages);
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
        final engineResult = Future<OcrResult>.sync(
          () => engine.recognize(
            ephemeral,
            cancellation: token,
            locale: normalizedLocale,
          ),
        );
        final result = await Future.any<OcrResult>(<Future<OcrResult>>[
          engineResult,
          token._cancelledResult,
        ]);
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

/// A dependency-free, on-device OCR engine for high-contrast Latin bitmap text.
///
/// It decodes bounded, non-interlaced 8-bit PNG pixels, segments glyphs, and
/// matches them against a built-in 5x7 font. It is intentionally discoverable
/// as English-only; unsupported scripts need a platform engine implementation.
class BitmapOcrEngine implements LocalOcrEngine {
  BitmapOcrEngine({this.limits = const OcrImageLimits()}) {
    _validateLimits(limits);
  }

  final OcrImageLimits limits;

  @override
  Set<String> get supportedLanguages => const <String>{'en'};

  @override
  Future<OcrResult> recognize(
    Uint8List ephemeralImageBytes, {
    required OcrCancellationToken cancellation,
    String? locale,
  }) async {
    cancellation.throwIfCancelled();
    _validateImage(ephemeralImageBytes, limits);
    final image = _decodePng(ephemeralImageBytes, cancellation);
    final glyphs = _segmentGlyphs(image, cancellation);
    if (glyphs.isEmpty) return const OcrResult(text: '', confidence: 1);

    final text = StringBuffer();
    var score = 0.0;
    var previousEnd = glyphs.first.start;
    final typicalWidth =
        glyphs
            .map((glyph) => glyph.end - glyph.start)
            .reduce((left, right) => left + right) /
        glyphs.length;
    for (final glyph in glyphs) {
      cancellation.throwIfCancelled();
      if (text.isNotEmpty && glyph.start - previousEnd > typicalWidth * 0.75) {
        text.write(' ');
      }
      final sample = _sampleGlyph(image, glyph);
      var bestCharacter = '?';
      var bestDistance = 36;
      for (final entry in _glyphTemplates.entries) {
        var distance = 0;
        for (var index = 0; index < sample.length; index++) {
          if (sample[index] != entry.value[index]) distance++;
        }
        if (distance < bestDistance) {
          bestCharacter = entry.key;
          bestDistance = distance;
        }
      }
      if (bestDistance > 12) bestCharacter = '?';
      text.write(bestCharacter);
      score += 1 - (bestDistance.clamp(0, 35) / 35);
      previousEnd = glyph.end;
    }
    return OcrResult(text: text.toString(), confidence: score / glyphs.length);
  }
}

_Bitmap _decodePng(Uint8List bytes, OcrCancellationToken cancellation) {
  const signature = <int>[137, 80, 78, 71, 13, 10, 26, 10];
  if (!_startsWith(bytes, signature)) {
    throw const CoreException(
      CoreErrorCode.unsupported,
      'Portable bitmap OCR supports PNG images only',
    );
  }
  var offset = signature.length;
  int? width;
  int? height;
  int? bytesPerPixel;
  var sawHeader = false;
  var sawEnd = false;
  final compressed = BytesBuilder(copy: false);
  while (offset < bytes.length) {
    cancellation.throwIfCancelled();
    if (offset + 12 > bytes.length) {
      throw const FormatException('PNG chunk is truncated');
    }
    final length = _uint32Be(bytes, offset);
    offset += 4;
    if (length > bytes.length - offset - 8) {
      throw const FormatException('PNG chunk length is invalid');
    }
    final type = bytes.sublist(offset, offset + 4);
    offset += 4;
    final data = bytes.sublist(offset, offset + length);
    offset += length;
    final checksum = _uint32Be(bytes, offset);
    offset += 4;
    if (_crc32(<int>[...type, ...data]) != checksum) {
      throw const FormatException('PNG chunk checksum mismatch');
    }
    final name = String.fromCharCodes(type);
    if (!sawHeader && name != 'IHDR') {
      throw const FormatException('PNG header chunk is missing');
    }
    switch (name) {
      case 'IHDR':
        if (sawHeader || data.length != 13) {
          throw const FormatException('PNG header is invalid');
        }
        width = _uint32Be(data, 0);
        height = _uint32Be(data, 4);
        final bitDepth = data[8];
        final colorType = data[9];
        if (width <= 0 ||
            height <= 0 ||
            bitDepth != 8 ||
            data[10] != 0 ||
            data[11] != 0 ||
            data[12] != 0) {
          throw const CoreException(
            CoreErrorCode.unsupported,
            'Portable bitmap OCR requires non-interlaced 8-bit PNG pixels',
          );
        }
        bytesPerPixel = switch (colorType) {
          0 => 1,
          2 => 3,
          4 => 2,
          6 => 4,
          _ => throw const CoreException(
            CoreErrorCode.unsupported,
            'Portable bitmap OCR PNG color type is unsupported',
          ),
        };
        sawHeader = true;
      case 'IDAT':
        if (!sawHeader || sawEnd) {
          throw const FormatException('PNG image data is out of order');
        }
        compressed.add(data);
      case 'IEND':
        if (data.isNotEmpty || !sawHeader || sawEnd) {
          throw const FormatException('PNG end chunk is invalid');
        }
        sawEnd = true;
        if (offset != bytes.length) {
          throw const FormatException('PNG has trailing data');
        }
      default:
        if ((type[0] & 0x20) == 0) {
          throw const CoreException(
            CoreErrorCode.unsupported,
            'Portable bitmap OCR encountered an unsupported PNG chunk',
          );
        }
    }
  }
  if (!sawEnd || width == null || height == null || bytesPerPixel == null) {
    throw const FormatException('PNG image is incomplete');
  }
  final imageWidth = width;
  final imageHeight = height;
  final pixelStride = bytesPerPixel;
  final rowBytes = imageWidth * pixelStride;
  final expectedBytes = (rowBytes + 1) * imageHeight;
  final inflated = decodeZlib(
    compressed.takeBytes(),
    maxOutputBytes: expectedBytes,
  );
  if (inflated.length != expectedBytes) {
    throw const FormatException('PNG pixel data size is invalid');
  }

  final pixels = List<Uint8List>.generate(
    imageHeight,
    (_) => Uint8List(rowBytes),
    growable: false,
  );
  var source = 0;
  for (var y = 0; y < imageHeight; y++) {
    cancellation.throwIfCancelled();
    final filter = inflated[source++];
    if (filter > 4) throw const FormatException('PNG row filter is invalid');
    for (var x = 0; x < rowBytes; x++) {
      final raw = inflated[source++];
      final left = x >= pixelStride ? pixels[y][x - pixelStride] : 0;
      final up = y > 0 ? pixels[y - 1][x] : 0;
      final upLeft = y > 0 && x >= pixelStride
          ? pixels[y - 1][x - pixelStride]
          : 0;
      pixels[y][x] = switch (filter) {
        0 => raw,
        1 => (raw + left) & 0xff,
        2 => (raw + up) & 0xff,
        3 => (raw + ((left + up) >> 1)) & 0xff,
        4 => (raw + _paeth(left, up, upLeft)) & 0xff,
        _ => throw StateError('unreachable'),
      };
    }
  }

  final ink = List<Uint8List>.generate(
    imageHeight,
    (_) => Uint8List(imageWidth),
    growable: false,
  );
  for (var y = 0; y < imageHeight; y++) {
    cancellation.throwIfCancelled();
    for (var x = 0; x < imageWidth; x++) {
      final start = x * pixelStride;
      final (red, green, blue, alpha) = switch (pixelStride) {
        1 => (pixels[y][start], pixels[y][start], pixels[y][start], 255),
        2 => (
          pixels[y][start],
          pixels[y][start],
          pixels[y][start],
          pixels[y][start + 1],
        ),
        3 => (
          pixels[y][start],
          pixels[y][start + 1],
          pixels[y][start + 2],
          255,
        ),
        4 => (
          pixels[y][start],
          pixels[y][start + 1],
          pixels[y][start + 2],
          pixels[y][start + 3],
        ),
        _ => throw StateError('unreachable'),
      };
      final luminance = (red * 299 + green * 587 + blue * 114) ~/ 1000;
      ink[y][x] = alpha >= 128 && luminance < 128 ? 1 : 0;
    }
  }
  return _Bitmap(imageWidth, imageHeight, ink);
}

List<_GlyphBounds> _segmentGlyphs(
  _Bitmap image,
  OcrCancellationToken cancellation,
) {
  var top = image.height;
  var bottom = 0;
  for (var y = 0; y < image.height; y++) {
    cancellation.throwIfCancelled();
    if (image.ink[y].contains(1)) {
      if (y < top) top = y;
      bottom = y + 1;
    }
  }
  if (top == image.height) return const <_GlyphBounds>[];
  final result = <_GlyphBounds>[];
  int? start;
  for (var x = 0; x <= image.width; x++) {
    cancellation.throwIfCancelled();
    var hasInk = false;
    if (x < image.width) {
      for (var y = top; y < bottom; y++) {
        if (image.ink[y][x] != 0) {
          hasInk = true;
          break;
        }
      }
    }
    if (hasInk) {
      start ??= x;
    } else if (start != null) {
      result.add(_GlyphBounds(start, x, top, bottom));
      start = null;
    }
  }
  return result;
}

List<int> _sampleGlyph(_Bitmap image, _GlyphBounds bounds) {
  final result = <int>[];
  final width = bounds.end - bounds.start;
  final height = bounds.bottom - bounds.top;
  for (var row = 0; row < 7; row++) {
    final top = bounds.top + (row * height ~/ 7);
    final bottom = bounds.top + ((row + 1) * height ~/ 7);
    for (var column = 0; column < 5; column++) {
      final left = bounds.start + (column * width ~/ 5);
      final right = bounds.start + ((column + 1) * width ~/ 5);
      var dark = 0;
      var total = 0;
      for (var y = top; y < bottom; y++) {
        for (var x = left; x < right; x++) {
          dark += image.ink[y][x];
          total++;
        }
      }
      result.add(total > 0 && dark * 2 >= total ? 1 : 0);
    }
  }
  return result;
}

class _Bitmap {
  const _Bitmap(this.width, this.height, this.ink);
  final int width;
  final int height;
  final List<Uint8List> ink;
}

class _GlyphBounds {
  const _GlyphBounds(this.start, this.end, this.top, this.bottom);
  final int start;
  final int end;
  final int top;
  final int bottom;
}

int _paeth(int left, int up, int upLeft) {
  final prediction = left + up - upLeft;
  final leftDistance = (prediction - left).abs();
  final upDistance = (prediction - up).abs();
  final upLeftDistance = (prediction - upLeft).abs();
  if (leftDistance <= upDistance && leftDistance <= upLeftDistance) return left;
  if (upDistance <= upLeftDistance) return up;
  return upLeft;
}

int _crc32(List<int> bytes) {
  var crc = 0xffffffff;
  for (final byte in bytes) {
    crc ^= byte;
    for (var bit = 0; bit < 8; bit++) {
      crc = (crc & 1) == 0 ? crc >> 1 : (crc >> 1) ^ 0xedb88320;
    }
  }
  return (crc ^ 0xffffffff) & 0xffffffff;
}

List<int> _glyph(String rows) => <int>[
  for (final character in rows.replaceAll('/', '').split(''))
    character == '1' ? 1 : 0,
];

final Map<String, List<int>> _glyphTemplates = <String, List<int>>{
  'A': _glyph('01110/10001/10001/11111/10001/10001/10001'),
  'B': _glyph('11110/10001/10001/11110/10001/10001/11110'),
  'C': _glyph('01111/10000/10000/10000/10000/10000/01111'),
  'D': _glyph('11110/10001/10001/10001/10001/10001/11110'),
  'E': _glyph('11111/10000/10000/11110/10000/10000/11111'),
  'F': _glyph('11111/10000/10000/11110/10000/10000/10000'),
  'G': _glyph('01111/10000/10000/10111/10001/10001/01111'),
  'H': _glyph('10001/10001/10001/11111/10001/10001/10001'),
  'I': _glyph('11111/00100/00100/00100/00100/00100/11111'),
  'J': _glyph('00111/00010/00010/00010/10010/10010/01100'),
  'K': _glyph('10001/10010/10100/11000/10100/10010/10001'),
  'L': _glyph('10000/10000/10000/10000/10000/10000/11111'),
  'M': _glyph('10001/11011/10101/10101/10001/10001/10001'),
  'N': _glyph('10001/11001/10101/10011/10001/10001/10001'),
  'O': _glyph('01110/10001/10001/10001/10001/10001/01110'),
  'P': _glyph('11110/10001/10001/11110/10000/10000/10000'),
  'Q': _glyph('01110/10001/10001/10001/10101/10010/01101'),
  'R': _glyph('11110/10001/10001/11110/10100/10010/10001'),
  'S': _glyph('01111/10000/10000/01110/00001/00001/11110'),
  'T': _glyph('11111/00100/00100/00100/00100/00100/00100'),
  'U': _glyph('10001/10001/10001/10001/10001/10001/01110'),
  'V': _glyph('10001/10001/10001/10001/10001/01010/00100'),
  'W': _glyph('10001/10001/10001/10101/10101/10101/01010'),
  'X': _glyph('10001/10001/01010/00100/01010/10001/10001'),
  'Y': _glyph('10001/10001/01010/00100/00100/00100/00100'),
  'Z': _glyph('11111/00001/00010/00100/01000/10000/11111'),
  '0': _glyph('01110/10001/10011/10101/11001/10001/01110'),
  '1': _glyph('00100/01100/00100/00100/00100/00100/01110'),
  '2': _glyph('01110/10001/00001/00010/00100/01000/11111'),
  '3': _glyph('11110/00001/00001/01110/00001/00001/11110'),
  '4': _glyph('00010/00110/01010/10010/11111/00010/00010'),
  '5': _glyph('11111/10000/10000/11110/00001/00001/11110'),
  '6': _glyph('01110/10000/10000/11110/10001/10001/01110'),
  '7': _glyph('11111/00001/00010/00100/01000/01000/01000'),
  '8': _glyph('01110/10001/10001/01110/10001/10001/01110'),
  '9': _glyph('01110/10001/10001/01111/00001/00001/01110'),
};

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
  if (dimensions.width <= 0 || dimensions.height <= 0) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'Image dimensions must be positive',
    );
  }
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
  return (bytes[offset] << 24) |
      (bytes[offset + 1] << 16) |
      (bytes[offset + 2] << 8) |
      bytes[offset + 3];
}

class _ImageDimensions {
  const _ImageDimensions(this.width, this.height);
  final int width;
  final int height;
}
