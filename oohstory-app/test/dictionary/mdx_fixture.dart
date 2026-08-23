import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

Uint8List buildMdxFixture({
  bool compressed = true,
  bool encrypted = false,
  List<MapEntry<String, String>> entries = const <MapEntry<String, String>>[
    MapEntry<String, String>('apple', '<b>first</b>'),
    MapEntry<String, String>('apple', '<b>second</b>'),
    MapEntry<String, String>('banana', 'yellow fruit'),
  ],
}) {
  final records = <int>[];
  final keyPayload = <int>[];
  for (final entry in entries) {
    keyPayload.addAll(_uint64(records.length));
    keyPayload.addAll(utf8.encode(entry.key));
    keyPayload.add(0);
    records.addAll(utf8.encode(entry.value));
  }
  final keyBlock = _block(keyPayload, compressed: compressed);
  final first = utf8.encode(entries.first.key);
  final last = utf8.encode(entries.last.key);
  final keyInfoPayload = <int>[
    ..._uint64(entries.length),
    ..._uint16(first.length),
    ...first,
    0,
    ..._uint16(last.length),
    ...last,
    0,
    ..._uint64(keyBlock.length),
    ..._uint64(keyPayload.length),
  ];
  final keyInfoBlock = _block(keyInfoPayload, compressed: compressed);
  final recordBlock = _block(records, compressed: compressed);
  final header = _utf16Le(
    '<Dictionary GeneratedByEngineVersion="2.0" '
    'Encoding="UTF-8" Encrypted="${encrypted ? '2' : 'No'}"/>\u0000',
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
    ..._uint32(_adler32(header)),
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

List<int> _block(List<int> expanded, {required bool compressed}) {
  final payload = compressed
      ? ZLibCodec(level: 6).encode(expanded)
      : List<int>.from(expanded);
  return <int>[
    compressed ? 2 : 0,
    0,
    0,
    0,
    ..._uint32(_adler32(expanded)),
    ...payload,
  ];
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
