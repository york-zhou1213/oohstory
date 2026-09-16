import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../adapters/contracts/adapter_contracts.dart';
import '../adapters/progress/progress.dart';
import '../core/core.dart';

class PendingProgress {
  const PendingProgress({required this.record, required this.baseRevision});

  final ProgressRecord record;
  final int? baseRevision;

  Map<String, Object?> toJson() => <String, Object?>{
    'record': record.toJson(),
    'base_revision': baseRevision,
  };

  factory PendingProgress.fromJson(Map<String, Object?> value) {
    final baseRevision = value['base_revision'];
    if (baseRevision != null && (baseRevision is! int || baseRevision < 0)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Pending progress base revision is invalid',
      );
    }
    return PendingProgress(
      record: ProgressRecord.fromJson(
        Map<String, Object?>.from(value['record']! as Map),
      ),
      baseRevision: baseRevision as int?,
    );
  }
}

class ProgressSyncConflict {
  const ProgressSyncConflict({required this.local, required this.remote});

  final PendingProgress local;
  final ProgressRecord? remote;
}

class ProgressSyncSummary {
  const ProgressSyncSummary({
    required this.uploaded,
    required this.remoteRecords,
    required this.conflicts,
    required this.pending,
  });

  final int uploaded;
  final List<ProgressRecord> remoteRecords;
  final List<ProgressSyncConflict> conflicts;
  final int pending;
}

