import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/errors.dart';
import 'package:oohstory/services/local_dictionary_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../dictionary/mdx_fixture.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory root;
  late SharedPreferences preferences;

  setUp(() async {
    root = await Directory.systemTemp.createTemp('oohstory-mdx-test-');
    SharedPreferences.setMockInitialValues(<String, Object>{});
    preferences = await SharedPreferences.getInstance();
  });

  tearDown(() async {
    if (await root.exists()) await root.delete(recursive: true);
  });

  LocalDictionaryService service() => LocalDictionaryService(
    documentsDirectory: () async => root,
    preferences: preferences,
  );

  test('persists dictionaries, resources, enablement and order', () async {
    final first = service();
    await first.init();
    final alpha = await first.import(
      name: 'Alpha.mdx',
      mdxBytes: buildMdxFixture(
        compression: 1,
        entries: const <MapEntry<String, String>>[
          MapEntry<String, String>('apple', 'alpha definition'),
        ],
      ),
      mddBytes: buildMdxFixture(
        entries: const <MapEntry<String, String>>[
          MapEntry<String, String>(r'\images\apple.png', 'image'),
        ],
      ),
    );
    final beta = await first.import(
      name: 'Beta.mdx',
      mdxBytes: buildMdxFixture(
        entries: const <MapEntry<String, String>>[
          MapEntry<String, String>('apple', 'beta definition'),
        ],
      ),
    );
    await first.move(beta.id, -1);
    await first.setEnabled(alpha.id, false);

    final restarted = service();
    await restarted.init();

    expect(restarted.list().map((item) => item.name), <String>[
      'Beta',
      'Alpha',
    ]);
    expect(restarted.list().last.enabled, isFalse);
    final results = await restarted.lookup('apple');
    expect(results.single.dictionaryName, 'Beta');
    expect(results.single.entries.single.definition, 'beta definition');
    expect(
      String.fromCharCodes(
        (await restarted.resource(alpha.id, '/IMAGES/apple.png'))!,
      ),
      'image',
    );
  });

  test('removes only app-owned copies and metadata', () async {
    final dictionary = service();
    await dictionary.init();
    final info = await dictionary.import(
      name: 'Remove.mdx',
      mdxBytes: buildMdxFixture(),
    );

    await dictionary.remove(info.id);

    expect(dictionary.list(), isEmpty);
    expect(
      Directory(
        '${root.path}/oohstory_dictionaries',
      ).listSync().whereType<File>(),
      isEmpty,
    );
  });

  test('detects a persisted dictionary changed outside the service', () async {
    final first = service();
    await first.init();
    final info = await first.import(
      name: 'Tamper.mdx',
      mdxBytes: buildMdxFixture(),
    );
    final file = File('${root.path}/oohstory_dictionaries/${info.id}.mdx');
    final bytes = await file.readAsBytes();
    bytes[bytes.length - 1] ^= 1;
    await file.writeAsBytes(bytes, flush: true);

    final restarted = service();
    await restarted.init();
    await expectLater(
      restarted.lookup('apple'),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.validationError,
        ),
      ),
    );
  });

  test('rejects empty and oversized imports before writing', () async {
    final dictionary = service();
    await dictionary.init();

    await expectLater(
      dictionary.import(name: 'Empty.mdx', mdxBytes: Uint8List(0)),
      throwsA(isA<CoreException>()),
    );
    expect(dictionary.list(), isEmpty);
  });
}
