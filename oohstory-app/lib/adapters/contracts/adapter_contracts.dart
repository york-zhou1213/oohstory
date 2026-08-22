import 'dart:typed_data';

import '../../core/capabilities.dart';
import '../../core/models.dart';

abstract interface class Adapter {
  String get providerId;
  ProviderCapabilities get capabilities;
}

abstract interface class BookSourceAdapter implements Adapter {
  Future<bool> probe(BookSource source);
  Future<BookDescriptor> open(BookSource source);
  Stream<List<int>> read(BookSource source);
}

abstract interface class FormatDecoder implements Adapter {
  Future<bool> probe(String mediaType, List<int> header);
  Future<DecodedDocument> decode(Stream<List<int>> bytes);
}

abstract interface class DictionaryAdapter implements Adapter {
  Future<List<DictionaryEntry>> lookup(String term, {String? locale});
}

abstract interface class OcrAdapter implements Adapter {
  Future<OcrResult> recognize(Uint8List imageBytes, {String? locale});
}

abstract interface class ProgressStore implements Adapter {
  Future<ProgressRecord?> get(String bookId);
  Future<void> put(ProgressRecord record);
  Future<void> delete(String bookId, ProgressRecord tombstone);
  Stream<ProgressRecord> pending();
}

abstract interface class ProgressTransport implements Adapter {
  Future<SyncPage<ProgressRecord>> pull({String? cursor, int? limit});
  Future<ProgressRecord> put(ProgressRecord record, {int? ifMatch});
  Future<ProgressRecord> delete(String bookId, {required int ifMatch});
}

abstract interface class CloudLibraryAdapter implements Adapter {
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor});
  Future<CloudEntry> stat(String path);
  Stream<List<int>> read(String path);
  Future<CloudEntry> write(
    String path,
    Stream<List<int>> bytes, {
    String? etag,
  });
  Future<void> delete(String path, {String? etag});
}

abstract interface class AnnotationSink implements Adapter {
  Future<void> export(Annotation annotation, {required String idempotencyKey});
}

abstract interface class UpdateChannelAdapter implements Adapter {
  Future<UpdateManifest> fetchManifest();
  Future<bool> verifyManifest(UpdateManifest manifest);
  Future<void> handoff(UpdateManifest manifest);
}