/// Account-partitioned, persistent outbox for the progress v1 transport.
///
/// Only progress metadata is stored here. Authentication material stays in the
/// platform secure store and is requested by the transport for each call.
class ProgressSyncQueue {
  ProgressSyncQueue({
    required this.preferences,
    required this.accountId,
    required this.transport,
    this.maxPendingBooks = 500,
    this.maxPullPages = 100,
  }) : _scope = sha256.convert(utf8.encode(accountId)).toString() {
    if (accountId.trim().isEmpty || accountId.length > 256) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress sync account scope is invalid',
      );
    }
    if (maxPendingBooks < 1 || maxPullPages < 1) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress sync queue limits must be positive',
      );
    }
  }

  final SharedPreferences preferences;
  final String accountId;
  final ProgressTransport transport;
  final int maxPendingBooks;
  final int maxPullPages;
  final String _scope;

  String get _pendingKey => 'oohstory_progress_sync_pending_v1_$_scope';
  String get _revisionKey => 'oohstory_progress_sync_revisions_v1_$_scope';

  Future<void> enqueue(ProgressRecord record) async {
    final pending = _loadPending();
    final revisions = _loadRevisions();
    final existing = pending[record.bookId];
    final baseRevision = existing?.baseRevision ?? revisions[record.bookId];
    final normalized = ProgressRecord(
      bookId: record.bookId,
      documentVersion: record.documentVersion,
      location: record.location,
      percentage: record.percentage,
      deviceId: record.deviceId,
      updatedAt: record.updatedAt.toUtc(),
      revision: baseRevision == null ? 0 : baseRevision + 1,
      tombstone: record.tombstone,
    );
    if (existing == null && pending.length >= maxPendingBooks) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Progress sync queue is full',
      );
    }
    pending[record.bookId] = PendingProgress(
      record: normalized,
      baseRevision: baseRevision,
    );
    await _savePending(pending);
  }

  Future<ProgressSyncSummary> synchronize() async {
    final latest = await _pullLatest();
    final revisions = _loadRevisions()
      ..addAll(<String, int>{
        for (final entry in latest.entries) entry.key: entry.value.revision,
      });
    await _saveRevisions(revisions);

    final snapshot = _loadPending();
    final conflicts = <ProgressSyncConflict>[];
    var uploaded = 0;
    for (final entry in snapshot.entries) {
      final pending = entry.value;
      final remote = latest[entry.key];
      if (pending.baseRevision != remote?.revision) {
        conflicts.add(ProgressSyncConflict(local: pending, remote: remote));
        continue;
      }
      final target = ProgressRecord(
        bookId: pending.record.bookId,
        documentVersion: pending.record.documentVersion,
        location: pending.record.location,
        percentage: pending.record.percentage,
        deviceId: pending.record.deviceId,
        updatedAt: pending.record.updatedAt,
        revision: remote == null ? 0 : remote.revision + 1,
        tombstone: pending.record.tombstone,
      );
      if (target.tombstone && remote == null) {
        conflicts.add(ProgressSyncConflict(local: pending, remote: null));
        continue;
      }
      try {
        final stored = target.tombstone
            ? await transport.delete(target.bookId, ifMatch: remote!.revision)
            : await transport.put(target, ifMatch: remote?.revision);
        latest[stored.bookId] = stored;
        revisions[stored.bookId] = stored.revision;
        await _removeIfUnchanged(entry.key, pending);
        uploaded++;
      } on ProgressTransportException catch (error) {
        if (error.code != CoreErrorCode.revisionConflict) rethrow;
        conflicts.add(
          ProgressSyncConflict(local: pending, remote: error.current ?? remote),
        );
      }
    }
    await _saveRevisions(revisions);
    return ProgressSyncSummary(
      uploaded: uploaded,
      remoteRecords: List<ProgressRecord>.unmodifiable(latest.values),
      conflicts: List<ProgressSyncConflict>.unmodifiable(conflicts),
      pending: _loadPending().length,
    );
  }

  Future<void> acceptRemote(String bookId, ProgressRecord remote) async {
    if (bookId != remote.bookId) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Remote progress does not match the queued book',
      );
    }
    final pending = _loadPending()..remove(bookId);
    final revisions = _loadRevisions()..[bookId] = remote.revision;
    await _savePending(pending);
    await _saveRevisions(revisions);
  }

  Future<void> retryLocal(String bookId, ProgressRecord currentRemote) async {
    final pending = _loadPending();
    final local = pending[bookId];
    if (local == null || currentRemote.bookId != bookId) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress conflict is no longer pending',
      );
    }
    pending[bookId] = PendingProgress(
      record: ProgressRecord(
        bookId: local.record.bookId,
        documentVersion: local.record.documentVersion,
        location: local.record.location,
        percentage: local.record.percentage,
        deviceId: local.record.deviceId,
        updatedAt: local.record.updatedAt,
        revision: currentRemote.revision + 1,
        tombstone: local.record.tombstone,
      ),
      baseRevision: currentRemote.revision,
    );
    await _savePending(pending);
  }

  int get pendingCount => _loadPending().length;

  Future<Map<String, ProgressRecord>> _pullLatest() async {
    final latest = <String, ProgressRecord>{};
    final seenCursors = <String>{};
    String? cursor;
    for (var pageNumber = 0; pageNumber < maxPullPages; pageNumber++) {
      final page = await transport.pull(cursor: cursor, limit: 100);
      for (final record in page.items) {
        final current = latest[record.bookId];
        if (current == null || record.revision > current.revision) {
          latest[record.bookId] = record;
        }
      }
      final next = page.nextCursor;
      if (next == null) return latest;
      if (next.isEmpty || !seenCursors.add(next)) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Progress server returned a cyclic cursor',
        );
      }
      cursor = next;
    }
    throw const CoreException(
      CoreErrorCode.payloadTooLarge,
      'Progress history exceeds the configured page limit',
    );
  }

  Map<String, PendingProgress> _loadPending() {
    final raw = preferences.getString(_pendingKey);
    if (raw == null) return <String, PendingProgress>{};
    try {
      final value = jsonDecode(raw);
      if (value is! Map) throw const FormatException();
      return <String, PendingProgress>{
        for (final entry in value.entries)
          entry.key as String: PendingProgress.fromJson(
            Map<String, Object?>.from(entry.value as Map),
          ),
      };
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Stored progress sync queue is invalid',
      );
    }
  }

  Map<String, int> _loadRevisions() {
    final raw = preferences.getString(_revisionKey);
    if (raw == null) return <String, int>{};
    try {
      final value = jsonDecode(raw);
      if (value is! Map) throw const FormatException();
      return <String, int>{
        for (final entry in value.entries)
          entry.key as String: entry.value as int,
      };
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Stored progress revisions are invalid',
      );
    }
  }

  Future<void> _removeIfUnchanged(
    String bookId,
    PendingProgress expected,
  ) async {
    final pending = _loadPending();
    final current = pending[bookId];
    if (current != null &&
        current.baseRevision == expected.baseRevision &&
        current.record.updatedAt == expected.record.updatedAt &&
        current.record.location == expected.record.location) {
      pending.remove(bookId);
      await _savePending(pending);
    }
  }

  Future<void> _savePending(Map<String, PendingProgress> value) async {
    await preferences.setString(
      _pendingKey,
      jsonEncode(<String, Object?>{
        for (final entry in value.entries) entry.key: entry.value.toJson(),
      }),
    );
  }

  Future<void> _saveRevisions(Map<String, int> value) async {
    await preferences.setString(
      _revisionKey,
      jsonEncode(<String, int>{
        for (final entry in value.entries) entry.key: entry.value,
      }),
    );
  }
}
