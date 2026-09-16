import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:path_provider/path_provider.dart';

import '../../core/errors.dart';
import 'offline_sync.dart';
import 'secure_credentials.dart';

const _masterKeyName = 'oohstory.cloud.offline_queue.master_key.v1';
const _magic = <int>[0x4f, 0x4f, 0x48, 0x51, 0x01];
const _nonceLength = 12;
const _macLength = 16;
const _maxRecordBytes = 129 * 1024 * 1024;

Future<OfflineMutationStore> createPersistentOfflineMutationStore({
  required SecureCredentialStore credentialStore,
  String? rootPath,
}) async {
  final root = rootPath == null
      ? Directory(
          '${(await getApplicationSupportDirectory()).path}'
          '${Platform.pathSeparator}cloud-mutation-queue-v1',
        )
      : Directory(rootPath);
  return EncryptedFileOfflineMutationStore(
    root: root,
    credentialStore: credentialStore,
  );
}

/// Native durable queue with one authenticated encrypted record per mutation.
///
/// Queue payloads never enter ordinary preferences. The AES-256-GCM key is stored
/// by the operating system credential backend, while ciphertext is staged in
/// the application-support directory and partitioned by provider/account hash.
final class EncryptedFileOfflineMutationStore implements OfflineMutationStore {
  EncryptedFileOfflineMutationStore({
    required Directory root,
    required SecureCredentialStore credentialStore,
  }) : _root = root,
       _credentialStore = credentialStore;

  final Directory _root;
  final SecureCredentialStore _credentialStore;
  final AesGcm _cipher = AesGcm.with256bits();
  final Random _random = Random.secure();
  Future<SecretKey>? _keyFuture;

  @override
  Future<void> put(QueuedCloudMutation mutation) async {
    _validateIdempotencyKey(mutation.idempotencyKey);
    if (mutation.bytes.length > _maxRecordBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Offline cloud mutation exceeds the durable queue limit',
      );
    }
    final directory = await _partition(mutation.scope, create: true);
    final target = File(
      '${directory.path}${Platform.pathSeparator}'
      '${mutation.idempotencyKey}.queue',
    );
    if (await target.exists()) return;

