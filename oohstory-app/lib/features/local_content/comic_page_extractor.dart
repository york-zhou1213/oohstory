import 'dart:typed_data';

import '../../adapters/formats/comic_archive_decoder.dart';

class ExtractedComicPage {
  const ExtractedComicPage({required this.name, required this.bytes});

  final String name;
  final Uint8List bytes;
}

class ComicPageExtractor {
  const ComicPageExtractor();

  List<ExtractedComicPage> fromDecoded(DecodedComicArchive archive) =>
      <ExtractedComicPage>[
        for (final page in archive.pages)
          ExtractedComicPage(name: page.name, bytes: page.bytes),
      ];
}
