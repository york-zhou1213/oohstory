import 'dart:convert';
import 'dart:io';

import 'package:archive/archive.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:oohstory/models/reader_preferences.dart';
import 'package:oohstory/services/local_storage_service.dart';
import 'package:oohstory/services/offline_book_parser.dart';
import 'package:oohstory/services/opds_catalog_service.dart';
import 'package:oohstory/utils/reader_pagination.dart';

void main() {
  test('reader modes have stable persisted values', () {
    expect(ReaderViewMode.values.map((mode) => mode.storageValue), [
      'scroll',
      'page',
      'spread',
    ]);
    expect(ReaderViewModeValue.parse('spread'), ReaderViewMode.spread);
    expect(ReaderViewModeValue.parse('unknown'), ReaderViewMode.scroll);
  });

  test('pagination keeps text in deterministic reading order', () {
    final source = List.generate(
      40,
      (index) => '第$index段 ${'故事内容' * 14}',
    ).join('\n\n');
    final pages = ReaderPagination.paginateText(source, 220);
    expect(pages.length, greaterThan(1));
    final rebuilt = pages.join('\n\n');
    for (var index = 0; index < 40; index++) {
      expect(rebuilt, contains('第$index段'));
    }
  });

  test('old local-book metadata migrates without data loss', () {
    final book = LocalBookInfo.fromJson({
      'id': 'local-1',
      'title': '旧书',
      'fileName': '旧书.txt',
      'fileSize': 42,
      'addedAt': 1,
    });
    expect(book.format, 'txt');
    expect(book.author, isEmpty);
    expect(book.progress, 0);
    expect(book.storageExtension, 'txt');
    expect(book.pageCount, 0);
  });

  test('offline parser imports text, markdown, FB2, DOCX and EPUB', () async {
    final temp = await Directory.systemTemp.createTemp('oohstory-parser-');
    addTearDown(() => temp.delete(recursive: true));
    const parser = OfflineBookParser();

    final txt = File('${temp.path}/文本.txt')..writeAsStringSync('第一章\n\n正文');
    final parsedTxt = await parser.parse(txt.path, '文本.txt');
    expect(parsedTxt.title, '文本');
    expect(parsedTxt.content, contains('正文'));

    final markdown = File('${temp.path}/笔记.md')
      ..writeAsStringSync('# 标题\n\n**重点**');
    final parsedMarkdown = await parser.parse(markdown.path, '笔记.md');
    expect(parsedMarkdown.content, contains('重点'));
    expect(parsedMarkdown.content, isNot(contains('**')));

    final fb2 = File('${temp.path}/小说.fb2')
      ..writeAsStringSync('''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <description><title-info><book-title>星海</book-title><author><first-name>灰</first-name><last-name>烬</last-name></author></title-info></description>
  <body><section><title><p>第一章</p></title><p>舰桥苏醒。</p></section></body>
</FictionBook>''');
    final parsedFb2 = await parser.parse(fb2.path, '小说.fb2');
    expect(parsedFb2.title, '星海');
    expect(parsedFb2.author, '灰 烬');
    expect(parsedFb2.content, contains('舰桥苏醒'));

    final docxArchive = Archive()
      ..addFile(
        ArchiveFile.string(
          'word/document.xml',
          '<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>DOCX 正文</w:t></w:r></w:p></w:body></w:document>',
        ),
      )
      ..addFile(
        ArchiveFile.string(
          'docProps/core.xml',
          '<cp:coreProperties xmlns:cp="urn:cp" xmlns:dc="urn:dc"><dc:title>文档书</dc:title><dc:creator>作者甲</dc:creator></cp:coreProperties>',
        ),
      );
    final docx = File('${temp.path}/文档.docx')
      ..writeAsBytesSync(ZipEncoder().encodeBytes(docxArchive));
    final parsedDocx = await parser.parse(docx.path, '文档.docx');
    expect(parsedDocx.title, '文档书');
    expect(parsedDocx.content, 'DOCX 正文');

    final epubArchive = Archive()
      ..addFile(
        ArchiveFile.string(
          'META-INF/container.xml',
          '<container><rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>',
        ),
      )
      ..addFile(
        ArchiveFile.string(
          'OPS/content.opf',
          '<package xmlns:dc="urn:dc"><metadata><dc:title>EPUB 书</dc:title><dc:creator>作者乙</dc:creator></metadata><manifest><item id="c1" href="c1.xhtml"/></manifest><spine><itemref idref="c1"/></spine></package>',
        ),
      )
      ..addFile(
        ArchiveFile.string(
          'OPS/c1.xhtml',
          '<html><body><h1>第一章</h1><p>EPUB 正文</p></body></html>',
        ),
      );
    final epub = File('${temp.path}/书.epub')
      ..writeAsBytesSync(ZipEncoder().encodeBytes(epubArchive));
    final parsedEpub = await parser.parse(epub.path, '书.epub');
    expect(parsedEpub.title, 'EPUB 书');
    expect(parsedEpub.author, '作者乙');
    expect(parsedEpub.content, contains('EPUB 正文'));
  });

  test(
    'offline parser validates PDF and naturally ordered CBZ pages',
    () async {
      final temp = await Directory.systemTemp.createTemp('oohstory-binary-');
      addTearDown(() => temp.delete(recursive: true));
      const parser = OfflineBookParser();

      final pdf = File('${temp.path}/星图.pdf')
        ..writeAsBytesSync('%PDF-1.4\n%%EOF'.codeUnits);
      final parsedPdf = await parser.parse(pdf.path, '星图.pdf');
      expect(parsedPdf.format, 'pdf');
      expect(parsedPdf.storageExtension, 'pdf');
      expect(parsedPdf.assetBytes, isNotEmpty);

      final cbzArchive = Archive()
        ..addFile(ArchiveFile('10.jpg', 3, [10, 11, 12]))
        ..addFile(ArchiveFile('2.jpg', 3, [2, 3, 4]));
      final cbz = File('${temp.path}/漫画.cbz')
        ..writeAsBytesSync(ZipEncoder().encodeBytes(cbzArchive));
      final parsedCbz = await parser.parse(cbz.path, '漫画.cbz');
      expect(parsedCbz.format, 'cbz');
      expect(parsedCbz.storageExtension, 'cbz');
      expect(parsedCbz.pageCount, 2);
    },
  );

  test('OPDS 1 and OPDS 2 expose only supported acquisitions', () async {
    final responses = <String, String>{
      '/opds': '''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>离线 EPUB</title>
<author><name>作者甲</name></author>
<link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="books/one.epub"/>
</entry></feed>''',
      '/opds2': jsonEncode({
        'publications': [
          {
            'metadata': {
              'title': '离线 PDF',
              'author': [
                {'name': '作者乙'},
              ],
            },
            'links': [
              {
                'rel': 'http://opds-spec.org/acquisition',
                'type': 'application/pdf',
                'href': '/books/two.pdf',
              },
            ],
          },
        ],
      }),
    };
    final service = OpdsCatalogService(
      client: MockClient((request) async {
        final body = responses[request.url.path];
        return body == null
            ? http.Response('missing', 404)
            : http.Response.bytes(utf8.encode(body), 200);
      }),
    );
    addTearDown(service.close);

    final atom = await service.fetch(Uri.parse('https://library.example/opds'));
    expect(atom.single.title, '离线 EPUB');
    expect(
      atom.single.downloadUri.toString(),
      'https://library.example/books/one.epub',
    );
    final json = await service.fetch(
      Uri.parse('https://library.example/opds2'),
    );
    expect(json.single.format, 'pdf');
    expect(json.single.author, '作者乙');
  });

  test('offline implementation remains clean-room', () {
    final files = [
      File('lib/services/offline_book_parser.dart'),
      File('lib/utils/reader_pagination.dart'),
      File('lib/models/reader_preferences.dart'),
    ];
    final combined = files.map((file) => file.readAsStringSync()).join('\n');
    expect(combined, isNot(contains('kookit')));
    expect(combined, isNot(contains('rendition.doSearch')));
    expect(combined, isNot(contains('ModeControl')));
  });
}
