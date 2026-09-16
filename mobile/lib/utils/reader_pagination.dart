class ReaderPagination {
  const ReaderPagination._();

  static int estimatedCharactersPerPage({
    required double width,
    required double height,
    required double fontSize,
    required double lineHeight,
  }) {
    final safeWidth = (width - 48).clamp(180.0, 720.0);
    final safeHeight = (height - 140).clamp(220.0, 1200.0);
    final charsPerLine = (safeWidth / (fontSize * 1.03)).floor().clamp(8, 80);
    final lines = (safeHeight / (fontSize * lineHeight)).floor().clamp(8, 80);
    return (charsPerLine * lines * .88).floor().clamp(120, 4200);
  }

  static List<String> paginateText(String content, int targetCharacters) {
    final normalized = content
        .replaceAll('\r\n', '\n')
        .replaceAll('\r', '\n')
        .trim();
    if (normalized.isEmpty) return const [''];
    final paragraphs = normalized
        .split(RegExp(r'\n{2,}|\n'))
        .map((value) => value.trim())
        .where((value) => value.isNotEmpty)
        .toList();
    final pages = <String>[];
    var buffer = StringBuffer();
    var length = 0;
    for (final paragraph in paragraphs) {
      if (paragraph.length > targetCharacters) {
        if (length > 0) {
          pages.add(buffer.toString().trim());
          buffer = StringBuffer();
          length = 0;
        }
        var start = 0;
        while (start < paragraph.length) {
          final end = (start + targetCharacters).clamp(0, paragraph.length);
          pages.add(paragraph.substring(start, end));
          start = end;
        }
        continue;
      }
      if (length > 0 && length + paragraph.length + 2 > targetCharacters) {
        pages.add(buffer.toString().trim());
        buffer = StringBuffer();
        length = 0;
      }
      if (length > 0) buffer.writeln('\n');
      buffer.write(paragraph);
      length += paragraph.length + 2;
    }
    if (length > 0) pages.add(buffer.toString().trim());
    return pages.isEmpty ? const [''] : pages;
  }
}
