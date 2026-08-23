import '../../core/capabilities.dart';
import '../../core/conflict.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import 'adapter_contracts.dart';

class InMemoryProgressStore implements ProgressStore {
  final Map<String, ProgressRecord> _records = <String, ProgressRecord>{};
  final List<ProgressRecord> _pending = <ProgressRecord>[];

  @override
  String get providerId => 'in-memory-progress-store';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.progressStorage],
  );

  @override
  Future<ProgressRecord?> get(String bookId) async => _records[bookId];

  @override
  Future<void> put(ProgressRecord record) async {
    _records[record.bookId] = record;
    _pending.add(record);
  }

  @override
  Future<void> delete(String bookId, ProgressRecord tombstone) async {
    if (!tombstone.tombstone || tombstone.bookId != bookId) {
      throw ArgumentError('delete requires a matching tombstone');
    }
    await put(tombstone);
  }

  @override
  Stream<ProgressRecord> pending() =>
      Stream<ProgressRecord>.fromIterable(_pending);
}

class InMemoryProgressTransport implements ProgressTransport {
  InMemoryProgressTransport({
    DateTime Function()? clock,
    ProgressConflictResolver resolver = const ProgressConflictResolver(),
  }) : _clock = clock ?? (() => DateTime.now().toUtc()),
       _resolver = resolver;

  final Map<String, ProgressRecord> _records = <String, ProgressRecord>{};
  final DateTime Function() _clock;
  final ProgressConflictResolver _resolver;

  @override
  String get providerId => 'in-memory-progress-transport';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.progressSync],
  );

  @override
  Future<SyncPage<ProgressRecord>> pull({String? cursor, int? limit}) async {
    final items = _records.values.toList()
      ..sort((a, b) => a.bookId.compareTo(b.bookId));
    final start = cursor == null ? 0 : int.tryParse(cursor);
    if (start == null || start < 0 || start > items.length) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cursor must identify a valid page boundary',
      );
    }
    if (limit != null && limit < 0) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Limit must be nonnegative',
      );
    }
    final end = limit == null
        ? items.length
        : (start + limit).clamp(0, items.length);
    return SyncPage<ProgressRecord>(
      items: items.sublist(start, end),
      nextCursor: end < items.length ? '$end' : null,
      serverTime: _clock(),
    );
  }

  @override
  Future<ProgressRecord> put(ProgressRecord record, {int? ifMatch}) async {
    final current = _records[record.bookId];
    if (current != null &&
        _sameProgress(current, record) &&
        ifMatch == (current.revision == 0 ? null : current.revision - 1)) {
      return current;
    }
    final validCreate =
        current == null && ifMatch == null && record.revision == 0;
    final validUpdate =
        current != null &&
        ifMatch == current.revision &&
        record.revision == current.revision + 1;
    if (!validCreate && !validUpdate) {
      throw const CoreException(
        CoreErrorCode.revisionConflict,
        'Revision precondition failed',
      );
    }
    final winner = current == null
        ? record
        : _resolver.merge(record, current).record;
    _records[record.bookId] = winner;
    return winner;
  }

  @override
  Future<ProgressRecord> delete(String bookId, {required int ifMatch}) async {
    final current = _records[bookId];
    if (current == null) throw StateError('Unknown book: $bookId');
    if (current.tombstone && current.revision == ifMatch + 1) return current;
    if (current.revision != ifMatch) {
      throw const CoreException(
        CoreErrorCode.revisionConflict,
        'If-Match does not match the current revision',
      );
    }
    final tombstone = ProgressRecord(
      bookId: current.bookId,
      documentVersion: current.documentVersion,
      location: current.location,
      percentage: current.percentage,
      deviceId: current.deviceId,
      updatedAt: _clock(),
      revision: current.revision + 1,
      tombstone: true,
    );
    _records[bookId] = tombstone;
    return tombstone;
  }

  bool _sameProgress(ProgressRecord left, ProgressRecord right) =>
      left.bookId == right.bookId &&
      left.documentVersion == right.documentVersion &&
      left.location == right.location &&
      left.percentage == right.percentage &&
      left.deviceId == right.deviceId &&
      left.updatedAt == right.updatedAt &&
      left.revision == right.revision &&
      left.tombstone == right.tombstone;
}
