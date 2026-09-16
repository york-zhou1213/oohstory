import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

class ReadingProgress {
  final String chapterId;
  final double within;
  final int timestamp;

  ReadingProgress({
    required this.chapterId,
    required this.within,
    required this.timestamp,
  });

  Map<String, dynamic> toJson() => {
    'chapterId': chapterId,
    'within': within,
    'timestamp': timestamp,
  };

  factory ReadingProgress.fromJson(Map<String, dynamic> json) =>
      ReadingProgress(
        chapterId: json['chapterId'] as String,
        within: (json['within'] as num).toDouble(),
        timestamp: json['timestamp'] as int? ?? 0,
      );
}

class ReadingProgressService {
  static const _key = 'oohstory_reading_progress';
  late SharedPreferences _prefs;

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  Map<String, ReadingProgress> _loadAll() {
    final raw = _prefs.getString(_key);
    if (raw == null) return {};
    final map = jsonDecode(raw) as Map<String, dynamic>;
    return map.map(
      (k, v) =>
          MapEntry(k, ReadingProgress.fromJson(v as Map<String, dynamic>)),
    );
  }

  void _saveAll(Map<String, ReadingProgress> data) {
    _prefs.setString(
      _key,
      jsonEncode(data.map((k, v) => MapEntry(k, v.toJson()))),
    );
  }

  ReadingProgress? get(String bookId) => _loadAll()[bookId];

  void save(String bookId, String chapterId, double within) {
    final all = _loadAll();
    all[bookId] = ReadingProgress(
      chapterId: chapterId,
      within: within,
      timestamp: DateTime.now().millisecondsSinceEpoch,
    );
    if (all.length > 100) {
      final sorted = all.entries.toList()
        ..sort((a, b) => b.value.timestamp.compareTo(a.value.timestamp));
      final trimmed = Map.fromEntries(sorted.take(100));
      _saveAll(trimmed);
    } else {
      _saveAll(all);
    }
  }

  void remove(String bookId) {
    final all = _loadAll();
    all.remove(bookId);
    _saveAll(all);
  }
}
