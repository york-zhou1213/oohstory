import 'errors.dart';

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
    if (<String>[
      bookId,
      documentVersion,
      location,
    ].any((value) => value.trim().isEmpty)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress identifiers and location must not be blank',
      );
    }
    if (!_uuid.hasMatch(deviceId)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Device ID must be a UUID',
      );
    }
    if (!percentage.isFinite || percentage < 0 || percentage > 1) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Percentage must be between 0 and 1',
      );
    }
    if (revision < 0) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Revision must be nonnegative',
      );
    }
    if (!updatedAt.isUtc) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Updated time must be UTC',
      );
    }
  }

  static final RegExp _uuid = RegExp(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
  );
  static final RegExp _utcTimestamp = RegExp(
    r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|\+00:00)$',
  );

  final String bookId;
  final String documentVersion;
  final String location;
  final double percentage;
  final String deviceId;
  final DateTime updatedAt;
  final int revision;
  final bool tombstone;

  factory ProgressRecord.fromJson(Map<String, Object?> json) {
    try {
      final timestamp = json['updated_at'] as String;
      final match = _utcTimestamp.firstMatch(timestamp);
      if (match == null) {
        throw const FormatException('Timestamp must be RFC3339 UTC');
      }
      final parsedTimestamp = DateTime.parse(timestamp);
      final fields = <int>[
        parsedTimestamp.year,
        parsedTimestamp.month,
        parsedTimestamp.day,
        parsedTimestamp.hour,
        parsedTimestamp.minute,
        parsedTimestamp.second,
      ];
      for (var index = 0; index < fields.length; index++) {
        if (fields[index] != int.parse(match.group(index + 1)!)) {
          throw const FormatException('Timestamp date is invalid');
        }
      }
      return ProgressRecord(
        bookId: json['book_id'] as String,
        documentVersion: json['document_version'] as String,
        location: json['location'] as String,
        percentage: (json['percentage'] as num).toDouble(),
        deviceId: json['device_id'] as String,
        updatedAt: parsedTimestamp,
        revision: json['revision'] as int,
        tombstone: json['tombstone'] as bool? ?? false,
      );
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress payload is invalid',
      );
    }
  }

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
