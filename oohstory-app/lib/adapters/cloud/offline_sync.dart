import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import '../../core/errors.dart';
import '../contracts/adapter_contracts.dart';
import 'cloud_support.dart';

enum CloudMutationType { write, delete }

final class QueuedCloudMutation {
  const QueuedCloudMutation({
    required this.idempotencyKey,
    required this.type,
    required this.path,
    required this.etag,
    required this.bytes,
    required this.enqueuedAt,
  });

  final String idempotencyKey;
  final CloudMutationType type;
  final String path;
  final String? etag;
  final Uint8List bytes;
  final DateTime enqueuedAt;

  Map<String, Object?> toJson() => <String, Object?>{
    'idempotency_key': idempotencyKey,
    'type': type.name,
    'path': path,
    'etag': etag,
    'bytes': base64Encode(bytes),
    'enqueued_at': enqueuedAt.toUtc().toIso8601String(),
  };

  factory QueuedCloudMutation.fromJson(Map<String, Object?> json) {
    try {
      return QueuedCloudMutation(
        idempotencyKey: json['idempotency_key'] as String,
        type: CloudMutationType.values.byName(json['type'] as String),
        path: json['path'] as String,
        etag: json['etag'] as String?,
        bytes: base64Decode(json['bytes'] as String),
        enqueuedAt: DateTime.parse(json['enqueued_at'] as String).toUtc(),
      );
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Offline cloud mutation is invalid',
      );
    }
  }
}

abstract interface class OfflineMutationStore {
  Future<void> put(QueuedCloudMutation mutation);

  /// Returns mutations in their original enqueue order.
  Future<List<QueuedCloudMutation>> pending();
  Future<void> remove(String idempotencyKey);
}

final class InMemoryOfflineMutationStore implements OfflineMutationStore {
  final Map<String, QueuedCloudMutation> _mutations =
      <String, QueuedCloudMutation>{};

  @override
  Future<void> put(QueuedCloudMutation mutation) async {
    _mutations.putIfAbsent(mutation.idempotencyKey, () => mutation);
  }

  @override
  Future<List<QueuedCloudMutation>> pending() async =>
      _mutations.values.toList(growable: false);

  @override
  Future<void> remove(String idempotencyKey) async {
    _mutations.remove(idempotencyKey);
  }
}

final class OfflineCloudSynchronizer {
  OfflineCloudSynchronizer({
    required this.adapter,
    required this.store,
    this.cancellationToken,
    this.maxQueuedBytes = 128 * 1024 * 1024,
    DateTime Function()? clock,
  }) : _clock = clock ?? (() => DateTime.now().toUtc());

  final CloudLibraryAdapter adapter;
  final OfflineMutationStore store;
  final CancellationToken? cancellationToken;
  final int maxQueuedBytes;
  final DateTime Function() _clock;
  static final CloudRoot _pathValidator = CloudRoot('OOHStory');

  Future<String> enqueueWrite(
    String path,
    Stream<List<int>> bytes, {
    String? etag,
  }) async {
    final normalizedPath = _pathValidator.requireDescendant(path);
    final payload = await collectBytes(
      bytes,
      maxBytes: maxQueuedBytes,
      cancellationToken: cancellationToken,
    );
    final key = _key(CloudMutationType.write, normalizedPath, etag, payload);
    await store.put(
      QueuedCloudMutation(
        idempotencyKey: key,
        type: CloudMutationType.write,
        path: normalizedPath,
        etag: etag,
        bytes: payload,
        enqueuedAt: _clock(),
      ),
    );
    return key;
  }

  Future<String> enqueueDelete(String path, {String? etag}) async {
    final normalizedPath = _pathValidator.requireDescendant(path);
    final key = _key(
      CloudMutationType.delete,
      normalizedPath,
      etag,
      const <int>[],
    );
    await store.put(
      QueuedCloudMutation(
        idempotencyKey: key,
        type: CloudMutationType.delete,
        path: normalizedPath,
        etag: etag,
        bytes: Uint8List(0),
        enqueuedAt: _clock(),
      ),
    );
    return key;
  }

  Future<int> replay() async {
    var applied = 0;
    for (final mutation in await store.pending()) {
      cancellationToken?.throwIfCancelled();
      final expectedKey = _key(
        mutation.type,
        _pathValidator.requireDescendant(mutation.path),
        mutation.etag,
        mutation.bytes,
      );
      if (mutation.bytes.length > maxQueuedBytes ||
          mutation.idempotencyKey != expectedKey) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Offline cloud mutation failed integrity validation',
        );
      }
      switch (mutation.type) {
        case CloudMutationType.write:
          await adapter.write(
            mutation.path,
            Stream<List<int>>.value(mutation.bytes),
            etag: mutation.etag,
          );
        case CloudMutationType.delete:
          await adapter.delete(mutation.path, etag: mutation.etag);
      }
      cancellationToken?.throwIfCancelled();
      await store.remove(mutation.idempotencyKey);
      applied++;
    }
    return applied;
  }

  String _key(
    CloudMutationType type,
    String path,
    String? etag,
    List<int> bytes,
  ) => sha256.convert(<int>[
    ...utf8.encode('${type.name}\u0000$path\u0000${etag ?? ''}\u0000'),
    ...bytes,
  ]).toString();
}
