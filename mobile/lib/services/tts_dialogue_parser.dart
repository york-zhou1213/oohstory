class TtsDialogueSegment {
  final String text;
  final bool isDialogue;

  const TtsDialogueSegment(this.text, {required this.isDialogue});
}

class TtsDialogueParser {
  static final quotePattern = RegExp(
    r'“[^”]*”|"[^"]*"|「[^」]*」|『[^』]*』|【[^】]*】|\[[^\]]*\]|［[^］]*］',
  );

  static final _roleColonPattern = RegExp(
    r'^\s*[^，。！？；：:\[\]［］【】“”「」『』]{1,15}[：:]\s*\S{2,}',
  );

  static int roleColonIndex(String line) {
    if (!_roleColonPattern.hasMatch(line)) return -1;
    final chinese = line.indexOf('：');
    final ascii = line.indexOf(':');
    if (chinese < 0) return ascii;
    if (ascii < 0) return chinese;
    return chinese < ascii ? chinese : ascii;
  }

  static bool isDialogueLine(String line) {
    return quotePattern.hasMatch(line) || roleColonIndex(line) > 0;
  }

  static String narrationOnly(String line) {
    return line.replaceAll(quotePattern, '');
  }

  static List<TtsDialogueSegment> splitLine(String line) {
    final matches = quotePattern.allMatches(line).toList();
    if (matches.isNotEmpty) {
      final segments = <TtsDialogueSegment>[];
      var last = 0;
      for (final match in matches) {
        if (match.start > last) {
          _append(segments, line.substring(last, match.start), false);
        }
        final wrapped = match.group(0)!;
        _append(segments, wrapped.substring(1, wrapped.length - 1), true);
        last = match.end;
      }
      if (last < line.length) {
        _append(segments, line.substring(last), false);
      }
      return segments;
    }

    final colon = roleColonIndex(line);
    if (colon > 0) {
      final segments = <TtsDialogueSegment>[];
      _append(segments, line.substring(0, colon), false);
      _append(segments, line.substring(colon + 1), true);
      return segments;
    }

    final text = line.trim();
    return text.isEmpty
        ? const []
        : [TtsDialogueSegment(text, isDialogue: false)];
  }

  static void _append(
    List<TtsDialogueSegment> segments,
    String text,
    bool isDialogue,
  ) {
    final cleaned = text.trim();
    if (cleaned.isNotEmpty) {
      segments.add(TtsDialogueSegment(cleaned, isDialogue: isDialogue));
    }
  }
}
