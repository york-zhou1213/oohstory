import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_service.dart';

class AppUpdateInfo {
  final String versionName;
  final int versionCode;
  final String releaseDate;
  final String downloadUrl;
  final String sha256;
  final int sizeBytes;
  final List<String> releaseNotes;

  const AppUpdateInfo({
    required this.versionName,
    required this.versionCode,
    required this.releaseDate,
    required this.downloadUrl,
    required this.sha256,
    required this.sizeBytes,
    required this.releaseNotes,
  });

  factory AppUpdateInfo.fromJson(Map<String, dynamic> json) {
    final latest = (json['latest'] as Map?)?.cast<String, dynamic>() ?? {};
    final notes = (latest['release_notes_public'] as List? ?? [])
        .map((item) => item.toString().trim())
        .where((item) => item.isNotEmpty)
        .take(8)
        .toList();
    return AppUpdateInfo(
      versionName: '${latest['version_name'] ?? ''}'.trim(),
      versionCode: (latest['version_code'] as num? ?? 0).toInt(),
      releaseDate: '${latest['release_date'] ?? ''}'.trim(),
      downloadUrl: '${latest['download_url'] ?? ''}'.trim(),
      sha256: '${latest['sha256'] ?? ''}'.trim(),
      sizeBytes: (latest['size_bytes'] as num? ?? 0).toInt(),
      releaseNotes: notes,
    );
  }

  bool get hasPublicNotes => releaseNotes.isNotEmpty;
}

class AppUpdateService {
  static const currentVersionName = '1.27.0';
  static const currentVersionCode = 75;
  static const _channel = MethodChannel('com.oohstory.oohstory/app');
  static const _lastPromptedVersionCodeKey =
      'oohstory_android_update_prompted_version_code';
  static const _lastPromptedAtKey = 'oohstory_android_update_prompted_at';
  static const _promptCooldown = Duration(hours: 12);

  final ApiService _api;

  AppUpdateService(this._api);

  Future<AppUpdateInfo?> checkForUpdate({bool force = false}) async {
    final payload = await _api.getAndroidUpdate(
      versionCode: currentVersionCode,
      versionName: currentVersionName,
    );
    if (payload['available'] != true) return null;
    final info = AppUpdateInfo.fromJson(payload);
    if (info.versionCode <= currentVersionCode ||
        info.versionName.isEmpty ||
        !info.hasPublicNotes ||
        !_isTrustedDownloadUrl(info.downloadUrl)) {
      return null;
    }
    if (!force && await _wasPromptedRecently(info.versionCode)) return null;
    return info;
  }

  Future<void> markPrompted(AppUpdateInfo info) async {
    final preferences = await SharedPreferences.getInstance();
    await preferences.setInt(_lastPromptedVersionCodeKey, info.versionCode);
    await preferences.setInt(
      _lastPromptedAtKey,
      DateTime.now().millisecondsSinceEpoch,
    );
  }

  Future<void> openDownload(AppUpdateInfo info) async {
    if (!_isTrustedDownloadUrl(info.downloadUrl)) {
      throw StateError('untrusted update download url');
    }
    final opened = await _channel.invokeMethod<bool>('openUrl', {
      'url': info.downloadUrl,
    });
    if (opened != true) {
      throw StateError('update download url was not opened');
    }
  }

  Future<bool> _wasPromptedRecently(int versionCode) async {
    final preferences = await SharedPreferences.getInstance();
    if (preferences.getInt(_lastPromptedVersionCodeKey) != versionCode) {
      return false;
    }
    final promptedAt = preferences.getInt(_lastPromptedAtKey) ?? 0;
    if (promptedAt <= 0) return false;
    final elapsed = DateTime.now().difference(
      DateTime.fromMillisecondsSinceEpoch(promptedAt),
    );
    return elapsed < _promptCooldown;
  }

  bool _isTrustedDownloadUrl(String value) {
    final uri = Uri.tryParse(value);
    return uri != null &&
        uri.scheme == 'https' &&
        uri.host == 'oohstory.com' &&
        uri.path.startsWith('/downloads/android/') &&
        uri.path.endsWith('.apk');
  }
}
