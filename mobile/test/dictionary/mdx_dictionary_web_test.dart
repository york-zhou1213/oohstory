@TestOn('browser')
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/dictionary/_lzo_decoder.dart';
import 'package:oohstory/adapters/dictionary/dictionary.dart';
import 'package:oohstory/core/errors.dart';

void main() {
  test('decodes a fixed zlib-compressed MDX fixture on Web', () async {
    final bytes = Uint8List.fromList(base64Decode(_compressedMdxFixture));
    final adapter = MdxDictionaryAdapter.fromBytes(bytes);

    expect(
      (await adapter.lookup('apple')).map((entry) => entry.definition),
      <String>['<b>first</b>', '<b>second</b>'],
    );
    expect(
      () => MdxDictionaryAdapter.fromBytes(
        bytes,
        limits: const MdxLimits(maxExpandedBytes: 8),
      ),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.payloadTooLarge,
        ),
      ),
    );
  });

  test('decodes a real LZO1X vector on Web', () {
    final source = base64Decode(
      'bG9jYWwtbWR4LWx6by1sb2NhbC1tZHgtbHpvLWxvY2FsLW1keC1sem8tbG9jYWwt'
      'bWR4LWx6by1sb2NhbC1tZHgtbHpvLWxvY2FsLW1keC1sem8tbG9jYWwtbWR4LWx6'
      'by1sb2NhbC1tZHgtbHpvLWxvY2FsLW1keC1sem8tbG9jYWwtbWR4LWx6by1sb2Nh'
      'bC1tZHgtbHpvLWxvY2FsLW1keC1sem8tQVBQTEUgZGVmaW5pdGlvbiB3aXRoIHJl'
      'cGVhdGVkIHJlcGVhdGVkIHJlcGVhdGVkIHRleHQ=',
    );
    final compressed = base64Decode(
      'C2xvY2FsLW1keC1sem8tIHk0AAAMQVBQTEUgZGVmaW5pdGlvbiB3aXRoIHJlcGVh'
      'dGVkKiAACHBlYXRlZCB0ZXh0EQAA',
    );

    expect(
      decodeLzo1x(
        compressed,
        expectedSize: source.length,
        maxOutputBytes: source.length,
      ),
      source,
    );
  });
}

const String _compressedMdxFixture =
    'AAAAmjwARABpAGMAdABpAG8AbgBhAHIAeQAgAEcAZQBuAGUAcgBhAHQAZQBkAEIA'
    'eQBFAG4AZwBpAG4AZQBWAGUAcgBzAGkAbwBuAD0AIgAyAC4AMAAiACAARQBuAGMA'
    'bwBkAGkAbgBnAD0AIgBVAFQARgAtADgAIgAgAEUAbgBjAHIAeQBwAHQAZQBkAD0A'
    'IgBOAG8AIgAvAD4AAACfGfhTAAAAAAAAAAEAAAAAAAAAAwAAAAAAAAApAAAAAAAA'
    'ACkAAAAAAAAAJATiAHsCAAAAb+4E0XicY2AAA2YG1sSCgpxUBga2pMQ8IGSAAhUo'
    'rQ0Ab+4E0QIAAAB4RQareJxjYICAxIKCnFQom4EHhSeZlJgHhAwAeEUGqwAAAAAA'
    'AAABAAAAAAAAAAMAAAAAAAAAEAAAAAAAAAAyAAAAAAAAADIAAAAAAAAAJQIAAAD0'
    'jw1ZeJyzSbJLyywqLrHRT7KzSbIrTk3Oz0sBcSpTc3LyyxXSikozSwD0jw1Z';
