import 'models.dart';

enum MergeDecision {
  acceptLocal,
  keepRemote,
  documentVersionConflict,
  equalRevisionTie,
}

class MergeResult {
  const MergeResult(this.record, this.decision);

  final ProgressRecord record;
  final MergeDecision decision;
}

class ProgressConflictResolver {
  const ProgressConflictResolver();

  MergeResult merge(ProgressRecord local, ProgressRecord remote) {
    if (local.bookId != remote.bookId) {
      throw ArgumentError('Cannot merge progress for different books');
    }

    if (local.revision < remote.revision) {
      return MergeResult(remote, MergeDecision.keepRemote);
    }
    if (local.revision > remote.revision) {
      return MergeResult(local, MergeDecision.acceptLocal);
    }

    if (local.documentVersion != remote.documentVersion) {
      return MergeResult(remote, MergeDecision.documentVersionConflict);
    }

    if (local.tombstone != remote.tombstone) {
      final winner = local.tombstone ? local : remote;
      return MergeResult(winner, MergeDecision.equalRevisionTie);
    }

    if (local.percentage != remote.percentage) {
      final winner = local.percentage > remote.percentage ? local : remote;
      return MergeResult(winner, MergeDecision.equalRevisionTie);
    }

    // Deterministic final tie-break; updatedAt is compared only after revision,
    // document version, tombstone and percentage are equal.
    final timeOrder = local.updatedAt.compareTo(remote.updatedAt);
    if (timeOrder != 0) {
      final winner = timeOrder > 0 ? local : remote;
      return MergeResult(winner, MergeDecision.equalRevisionTie);
    }
    final localKey = '${local.deviceId}\u0000${local.location}';
    final remoteKey = '${remote.deviceId}\u0000${remote.location}';
    final winner = localKey.compareTo(remoteKey) > 0 ? local : remote;
    return MergeResult(winner, MergeDecision.equalRevisionTie);
  }

  ProgressRecord replayOffline(
    Iterable<ProgressRecord> pending,
    ProgressRecord remote,
  ) {
    var current = remote;
    for (final record in pending) {
      current = merge(record, current).record;
    }
    return current;
  }
}
