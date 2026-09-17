import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

Uint8List buildMdxFixture({
  bool compressed = true,
  int? compression,
  bool encrypted = false,
  bool includeEncoding = true,
  bool resourceLibrary = false,
  String engineVersion = '2.0',
  List<MapEntry<String, String>> entries = const <MapEntry<String, String>>[
    MapEntry<String, String>('apple', '<b>first</b>'),
    MapEntry<String, String>('apple', '<b>second</b>'),
    MapEntry<String, String>('banana', 'yellow fruit'),
  ],
}) {
  final selectedCompression = compression ?? (compressed ? 2 : 0);
  final utf16Keys = resourceLibrary && !includeEncoding;
  final records = <int>[];
  final keyPayload = <int>[];
  for (final entry in entries) {
    keyPayload.addAll(_uint64(records.length));
    keyPayload.addAll(utf16Keys ? _utf16Le(entry.key) : utf8.encode(entry.key));
    keyPayload.addAll(utf16Keys ? const <int>[0, 0] : const <int>[0]);
    records.addAll(utf8.encode(entry.value));
  }
  final keyBlock = _block(keyPayload, compression: selectedCompression);
  final first = utf16Keys
      ? _utf16Le(entries.first.key)
      : utf8.encode(entries.first.key);
  final last = utf16Keys
      ? _utf16Le(entries.last.key)
      : utf8.encode(entries.last.key);
  final terminator = utf16Keys ? const <int>[0, 0] : const <int>[0];
  final keyInfoPayload = <int>[
    ..._uint64(entries.length),
    ..._uint16(utf16Keys ? first.length ~/ 2 : first.length),
    ...first,
    ...terminator,
    ..._uint16(utf16Keys ? last.length ~/ 2 : last.length),
    ...last,
    ...terminator,
    ..._uint64(keyBlock.length),
    ..._uint64(keyPayload.length),
  ];
  final keyInfoBlock = _block(keyInfoPayload, compression: selectedCompression);
  final recordBlock = _block(records, compression: selectedCompression);
  final header = _utf16Le(
    '<${resourceLibrary ? 'Library_Data' : 'Dictionary'} '
    'GeneratedByEngineVersion="$engineVersion" '
    '${includeEncoding ? 'Encoding="UTF-8" ' : ''}'
    'Encrypted="${encrypted ? '2' : 'No'}"/>\u0000',
  );
  final keyHeader = <int>[
    ..._uint64(1),
    ..._uint64(entries.length),
    ..._uint64(keyInfoPayload.length),
    ..._uint64(keyInfoBlock.length),
    ..._uint64(keyBlock.length),
  ];
  final result = <int>[
    ..._uint32(header.length),
    ...header,
    ..._uint32Le(_adler32(header)),
    ...keyHeader,
    ..._uint32(_adler32(keyHeader)),
    ...keyInfoBlock,
    ...keyBlock,
    ..._uint64(1),
    ..._uint64(entries.length),
    ..._uint64(16),
    ..._uint64(recordBlock.length),
    ..._uint64(recordBlock.length),
    ..._uint64(records.length),
    ...recordBlock,
  ];
  return Uint8List.fromList(result);
}

List<int> _block(List<int> expanded, {required int compression}) {
  final payload = switch (compression) {
    0 => List<int>.from(expanded),
    1 => _lzoLiteralBlock(expanded),
    2 => ZLibCodec(level: 6).encode(expanded),
    _ => throw ArgumentError.value(compression, 'compression'),
  };
  return <int>[
    compression,
    0,
    0,
    0,
    ..._uint32(_adler32(expanded)),
    ...payload,
  ];
}

List<int> _lzoLiteralBlock(List<int> expanded) {
  if (expanded.isEmpty) return const <int>[17, 0, 0];
  final result = <int>[];
  if (expanded.length <= 238) {
    result.add(17 + expanded.length);
  } else {
    final encoded = expanded.length - 18;
    result.add(0);
    var remaining = encoded;
    while (remaining > 255) {
      result.add(0);
      remaining -= 255;
    }
    if (remaining == 0) {
      result
        ..removeLast()
        ..add(255);
    } else {
      result.add(remaining);
    }
  }
  result
    ..addAll(expanded)
    ..addAll(const <int>[17, 0, 0]);
  return result;
}

List<int> _utf16Le(String value) => <int>[
  for (final unit in value.codeUnits) ...<int>[unit & 0xff, unit >> 8],
];

List<int> _uint16(int value) => <int>[value >> 8, value & 0xff];

List<int> _uint32(int value) => <int>[
  (value >> 24) & 0xff,
  (value >> 16) & 0xff,
  (value >> 8) & 0xff,
  value & 0xff,
];

List<int> _uint32Le(int value) => <int>[
  value & 0xff,
  (value >> 8) & 0xff,
  (value >> 16) & 0xff,
  (value >> 24) & 0xff,
];

List<int> _uint64(int value) => <int>[
  for (var shift = 56; shift >= 0; shift -= 8) (value >> shift) & 0xff,
];

int _adler32(List<int> bytes) {
  const modulus = 65521;
  var first = 1;
  var second = 0;
  for (final byte in bytes) {
    first = (first + byte) % modulus;
    second = (second + first) % modulus;
  }
  return ((second << 16) | first) & 0xffffffff;
}
