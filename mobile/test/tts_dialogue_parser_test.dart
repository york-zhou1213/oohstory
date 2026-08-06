import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/services/tts_dialogue_parser.dart';

void main() {
  group('TtsDialogueParser', () {
    test('recognizes every supported dialogue delimiter', () {
      const dialogueLines = [
        '“你终于来了。”',
        '"你终于来了。"',
        '「你终于来了。」',
        '『你终于来了。』',
        '【你终于来了。】',
        '[你终于来了。]',
        '［你终于来了。］',
        '林夏：你终于来了。',
        '林夏: 你终于来了。',
      ];

      for (final line in dialogueLines) {
        expect(TtsDialogueParser.isDialogueLine(line), isTrue, reason: line);
      }
      expect(TtsDialogueParser.isDialogueLine('雨还在下，街上没有人。'), isFalse);
    });

    test('splits narration and quoted dialogue without delimiters', () {
      final segments = TtsDialogueParser.splitLine('他皱起眉：“立刻离开。”随后关上门。');

      expect(segments.map((item) => item.text), ['他皱起眉：', '立刻离开。', '随后关上门。']);
      expect(segments.map((item) => item.isDialogue), [false, true, false]);
    });

    test('treats square brackets and role colon content as dialogue', () {
      final bracket = TtsDialogueParser.splitLine('[别过来。]');
      expect(bracket.single.text, '别过来。');
      expect(bracket.single.isDialogue, isTrue);

      final role = TtsDialogueParser.splitLine('林夏：别过来。');
      expect(role.map((item) => item.text), ['林夏', '别过来。']);
      expect(role.map((item) => item.isDialogue), [false, true]);
    });
  });
}
