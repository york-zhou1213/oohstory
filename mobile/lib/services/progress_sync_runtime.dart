import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../adapters/progress/progress.dart';
import '../core/core.dart';
import 'account_service.dart';
import 'api_service.dart';
import 'progress_sync_queue.dart';

class ProgressSyncRuntime {
  ProgressSyncRuntime._();

  static final instance = ProgressSyncRuntime._();
  static const _deviceIdKey = 'oohstory_progress_sync_device_id_v1';

  final Map<String, ProgressSyncQueue> _queues = <String, ProgressSyncQueue>{};
  SharedPreferences? _preferences;
  String? _deviceId;
  OohStoryProgressTransport? _transport;

  bool get enabled =>
      ProductCapabilityProfile.production.accountProgressSyncEnabled;

  Future<void> record({
    required String bookId,
    required String chapterId,
    required double percentage,
    String documentVersion = 'oohstory-catalog-v1',
  }) async {
    final account = AccountService.instance;
    if (!enabled || !account.isSignedIn || account.user == null) return;
    final queue = await _queue(account.user!.id);
    await queue.enqueue(
      ProgressRecord(
        bookId: canonicalBookId(bookId),
        documentVersion: documentVersion,
        location: jsonEncode(<String, Object?>{
          'chapter_id': chapterId,
          'within': percentage.clamp(0.0, 1.0),
        }),
        percentage: percentage.clamp(0.0, 1.0).toDouble(),
        deviceId: await _loadDeviceId(),
        updatedAt: DateTime.now().toUtc(),
        revision: 0,
      ),
    );
  }

  Future<ProgressSyncSummary?> synchronize() async {
    final account = AccountService.instance;
    if (!enabled || !account.isSignedIn || account.user == null) return null;
    return (await _queue(account.user!.id)).synchronize();
  }

  static String canonicalBookId(String value) {
    final normalized = value.trim();
    if (RegExp(r'^[A-Za-z0-9._~-]{1,256}$').hasMatch(normalized)) {
      return 'oohstory:$normalized';
    }
    return 'sha256:${sha256.convert(utf8.encode(normalized))}';
  }

  Future<ProgressSyncQueue> _queue(String accountId) async {
    final preferences = _preferences ??= await SharedPreferences.getInstance();
    final transport = _transport ??= OohStoryProgressTransport(
      baseUri: Uri.parse(ApiService.baseUrl),
      authHeaders: () => AccountService.instance.authHeaders,
      client: OohHttpClient(),
    );
    return _queues.putIfAbsent(
      accountId,
      () => ProgressSyncQueue(
        preferences: preferences,
        accountId: accountId,
        transport: transport,
      ),
    );
  }

  Future<String> _loadDeviceId() async {
    if (_deviceId != null) return _deviceId!;
    final preferences = _preferences ??= await SharedPreferences.getInstance();
    final stored = preferences.getString(_deviceIdKey);
    if (stored != null &&
        RegExp(
          r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
        ).hasMatch(stored)) {
      return _deviceId = stored;
    }
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    final hex = bytes
        .map((value) => value.toRadixString(16).padLeft(2, '0'))
        .join();
    final generated =
        '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-'
        '${hex.substring(20)}';
    await preferences.setString(_deviceIdKey, generated);
    _deviceId = generated;
    return generated;
  }
}
