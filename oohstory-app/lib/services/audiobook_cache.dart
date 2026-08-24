import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:path_provider/path_provider.dart';

import 'api_service.dart';

class AudiobookCache {
  static const int sessionSegmentLimit = 5;
  final ApiService api;
  bool _cancelled = false;

  AudiobookCache(this.api);

  Future<Directory> _legacyRoot() async {
    final support = await getApplicationSupportDirectory();
    return Directory('${support.path}/audiobook-cache-v1');
  }

  Future<bool> allowNextChapterPrefetch() async {
    final states = await Connectivity().checkConnectivity();
    return states.any(
      (state) =>
          state == ConnectivityResult.wifi ||
          state == ConnectivityResult.ethernet,
    );
  }

  Future<List<Uri>> prepareChapter(
    Map<String, dynamic> manifest, {
    int priorityStartIndex = 0,
    void Function(int done, int total)? onProgress,
    void Function(String segmentHash)? onSegment,
  }) async {
    _cancelled = false;
    await clearSession();
    final segments = (manifest['segments'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
    final start = priorityStartIndex
        .clamp(0, segments.isEmpty ? 0 : segments.length - 1)
        .toInt();
    final ordered = [
      ...segments.skip(start),
      ...segments.take(start),
    ].take(sessionSegmentLimit).toList();
    for (var index = 0; index < ordered.length; index++) {
      if (_cancelled) {
        throw const FileSystemException('audiobook cache cancelled');
      }
      onProgress?.call(index + 1, ordered.length);
      onSegment?.call('${ordered[index]['sha256'] ?? ''}');
    }
    return const [];
  }

  Future<List<Uri>> expectedUris(Map<String, dynamic> manifest) async =>
      const [];

  Future<void> cancelIncomplete() async {
    _cancelled = true;
    await clearSession();
  }

  Future<void> clearSession() async {
    final root = await _legacyRoot();
    try {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    } catch (_) {
      // Cache cleanup must never interrupt live playback.
    }
  }

  Future<void> evictLru() async => clearSession();
}
