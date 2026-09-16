import 'dart:convert';

import '../../core/models.dart';

class AnnotationMarkdownRenderer {
  const AnnotationMarkdownRenderer();

  String render(DocumentIdentity document, List<Annotation> annotations) {
    final ordered = [...annotations]
      ..sort((left, right) {
        final time = (left.createdAt ?? DateTime.fromMillisecondsSinceEpoch(0))
            .compareTo(
              right.createdAt ?? DateTime.fromMillisecondsSinceEpoch(0),
            );
        return time != 0 ? time : left.id.compareTo(right.id);
      });
    final latest = ordered
        .map((item) => item.createdAt?.toUtc())
        .whereType<DateTime>()
        .fold<DateTime?>(
          null,
          (value, item) => value == null || item.isAfter(value) ? item : value,
        );
    final output = StringBuffer()
      ..writeln('---')
      ..writeln('oohstory_schema: 1')
      ..writeln('source: ${jsonEncode('OOHStory')}')
      ..writeln('book_id: ${jsonEncode(document.id)}')
      ..writeln('document_version: ${jsonEncode(document.documentVersion)}')
      ..writeln('title: ${jsonEncode(singleLine(document.title))}')
      ..writeln('author: ${jsonEncode(singleLine(document.author))}')
      ..writeln('annotation_count: ${ordered.length}')
      ..writeln(
        'updated_at: ${jsonEncode((latest ?? DateTime.fromMillisecondsSinceEpoch(0, isUtc: true)).toIso8601String())}',
      )
      ..writeln('tags:')
      ..writeln('  - oohstory')
      ..writeln('  - reading-notes')
      ..writeln('---')
      ..writeln()
      ..writeln(
        '# ${singleLine(document.title).isEmpty ? '未命名书籍' : singleLine(document.title)}',
      )
      ..writeln();
    if (document.author.trim().isNotEmpty) {
      output
        ..writeln('作者：${singleLine(document.author)}')
        ..writeln();
    }
    for (final annotation in ordered) {
      final label = switch (annotation.type) {
        'note' => '笔记',
        'bookmark' => '书签',
        _ => '高亮',
      };
      output
        ..writeln('## $label · ${percentage(annotation.location)}')
        ..writeln()
        ..writeln('<!-- oohstory-annotation:${jsonEncode(annotation.id)} -->');
      if (annotation.createdAt != null) {
        output.writeln(
          '创建时间：${annotation.createdAt!.toUtc().toIso8601String()}',
        );
      }
      output
        ..writeln()
        ..writeln(
          annotation.text
              .replaceAll('\r\n', '\n')
              .replaceAll('\r', '\n')
              .split('\n')
              .map((line) => '> $line')
              .join('\n'),
        )
        ..writeln();
      if (annotation.note?.trim().isNotEmpty ?? false) {
        output
          ..writeln(annotation.note!.trim())
          ..writeln();
      }
    }
    return output.toString();
  }

  static String percentage(String location) {
    if (!location.startsWith('progress:')) return location;
    final progress = double.tryParse(location.substring('progress:'.length));
    return progress == null ? location : '${(progress * 100).round()}%';
  }

  static String singleLine(String value) =>
      value.replaceAll(RegExp(r'[\r\n\t]+'), ' ').trim();
}