    final cleartext = _encodeMutation(mutation);
    final nonce = List<int>.generate(
      _nonceLength,
      (_) => _random.nextInt(256),
      growable: false,
    );
    final encrypted = await _cipher.encrypt(
      cleartext,
      secretKey: await _key(),
      nonce: nonce,
    );
    final record = Uint8List.fromList(<int>[
      ..._magic,
      ...encrypted.nonce,
      ...encrypted.mac.bytes,
      ...encrypted.cipherText,
    ]);
    final temporary = File(
      '${target.path}.tmp.'
      '${List<int>.generate(8, (_) => _random.nextInt(256)).map((value) => value.toRadixString(16).padLeft(2, '0')).join()}',
    );
    try {
      await temporary.writeAsBytes(record, flush: true);
      if (await target.exists()) {
        await temporary.delete();
        return;
      }
      await temporary.rename(target.path);
    } on Object {
      if (await temporary.exists()) await temporary.delete();
      rethrow;
    }
  }

  @override
  Future<List<QueuedCloudMutation>> pending(CloudMutationScope scope) async {
    final directory = await _partition(scope, create: false);
    if (!await directory.exists()) return const <QueuedCloudMutation>[];
    final records = <QueuedCloudMutation>[];
    await for (final entity in directory.list(followLinks: false)) {
      if (entity is! File || !entity.path.endsWith('.queue')) continue;
      final type = await FileSystemEntity.type(entity.path, followLinks: false);
      if (type != FileSystemEntityType.file) continue;
      final length = await entity.length();
      if (length <= _magic.length + _nonceLength + _macLength ||
          length > _maxRecordBytes + 64 * 1024) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Stored offline cloud mutation is invalid',
        );
      }
      final record = await entity.readAsBytes();
      final mutation = await _decodeMutation(record);
      if (mutation.scope != scope ||
          !entity.path.endsWith('${mutation.idempotencyKey}.queue')) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Stored offline cloud mutation scope is invalid',
        );
      }
      records.add(mutation);
    }
    records.sort((left, right) {
      final time = left.enqueuedAt.compareTo(right.enqueuedAt);
      return time != 0
          ? time
          : left.idempotencyKey.compareTo(right.idempotencyKey);
    });
    return List<QueuedCloudMutation>.unmodifiable(records);
  }

  @override
  Future<void> remove(CloudMutationScope scope, String idempotencyKey) async {
    _validateIdempotencyKey(idempotencyKey);
    final directory = await _partition(scope, create: false);
    final target = File(
      '${directory.path}${Platform.pathSeparator}$idempotencyKey.queue',
    );
    if (await target.exists()) await target.delete();
    if (await directory.exists() && await directory.list().isEmpty) {
      await directory.delete();
    }
  }

  Future<Directory> _partition(
    CloudMutationScope scope, {
    required bool create,
  }) async {
    final directory = Directory(
      '${_root.path}${Platform.pathSeparator}${cloudMutationScopeKey(scope)}',
    );
    if (create) await directory.create(recursive: true);
    return directory;
  }

  Future<SecretKey> _key() => _keyFuture ??= _loadOrCreateKey();

  Future<SecretKey> _loadOrCreateKey() async {
    final stored = await _credentialStore.read(_masterKeyName);
    if (stored != null) {
      try {
        final bytes = base64Decode(stored);
        if (bytes.length != 32) throw const FormatException();
        return SecretKey(bytes);
      } on Object {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Offline cloud queue encryption key is invalid',
        );
      }
    }
    final bytes = List<int>.generate(
      32,
      (_) => _random.nextInt(256),
      growable: false,
    );
    await _credentialStore.write(_masterKeyName, base64Encode(bytes));
    final committed = await _credentialStore.read(_masterKeyName);
    if (committed == null) {
      throw const CoreException(
        CoreErrorCode.upstreamError,
        'Offline cloud queue encryption key could not be saved',
      );
    }
    try {
      final committedBytes = base64Decode(committed);
      if (committedBytes.length != 32) throw const FormatException();
      return SecretKey(committedBytes);
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Offline cloud queue encryption key is invalid',
      );
    }
  }

  Uint8List _encodeMutation(QueuedCloudMutation mutation) {
    final metadata = utf8.encode(
      jsonEncode(<String, Object?>{
        'version': 1,
        'provider_id': mutation.scope.providerId,
        'account_id': mutation.scope.accountId,
        'idempotency_key': mutation.idempotencyKey,
        'type': mutation.type.name,
        'path': mutation.path,
        'etag': mutation.etag,
        'enqueued_at': mutation.enqueuedAt.toUtc().toIso8601String(),
      }),
    );
    final output = BytesBuilder(copy: false)
      ..add(Uint8List(4)..buffer.asByteData().setUint32(0, metadata.length))
      ..add(metadata)
      ..add(mutation.bytes);
    return output.takeBytes();
  }

  Future<QueuedCloudMutation> _decodeMutation(Uint8List record) async {
    try {
      for (var index = 0; index < _magic.length; index++) {
        if (record[index] != _magic[index]) throw const FormatException();
      }
      var offset = _magic.length;
      final nonce = record.sublist(offset, offset += _nonceLength);
      final mac = record.sublist(offset, offset += _macLength);
      final cleartext = await _cipher.decrypt(
        SecretBox(record.sublist(offset), nonce: nonce, mac: Mac(mac)),
        secretKey: await _key(),
      );
      if (cleartext.length < 4) throw const FormatException();
      final data = Uint8List.fromList(cleartext);
      final metadataLength = data.buffer.asByteData().getUint32(0);
      if (metadataLength < 2 || metadataLength > data.length - 4) {
        throw const FormatException();
      }
      final decoded = jsonDecode(
        utf8.decode(data.sublist(4, 4 + metadataLength)),
      );
      if (decoded is! Map || decoded['version'] != 1) {
        throw const FormatException();
      }
      return QueuedCloudMutation(
        scope: CloudMutationScope(
          providerId: decoded['provider_id'] as String,
          accountId: decoded['account_id'] as String,
        ),
        idempotencyKey: decoded['idempotency_key'] as String,
        type: CloudMutationType.values.byName(decoded['type'] as String),
        path: decoded['path'] as String,
        etag: decoded['etag'] as String?,
        bytes: data.sublist(4 + metadataLength),
        enqueuedAt: DateTime.parse(decoded['enqueued_at'] as String).toUtc(),
      );
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Stored offline cloud mutation failed integrity validation',
      );
    }
  }

  static void _validateIdempotencyKey(String value) {
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Offline cloud mutation key is invalid',
      );
    }
  }
}
