class ProgressRecord {
  ProgressRecord({
    required this.bookId,
    required this.documentVersion,
    required this.location,
    required this.percentage,
    required this.deviceId,
    required this.updatedAt,
    required this.revision,
    this.tombstone = false,
  }) {
    if (bookId.isEmpty ||
        documentVersion.isEmpty ||
        location.isEmpty ||
        deviceId.isEmpty) {
      throw ArgumentError(
        'Progress identifiers and location must not be empty',
      );
    }
    if (!percentage.isFinite || percentage < 0 || percentage > 1) {
      throw RangeError.range(percentage, 0, 1, 'percentage');
    }
    if (revision < 0) {
      throw RangeError.value(revision, 'revision', 'Must be nonnegative');
    }
    if (!updatedAt.isUtc) {
      throw ArgumentError.value(updatedAt, 'updatedAt', 'Must be UTC');
    }
  }

  final String bookId;
  final String documentVersion;
  final String location;
  final double percentage;
  final String deviceId;
  final DateTime updatedAt;
  final int revision;
  final bool tombstone;

  factory ProgressRecord.fromJson(Map<String, Object?> json) => ProgressRecord(
    bookId: json['book_id'] as String,
    documentVersion: json['document_version'] as String,
    location: json['location'] as String,
    percentage: (json['percentage'] as num).toDouble(),
    deviceId: json['device_id'] as String,
    updatedAt: DateTime.parse(json['updated_at'] as String).toUtc(),
    revision: json['revision'] as int,
    tombstone: json['tombstone'] as bool? ?? false,
  );

  Map<String, Object?> toJson() => <String, Object?>{
    'book_id': bookId,
    'document_version': documentVersion,
    'location': location,
    'percentage': percentage,
    'device_id': deviceId,
    'updated_at': updatedAt.toIso8601String(),
    'revision': revision,
    'tombstone': tombstone,
  };
}

class SyncPage<T> {
  const SyncPage({
    required this.items,
    required this.nextCursor,
    required this.serverTime,
  });

  final List<T> items;
  final String? nextCursor;
  final DateTime serverTime;

  Map<String, Object?> toJson(Object? Function(T item) encode) =>
      <String, Object?>{
        'items': items.map(encode).toList(growable: false),
        'next_cursor': nextCursor,
        'server_time': serverTime.toUtc().toIso8601String(),
      };
}

class BookDescriptor {
  const BookDescriptor({
    required this.id,
    required this.title,
    required this.documentVersion,
    this.mediaType,
  });

  final String id;
  final String title;
  final String documentVersion;
  final String? mediaType;
}

class BookSource {
  const BookSource({required this.uri, this.providerId});

  final Uri uri;
  final String? providerId;
}

class DecodedDocument {
  const DecodedDocument({required this.version, required this.sections});

  final String version;
  final List<String> sections;
}

class DictionaryEntry {
  const DictionaryEntry({required this.term, required this.definition});

  final String term;
  final String definition;
}

class OcrResult {
  const OcrResult({required this.text, required this.confidence});

  final String text;
  final double confidence;
}

class CloudEntry {
  const CloudEntry({required this.path, required this.isDirectory, this.etag});

  final String path;
  final bool isDirectory;
  final String? etag;
}

class Annotation {
  const Annotation({
    required this.id,
    required this.bookId,
    required this.location,
    required this.text,
    this.note,
  });

  final String id;
  final String bookId;
  final String location;
  final String text;
  final String? note;
}

class UpdateManifest {
  const UpdateManifest({required this.version, required this.uri});

  final String version;
  final Uri uri;
}
