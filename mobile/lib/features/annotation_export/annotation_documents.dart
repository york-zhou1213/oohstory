import 'dart:typed_data';

import '../../core/models.dart';
import '../../models/reader_preferences.dart';
import '../../services/local_storage_service.dart';

final class AnnotationAttachmentProvenance {
  const AnnotationAttachmentProvenance({
    required this.source,
    required this.sourceId,
  });

  final String source;
  final String sourceId;
}

final class AnnotationExportAttachment {
  AnnotationExportAttachment({
    required this.id,
    required this.bookId,
    required this.annotationId,
    required this.fileName,
    required this.mediaType,
    required List<int> bytes,
    required this.provenance,
  }) : bytes = Uint8List.fromList(bytes).asUnmodifiableView();

  final String id;
  final String bookId;
  final String annotationId;
  final String fileName;
  final String mediaType;
  final Uint8List bytes;
  final AnnotationAttachmentProvenance provenance;
}

class AnnotationExportDocument {
  const AnnotationExportDocument({
    required this.identity,
    required this.annotations,
    this.attachments = const <AnnotationExportAttachment>[],
  });

  final DocumentIdentity identity;
  final List<Annotation> annotations;
  final List<AnnotationExportAttachment> attachments;
}

class StoredAnnotationExportSource {
  const StoredAnnotationExportSource._();

  static List<AnnotationExportDocument> documentsFrom(
    LocalStorageService storage,
  ) {
    final annotationsByBook = <String, List<OfflineAnnotation>>{};
    for (final annotation in storage.getAnnotations()) {
      annotationsByBook
          .putIfAbsent(annotation.bookId, () => <OfflineAnnotation>[])
          .add(annotation);
    }
    final localBooks = <String, LocalBookInfo>{
      for (final book in storage.getLocalBooks()) book.id: book,
    };
    final downloadedBooks = <String, BookMeta>{
      for (final item in storage.getDownloadedBooks()) item.book.id: item.book,
    };
    final historyBooks = <String, BookMeta>{
      for (final item in storage.getHistory()) item.book.id: item.book,
    };
    final documents = <AnnotationExportDocument>[];
    for (final entry in annotationsByBook.entries) {
      final local = localBooks[entry.key];
      final remote = downloadedBooks[entry.key] ?? historyBooks[entry.key];
      final title = local?.title.trim().isNotEmpty == true
          ? local!.title
          : remote?.title.trim().isNotEmpty == true
          ? remote!.title
          : entry.key;
      final author = local?.author.trim().isNotEmpty == true
          ? local!.author
          : remote?.author ?? '';
      final version = local == null
          ? entry.key
          : '${local.id}:${local.format}:${local.fileSize}';
      documents.add(
        AnnotationExportDocument(
          identity: DocumentIdentity(
            id: entry.key,
            title: title,
            author: author,
            documentVersion: version,
          ),
          annotations: entry.value
              .map(
                (item) => Annotation(
                  id: item.id,
                  bookId: item.bookId,
                  location: 'progress:${item.progress.toStringAsFixed(6)}',
                  text: item.excerpt,
                  note: item.note,
                  type: item.type,
                  createdAt: DateTime.fromMillisecondsSinceEpoch(
                    item.createdAt,
                    isUtc: true,
                  ),
                ),
              )
              .toList(growable: false),
        ),
      );
    }
    documents.sort(
      (left, right) => left.identity.title.compareTo(right.identity.title),
    );
    return List<AnnotationExportDocument>.unmodifiable(documents);
  }

  static Future<List<AnnotationExportDocument>> documentsWithAttachmentsFrom(
    LocalStorageService storage,
  ) async {
    final documents = documentsFrom(storage);
    final attachmentsByBook = <String, List<OfflineAnnotationAttachment>>{};
    for (final attachment in storage.getAnnotationAttachments()) {
      attachmentsByBook
          .putIfAbsent(attachment.bookId, () => <OfflineAnnotationAttachment>[])
          .add(attachment);
    }
    final enriched = <AnnotationExportDocument>[];
    for (final document in documents) {
      final annotationIds = document.annotations.map((item) => item.id).toSet();
      final attachments = <AnnotationExportAttachment>[];
      for (final attachment
          in attachmentsByBook[document.identity.id] ??
              const <OfflineAnnotationAttachment>[]) {
        if (!annotationIds.contains(attachment.annotationId)) continue;
        attachments.add(
          AnnotationExportAttachment(
            id: attachment.id,
            bookId: attachment.bookId,
            annotationId: attachment.annotationId,
            fileName: attachment.fileName,
            mediaType: attachment.mediaType,
            bytes: await storage.readAnnotationAttachment(attachment),
            provenance: AnnotationAttachmentProvenance(
              source: 'oohstory.local-attachment',
              sourceId: attachment.id,
            ),
          ),
        );
      }
      enriched.add(
        AnnotationExportDocument(
          identity: document.identity,
          annotations: document.annotations,
          attachments: List<AnnotationExportAttachment>.unmodifiable(
            attachments,
          ),
        ),
      );
    }
    return List<AnnotationExportDocument>.unmodifiable(enriched);
  }
}
