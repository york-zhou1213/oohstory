import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/services/dictionary_definition_sanitizer.dart';

void main() {
  const sanitizer = DictionaryDefinitionSanitizer();

  test('drops executable markup, remote URLs and unsafe CSS', () async {
    final result = await sanitizer.sanitize('''
      <script>alert(1)</script>
      <iframe src="https://evil.invalid"></iframe>
      <a href="https://evil.invalid" onclick="steal()">remote</a>
      <p style="color: red; background: url(https://evil.invalid/x); position: fixed">
        safe text
      </p>
      <img src="https://evil.invalid/x.png">
    ''');

    expect(result.html, isNot(contains('script')));
    expect(result.html, isNot(contains('iframe')));
    expect(result.html, isNot(contains('onclick')));
    expect(result.html, isNot(contains('https://')));
    expect(result.html, contains('color: red'));
    expect(result.html, isNot(contains('position')));
    expect(result.html, contains('safe text'));
  });

  test('keeps entry jumps and embeds only verified local images', () async {
    final png = Uint8List.fromList(<int>[
      0x89,
      0x50,
      0x4e,
      0x47,
      0x0d,
      0x0a,
      0x1a,
      0x0a,
    ]);
    final result = await sanitizer.sanitize(
      '<a href="entry://apple">Apple</a>'
      '<img src="/images/apple.png" alt="apple">'
      '<img src="../secret.png">',
      loadResource: (path) async => path == 'images/apple.png' ? png : null,
    );

    expect(result.html, contains('href="entry://apple"'));
    expect(result.html, contains('data:image/png;base64,'));
    expect(result.html, isNot(contains('secret')));
  });

  test(
    'collects local pronunciation resources without rendering audio HTML',
    () async {
      final result = await sanitizer.sanitize(
        '<audio><source src="audio/apple.mp3"></audio>'
        '<audio src="/audio/apple.ogg"></audio>',
      );

      expect(result.html, isEmpty);
      expect(result.audioResources, <String>[
        'audio/apple.mp3',
        'audio/apple.ogg',
      ]);
    },
  );

  test('sandboxes inline and MDD-hosted styles and sound links', () async {
    final result = await sanitizer.sanitize(
      '<style>.word { color: blue; position: fixed }</style>'
      '<link rel="stylesheet" href="styles/main.css">'
      '<p class="word" id="entry">word</p>'
      '<a href="sound://audio/word.mp3">pronounce</a>',
      loadResource: (path) async => path == 'styles/main.css'
          ? Uint8List.fromList(
              '.word { font-weight: 700; background: url(evil) }'.codeUnits,
            )
          : null,
    );

    expect(result.html, contains('.word {color: blue}'));
    expect(result.html, contains('class="word"'));
    expect(result.html, contains('id="entry"'));
    expect(result.html, isNot(contains('position')));
    expect(result.html, isNot(contains('url(')));
    expect(result.html, contains('href="audio://audio%2Fword.mp3"'));
    expect(result.audioResources, contains('audio/word.mp3'));
  });
}
