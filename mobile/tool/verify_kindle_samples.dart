import 'dart:io';

import 'package:oohstory/adapters/formats/kindle_format_decoder.dart';

Future<void> main(List<String> paths) async {
  if (paths.isEmpty) throw ArgumentError('Pass one or more MOBI/AZW3 files');
  const decoder = KindleFormatDecoder();
  for (final path in paths) {
    final file = File(path);
    final result = await decoder.decodeBook(file.openRead());
    stdout.writeln(
      '${file.uri.pathSegments.last}: ${result.metadata.format} | '
      '${result.metadata.title} | ${result.document.sections.length} sections',
    );
  }
}
