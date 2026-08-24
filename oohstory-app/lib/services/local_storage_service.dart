import 'dart:convert';
import 'dart:io';
import 'package:archive/archive.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:path_provider/path_provider.dart';
import 'package:path/path.dart' as path_utils;
import '../models/book.dart';
import '../models/reader_preferences.dart';
import 'offline_book_parser.dart';

class BookMeta {
  final String id;
  final String title;
  final String author;
  final String? coverUrl;
  final int timestamp;

  BookMeta({
    required this.id,
    required this.title,
    required this.author,
    this.coverUrl,
    required this.timestamp,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'title': title,
    'author': author,
    'coverUrl': coverUrl,
    'timestamp': timestamp,
  };

  factory BookMeta.fromJson(Map<String, dynamic> json) => BookMeta(
    id: json['id'] as String,
    title: json['title'] as String? ?? '',
    author: json['author'] as String? ?? '',
    coverUrl: json['coverUrl'] as String?,
    timestamp: json['timestamp'] as int? ?? 0,
  );

  factory BookMeta.fromBook(Book book) => BookMeta(
    id: book.id,
    title: book.title,
    author: book.author,
    coverUrl: book.coverUrl,
    timestamp: DateTime.now().millisecondsSinceEpoch,
  );
}

class HistoryEntry {
  final BookMeta book;
  final String lastChapterId;
  final String lastChapterTitle;
  final int lastChapterPosition;
  final double chapterProgress;
  final double overallProgress;
  final int chapterCount;
  final int lastReadAt;

  HistoryEntry({
    required this.book,
    required this.lastChapterId,
    required this.lastChapterTitle,
    this.lastChapterPosition = 1,
    this.chapterProgress = 0,
    this.overallProgress = 0,
    this.chapterCount = 0,
    required this.lastReadAt,
  });

  Map<String, dynamic> toJson() => {
    'book': book.toJson(),
    'lastChapterId': lastChapterId,
    'lastChapterTitle': lastChapterTitle,
    'lastChapterPosition': lastChapterPosition,
    'chapterProgress': chapterProgress,
    'overallProgress': overallProgress,
    'chapterCount': chapterCount,
    'lastReadAt': lastReadAt,
  };

  factory HistoryEntry.fromJson(Map<String, dynamic> json) => HistoryEntry(
    book: BookMeta.fromJson(json['book'] as Map<String, dynamic>),
    lastChapterId: json['lastChapterId'] as String? ?? '',
    lastChapterTitle: json['lastChapterTitle'] as String? ?? '',
    lastChapterPosition:
        json['lastChapterPosition'] as int? ??
        int.tryParse(json['lastChapterId'] as String? ?? '') ??
        1,
    chapterProgress: (json['chapterProgress'] as num?)?.toDouble() ?? 0,
    overallProgress: (json['overallProgress'] as num?)?.toDouble() ?? 0,
    chapterCount: json['chapterCount'] as int? ?? 0,
    lastReadAt: json['lastReadAt'] as int? ?? 0,
  );
}

class DownloadedBookInfo {
  final BookMeta book;
  final List<DownloadedChapterInfo> chapters;
  int get totalSize => chapters.fold(0, (sum, c) => sum + c.size);

  DownloadedBookInfo({required this.book, required this.chapters});

  Map<String, dynamic> toJson() => {
    'book': book.toJson(),
    'chapters': chapters.map((c) => c.toJson()).toList(),
  };

  factory DownloadedBookInfo.fromJson(Map<String, dynamic> json) =>
      DownloadedBookInfo(
        book: BookMeta.fromJson(json['book'] as Map<String, dynamic>),
        chapters:
            (json['chapters'] as List?)
                ?.map(
                  (e) =>
                      DownloadedChapterInfo.fromJson(e as Map<String, dynamic>),
                )
                .toList() ??
            [],
      );
}

class DownloadedChapterInfo {
  final String id;
  final String title;
  final int position;
  final int size;
  final int downloadedAt;

  DownloadedChapterInfo({
    required this.id,
    required this.title,
    required this.position,
    required this.size,
    required this.downloadedAt,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'title': title,
    'position': position,
    'size': size,
    'downloadedAt': downloadedAt,
  };

  factory DownloadedChapterInfo.fromJson(Map<String, dynamic> json) =>
      DownloadedChapterInfo(
        id: json['id'] as String,
        title: json['title'] as String? ?? '',
        position: json['position'] as int? ?? 0,
        size: json['size'] as int? ?? 0,
        downloadedAt: json['downloadedAt'] as int? ?? 0,
      );
}

class LocalBookInfo {
  final String id;
  final String title;
  final String author;
  final String fileName;
  final String format;
  final int fileSize;
  final int wordCount;
  final int addedAt;
  final int lastOpenedAt;
  final double progress;
  final String storageExtension;
  final int pageCount;

  LocalBookInfo({
    required this.id,
    required this.title,
    this.author = '',
    required this.fileName,
    this.format = 'txt',
    required this.fileSize,
    this.wordCount = 0,
    required this.addedAt,
    this.lastOpenedAt = 0,
    this.progress = 0,
    this.storageExtension = 'txt',
    this.pageCount = 0,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'title': title,
    'author': author,
    'fileName': fileName,
    'format': format,
    'fileSize': fileSize,
    'wordCount': wordCount,
    'addedAt': addedAt,
    'lastOpenedAt': lastOpenedAt,
    'progress': progress,
    'storageExtension': storageExtension,
    'pageCount': pageCount,
  };

  factory LocalBookInfo.fromJson(Map<String, dynamic> json) => LocalBookInfo(
    id: json['id'] as String,
    title: json['title'] as String? ?? '',
    author: json['author'] as String? ?? '',
    fileName: json['fileName'] as String? ?? '',
    format: json['format'] as String? ?? 'txt',
    fileSize: json['fileSize'] as int? ?? 0,
    wordCount: json['wordCount'] as int? ?? 0,
    addedAt: json['addedAt'] as int? ?? 0,
    lastOpenedAt: json['lastOpenedAt'] as int? ?? 0,
    progress: (json['progress'] as num?)?.toDouble() ?? 0,
    storageExtension: _safeStorageExtension(json['storageExtension']),
    pageCount: json['pageCount'] as int? ?? 0,
  );

  static String _safeStorageExtension(Object? raw) {
    final extension = raw?.toString().toLowerCase() ?? 'txt';
    return const {'txt', 'pdf', 'cbz'}.contains(extension) ? extension : 'txt';
  }

  LocalBookInfo copyWith({
    String? title,
    String? author,
    int? lastOpenedAt,
    double? progress,
  }) => LocalBookInfo(
    id: id,
    title: title ?? this.title,
    author: author ?? this.author,
    fileName: fileName,
    format: format,
    fileSize: fileSize,
    wordCount: wordCount,
    addedAt: addedAt,
    lastOpenedAt: lastOpenedAt ?? this.lastOpenedAt,
    progress: progress ?? this.progress,
    storageExtension: storageExtension,
    pageCount: pageCount,
  );
}

class OfflineSnapshotInfo {
  final String path;
  final String name;
  final int createdAt;
  final int size;

  const OfflineSnapshotInfo({
    required this.path,
    required this.name,
    required this.createdAt,
    required this.size,
  });
}

class LocalStorageService {
  static final ValueNotifier<int> historyVersion = ValueNotifier<int>(0);
  static const _favKey = 'oohstory_favorites';
  static const _histKey = 'oohstory_history';
  static const _dlIndexKey = 'oohstory_downloads_index';
  static const _localBooksKey = 'oohstory_local_books';
  static const _readerPreferencesKey = 'oohstory_reader_preferences_v2';
  static const _annotationsKey = 'oohstory_offline_annotations_v1';
  static const _readingStatsKey = 'oohstory_offline_reading_stats_v1';
  static const _backupSchema = 2;
  final OfflineBookParser _bookParser = const OfflineBookParser();

  late SharedPreferences _prefs;
  bool _initialized = false;

  Future<void> init() async {
    if (_initialized) return;
    _prefs = await SharedPreferences.getInstance();
    _initialized = true;
  }

  // ─── Favorites ───

  List<BookMeta> getFavorites() {
    final raw = _prefs.getString(_favKey);
    if (raw == null) return [];
    final list = jsonDecode(raw) as List;
    return list
        .map((e) => BookMeta.fromJson(e as Map<String, dynamic>))
        .toList()
      ..sort((a, b) => b.timestamp.compareTo(a.timestamp));
  }

  bool isFavorite(String bookId) {
    return getFavorites().any((b) => b.id == bookId);
  }

  void addFavorite(Book book) {
    final favs = getFavorites();
    favs.removeWhere((b) => b.id == book.id);
    favs.insert(0, BookMeta.fromBook(book));
    _prefs.setString(_favKey, jsonEncode(favs.map((b) => b.toJson()).toList()));
  }

  void removeFavorite(String bookId) {
    final favs = getFavorites();
    favs.removeWhere((b) => b.id == bookId);
    _prefs.setString(_favKey, jsonEncode(favs.map((b) => b.toJson()).toList()));
  }

  void toggleFavorite(Book book) {
    if (isFavorite(book.id)) {
      removeFavorite(book.id);
    } else {
      addFavorite(book);
    }
  }

  // ─── Reading History ───

  List<HistoryEntry> getHistory() {
    final raw = _prefs.getString(_histKey);
    if (raw == null) return [];
    final list = jsonDecode(raw) as List;
    return list
        .map((e) => HistoryEntry.fromJson(e as Map<String, dynamic>))
        .toList()
      ..sort((a, b) => b.lastReadAt.compareTo(a.lastReadAt));
  }

  void recordRead(
    Book book,
    String chapterId,
    String chapterTitle, {
    int chapterPosition = 1,
    int chapterCount = 0,
    double chapterProgress = 0,
  }) {
    final safePosition = chapterPosition < 1 ? 1 : chapterPosition;
    final safeCount = chapterCount < 0 ? 0 : chapterCount;
    final safeChapterProgress = chapterProgress.clamp(0.0, 1.0).toDouble();
    final overall = safeCount > 0
        ? ((safePosition - 1 + safeChapterProgress) / safeCount)
              .clamp(0.0, 1.0)
              .toDouble()
        : 0.0;
    final history = getHistory();
    history.removeWhere((e) => e.book.id == book.id);
    history.insert(
      0,
      HistoryEntry(
        book: BookMeta.fromBook(book),
        lastChapterId: chapterId,
        lastChapterTitle: chapterTitle,
        lastChapterPosition: safePosition,
        chapterProgress: safeChapterProgress,
        overallProgress: overall,
        chapterCount: safeCount,
        lastReadAt: DateTime.now().millisecondsSinceEpoch,
      ),
    );
    if (history.length > 50) history.removeRange(50, history.length);
    _prefs.setString(
      _histKey,
      jsonEncode(history.map((e) => e.toJson()).toList()),
    );
    historyVersion.value++;
  }

  void removeFromHistory(String bookId) {
    final history = getHistory();
    history.removeWhere((e) => e.book.id == bookId);
    _prefs.setString(
      _histKey,
      jsonEncode(history.map((e) => e.toJson()).toList()),
    );
    historyVersion.value++;
  }

  void clearHistory() {
    _prefs.remove(_histKey);
    historyVersion.value++;
  }

  /// Merge server-owned history and favorites without deleting newer local
  /// entries. Private local files remain device-only by design.
  void mergeCloudState(Map<String, dynamic> cloud) {
    final favorites = {for (final item in getFavorites()) item.id: item};
    for (final raw in (cloud['favorites'] as List? ?? const [])) {
      final item = Map<String, dynamic>.from(raw as Map);
      final timestamp =
          DateTime.tryParse(
            item['updated_at'] as String? ?? '',
          )?.millisecondsSinceEpoch ??
          0;
      final candidate = BookMeta(
        id: item['book_id'] as String? ?? '',
        title: item['title'] as String? ?? '',
        author: item['author'] as String? ?? '',
        coverUrl: item['cover_url'] as String?,
        timestamp: timestamp,
      );
      final previous = favorites[candidate.id];
      if (candidate.id.isNotEmpty &&
          (previous == null || candidate.timestamp >= previous.timestamp)) {
        favorites[candidate.id] = candidate;
      }
    }
    _prefs.setString(
      _favKey,
      jsonEncode(favorites.values.map((item) => item.toJson()).toList()),
    );

    final history = {for (final item in getHistory()) item.book.id: item};
    for (final raw in (cloud['history'] as List? ?? const [])) {
      final item = Map<String, dynamic>.from(raw as Map);
      final timestamp =
          DateTime.tryParse(
            item['updated_at'] as String? ?? '',
          )?.millisecondsSinceEpoch ??
          0;
      final candidate = HistoryEntry(
        book: BookMeta(
          id: item['book_id'] as String? ?? '',
          title: item['title'] as String? ?? '',
          author: item['author'] as String? ?? '',
          coverUrl: item['cover_url'] as String?,
          timestamp: timestamp,
        ),
        lastChapterId: (item['chapter_id'] ?? 1).toString(),
        lastChapterTitle: item['current_chapter'] as String? ?? '',
        lastChapterPosition: (item['chapter_id'] as num?)?.toInt() ?? 1,
        chapterProgress:
            (item['chapter_progress'] as num?)?.toDouble() ??
            (item['progress'] as num?)?.toDouble() ??
            0,
        overallProgress: (item['overall_progress'] as num?)?.toDouble() ?? 0,
        chapterCount: (item['chapter_count'] as num?)?.toInt() ?? 0,
        lastReadAt: timestamp,
      );
      final previous = history[candidate.book.id];
      if (candidate.book.id.isNotEmpty &&
          (previous == null || candidate.lastReadAt >= previous.lastReadAt)) {
        history[candidate.book.id] = candidate;
      }
    }
    final mergedHistory = history.values.toList()
      ..sort((left, right) => right.lastReadAt.compareTo(left.lastReadAt));
    _prefs.setString(
      _histKey,
      jsonEncode(mergedHistory.take(500).map((item) => item.toJson()).toList()),
    );
    historyVersion.value++;
  }

  // ─── Downloads ───

  Future<Directory> _downloadDir() async {
    final appDir = await getApplicationDocumentsDirectory();
    final dlDir = Directory('${appDir.path}/downloads');
    if (!await dlDir.exists()) await dlDir.create(recursive: true);
    return dlDir;
  }

  Map<String, DownloadedBookInfo> _getDownloadIndex() {
    final raw = _prefs.getString(_dlIndexKey);
    if (raw == null) return {};
    final map = jsonDecode(raw) as Map<String, dynamic>;
    return map.map(
      (k, v) =>
          MapEntry(k, DownloadedBookInfo.fromJson(v as Map<String, dynamic>)),
    );
  }

  void _saveDownloadIndex(Map<String, DownloadedBookInfo> index) {
    _prefs.setString(
      _dlIndexKey,
      jsonEncode(index.map((k, v) => MapEntry(k, v.toJson()))),
    );
  }

  List<DownloadedBookInfo> getDownloadedBooks() {
    return _getDownloadIndex().values.toList()
      ..sort((a, b) => b.book.timestamp.compareTo(a.book.timestamp));
  }

  bool isChapterDownloaded(String bookId, String chapterId) {
    final index = _getDownloadIndex();
    final bookInfo = index[bookId];
    if (bookInfo == null) return false;
    return bookInfo.chapters.any((c) => c.id == chapterId);
  }

  int downloadedChapterCount(String bookId) {
    final index = _getDownloadIndex();
    return index[bookId]?.chapters.length ?? 0;
  }

  Future<void> downloadChapter(
    Book book,
    Chapter chapter,
    String content,
  ) async {
    final dir = await _downloadDir();
    final file = File('${dir.path}/${book.id}_${chapter.id}.txt');
    await file.writeAsString(content);

    final index = _getDownloadIndex();
    final existing = index[book.id];
    final chapterInfo = DownloadedChapterInfo(
      id: chapter.id,
      title: chapter.displayTitle,
      position: chapter.position,
      size: content.length,
      downloadedAt: DateTime.now().millisecondsSinceEpoch,
    );

    if (existing != null) {
      existing.chapters.removeWhere((c) => c.id == chapter.id);
      existing.chapters.add(chapterInfo);
      existing.chapters.sort((a, b) => a.position.compareTo(b.position));
    } else {
      index[book.id] = DownloadedBookInfo(
        book: BookMeta.fromBook(book),
        chapters: [chapterInfo],
      );
    }
    _saveDownloadIndex(index);
  }

  Future<String?> getDownloadedContent(String bookId, String chapterId) async {
    final dir = await _downloadDir();
    final file = File('${dir.path}/${bookId}_$chapterId.txt');
    if (await file.exists()) return file.readAsString();
    return null;
  }

  Future<void> deleteDownloadedBook(String bookId) async {
    final index = _getDownloadIndex();
    final bookInfo = index.remove(bookId);
    _saveDownloadIndex(index);

    if (bookInfo != null) {
      final dir = await _downloadDir();
      for (final ch in bookInfo.chapters) {
        final file = File('${dir.path}/${bookId}_${ch.id}.txt');
        if (await file.exists()) await file.delete();
      }
    }
  }

  Future<void> deleteDownloadedChapter(String bookId, String chapterId) async {
    final index = _getDownloadIndex();
    final bookInfo = index[bookId];
    if (bookInfo == null) return;

    bookInfo.chapters.removeWhere((c) => c.id == chapterId);
    if (bookInfo.chapters.isEmpty) {
      index.remove(bookId);
    }
    _saveDownloadIndex(index);

    final dir = await _downloadDir();
    final file = File('${dir.path}/${bookId}_$chapterId.txt');
    if (await file.exists()) await file.delete();
  }

  Future<int> totalDownloadSize() async {
    final dir = await _downloadDir();
    if (!await dir.exists()) return 0;
    int total = 0;
    await for (final entity in dir.list()) {
      if (entity is File) {
        total += await entity.length();
      }
    }
    return total;
  }

  Future<void> clearAllDownloads() async {
    _prefs.remove(_dlIndexKey);
    final dir = await _downloadDir();
    if (await dir.exists()) await dir.delete(recursive: true);
  }

  // ─── Local Books ───

  Future<Directory> _localBooksDir() async {
    final appDir = await getApplicationDocumentsDirectory();
    final dir = Directory('${appDir.path}/local_books');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  Future<Directory> _snapshotDir() async {
    final appDir = await getApplicationDocumentsDirectory();
    final dir = Directory('${appDir.path}/offline_snapshots');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  List<LocalBookInfo> getLocalBooks() {
    final raw = _prefs.getString(_localBooksKey);
    if (raw == null) return [];
    final list = jsonDecode(raw) as List;
    return list
        .map((e) => LocalBookInfo.fromJson(e as Map<String, dynamic>))
        .toList()
      ..sort((a, b) => b.addedAt.compareTo(a.addedAt));
  }

  void _saveLocalBooks(List<LocalBookInfo> books) {
    _prefs.setString(
      _localBooksKey,
      jsonEncode(books.map((b) => b.toJson()).toList()),
    );
  }

  Future<LocalBookInfo> importLocalBook(
    String filePath,
    String fileName,
  ) async {
    final parsed = await _bookParser.parse(filePath, fileName);
    if (parsed.assetBytes == null && parsed.content.trim().isEmpty) {
      throw const FormatException('文件中没有可读正文');
    }
    final sourceBytes =
        parsed.assetBytes ?? Uint8List.fromList(utf8.encode(parsed.content));
    final digest = sha256.convert(sourceBytes).toString();
    final id = 'local_${digest.substring(0, 24)}';
    final dir = await _localBooksDir();
    final dest = File('${dir.path}/$id.${parsed.storageExtension}');
    if (parsed.assetBytes != null) {
      await dest.writeAsBytes(parsed.assetBytes!, flush: true);
    } else {
      await dest.writeAsString(parsed.content, flush: true);
    }
    final now = DateTime.now().millisecondsSinceEpoch;
    final info = LocalBookInfo(
      id: id,
      title: parsed.title,
      author: parsed.author,
      fileName: fileName,
      format: parsed.format,
      fileSize: parsed.sourceSize,
      wordCount: parsed.wordCount,
      addedAt: now,
      storageExtension: parsed.storageExtension,
      pageCount: parsed.pageCount,
    );
    final books = getLocalBooks();
    books.removeWhere((book) => book.id == id);
    books.insert(0, info);
    _saveLocalBooks(books);
    return info;
  }

  Future<List<LocalBookInfo>> importLocalBooks(
    Iterable<({String path, String name})> files,
  ) async {
    final imported = <LocalBookInfo>[];
    for (final file in files) {
      imported.add(await importLocalBook(file.path, file.name));
    }
    return imported;
  }

  Future<String?> getLocalBookContent(String bookId) async {
    final book = getLocalBooks().where((item) => item.id == bookId).firstOrNull;
    if (book == null || book.storageExtension != 'txt') return null;
    final file = await getLocalBookFile(book);
    if (await file.exists()) return file.readAsString();
    return null;
  }

  Future<File> getLocalBookFile(LocalBookInfo book) async {
    final dir = await _localBooksDir();
    return File('${dir.path}/${book.id}.${book.storageExtension}');
  }

  Future<void> deleteLocalBook(String bookId) async {
    final books = getLocalBooks();
    final removed = books.where((book) => book.id == bookId).firstOrNull;
    books.removeWhere((b) => b.id == bookId);
    _saveLocalBooks(books);
    final file = removed == null ? null : await getLocalBookFile(removed);
    if (file != null && await file.exists()) await file.delete();
    final annotations = getAnnotations()
      ..removeWhere((annotation) => annotation.bookId == bookId);
    _saveAnnotations(annotations);
    final stats = _getAllReadingStats()..remove(bookId);
    _saveReadingStats(stats);
  }

  void updateLocalBookProgress(String bookId, double progress) {
    final books = getLocalBooks();
    final index = books.indexWhere((book) => book.id == bookId);
    if (index < 0) return;
    books[index] = books[index].copyWith(
      progress: progress.clamp(0, 1),
      lastOpenedAt: DateTime.now().millisecondsSinceEpoch,
    );
    _saveLocalBooks(books);
  }

  List<LocalBookInfo> searchLocalBooks(String query) {
    final needle = query.trim().toLowerCase();
    if (needle.isEmpty) return getLocalBooks();
    return getLocalBooks()
        .where(
          (book) =>
              book.title.toLowerCase().contains(needle) ||
              book.author.toLowerCase().contains(needle) ||
              book.fileName.toLowerCase().contains(needle),
        )
        .toList();
  }

  List<int> searchLocalBookContent(String content, String query) {
    final needle = query.trim().toLowerCase();
    if (needle.isEmpty) return const [];
    final haystack = content.toLowerCase();
    final matches = <int>[];
    var offset = 0;
    while (offset < haystack.length && matches.length < 500) {
      final index = haystack.indexOf(needle, offset);
      if (index < 0) break;
      matches.add(index);
      offset = index + needle.length;
    }
    return matches;
  }

  ReaderPreferences getReaderPreferences() {
    final raw = _prefs.getString(_readerPreferencesKey);
    if (raw == null) return const ReaderPreferences();
    try {
      return ReaderPreferences.fromJson(
        Map<String, dynamic>.from(jsonDecode(raw) as Map),
      );
    } catch (_) {
      return const ReaderPreferences();
    }
  }

  Future<void> saveReaderPreferences(ReaderPreferences preferences) =>
      _prefs.setString(_readerPreferencesKey, jsonEncode(preferences.toJson()));

  List<OfflineAnnotation> getAnnotations({String? bookId}) {
    final raw = _prefs.getString(_annotationsKey);
    if (raw == null) return [];
    final annotations =
        (jsonDecode(raw) as List)
            .map(
              (item) => OfflineAnnotation.fromJson(
                Map<String, dynamic>.from(item as Map),
              ),
            )
            .where((item) => bookId == null || item.bookId == bookId)
            .toList()
          ..sort((left, right) => right.createdAt.compareTo(left.createdAt));
    return annotations;
  }

  void _saveAnnotations(List<OfflineAnnotation> annotations) {
    _prefs.setString(
      _annotationsKey,
      jsonEncode(annotations.map((item) => item.toJson()).toList()),
    );
  }

  OfflineAnnotation addAnnotation({
    required String bookId,
    required String type,
    required String excerpt,
    String note = '',
    required double progress,
  }) {
    final now = DateTime.now().millisecondsSinceEpoch;
    final annotation = OfflineAnnotation(
      id: 'annotation_${now}_${excerpt.hashCode.abs()}',
      bookId: bookId,
      type: type,
      excerpt: excerpt,
      note: note,
      progress: progress.clamp(0, 1),
      createdAt: now,
    );
    final annotations = getAnnotations()..add(annotation);
    _saveAnnotations(annotations);
    return annotation;
  }

  void removeAnnotation(String id) {
    final annotations = getAnnotations()..removeWhere((item) => item.id == id);
    _saveAnnotations(annotations);
  }

  Map<String, OfflineReadingStats> _getAllReadingStats() {
    final raw = _prefs.getString(_readingStatsKey);
    if (raw == null) return {};
    return Map<String, dynamic>.from(jsonDecode(raw) as Map).map(
      (key, value) => MapEntry(
        key,
        OfflineReadingStats.fromJson(Map<String, dynamic>.from(value as Map)),
      ),
    );
  }

  void _saveReadingStats(Map<String, OfflineReadingStats> stats) {
    _prefs.setString(
      _readingStatsKey,
      jsonEncode(stats.map((key, value) => MapEntry(key, value.toJson()))),
    );
  }

  OfflineReadingStats getReadingStats(String bookId) =>
      _getAllReadingStats()[bookId] ?? const OfflineReadingStats();

  void recordReadingSession(String bookId, Duration duration) {
    if (duration.inSeconds < 5) return;
    final all = _getAllReadingStats();
    final previous = all[bookId] ?? const OfflineReadingStats();
    all[bookId] = OfflineReadingStats(
      totalSeconds: previous.totalSeconds + duration.inSeconds,
      sessions: previous.sessions + 1,
      lastReadAt: DateTime.now().millisecondsSinceEpoch,
    );
    _saveReadingStats(all);
  }

  Future<File> createOfflineBackup() async {
    final archive = Archive();
    final books = getLocalBooks();
    final dir = await _localBooksDir();
    final bookContents = <String, Uint8List>{};
    final fileHashes = <String, String>{};
    for (final book in books) {
      final file = File('${dir.path}/${book.id}.${book.storageExtension}');
      if (!await file.exists()) continue;
      final content = await file.readAsBytes();
      bookContents[book.id] = content;
      fileHashes[book.id] = sha256.convert(content).toString();
    }
    final manifest = {
      'schema': _backupSchema,
      'createdAt': DateTime.now().toUtc().toIso8601String(),
      'books': books.map((book) => book.toJson()).toList(),
      'annotations': getAnnotations().map((item) => item.toJson()).toList(),
      'readingStats': _getAllReadingStats().map(
        (key, value) => MapEntry(key, value.toJson()),
      ),
      'readerPreferences': getReaderPreferences().toJson(),
      'files': fileHashes,
    };
    archive.addFile(
      ArchiveFile.string(
        'manifest.json',
        const JsonEncoder.withIndent('  ').convert(manifest),
      ),
    );
    for (final book in books) {
      final content = bookContents[book.id];
      if (content == null) continue;
      archive.addFile(
        ArchiveFile(
          'books/${book.id}.${book.storageExtension}',
          content.length,
          content,
        ),
      );
    }
    final bytes = ZipEncoder().encodeBytes(archive);
    final temp = await getTemporaryDirectory();
    final output = File(
      '${temp.path}/OOHStory-offline-backup-${DateTime.now().millisecondsSinceEpoch}.zip',
    );
    await output.writeAsBytes(bytes, flush: true);
    return output;
  }

  Future<OfflineSnapshotInfo> createOfflineSnapshot({
    String? preservePath,
  }) async {
    final backup = await createOfflineBackup();
    final dir = await _snapshotDir();
    final createdAt = DateTime.now().millisecondsSinceEpoch;
    final name = 'OOHStory-snapshot-$createdAt.zip';
    final snapshot = await backup.copy('${dir.path}/$name');
    if (await backup.exists()) await backup.delete();
    final snapshots = await listOfflineSnapshots();
    final normalizedPreserve = preservePath == null
        ? null
        : path_utils.normalize(File(preservePath).absolute.path);
    var retained = 0;
    for (final stale in snapshots) {
      final normalizedStale = path_utils.normalize(
        File(stale.path).absolute.path,
      );
      if (normalizedStale == normalizedPreserve || retained < 10) {
        retained += 1;
        continue;
      }
      final file = File(stale.path);
      if (await file.exists()) await file.delete();
    }
    return OfflineSnapshotInfo(
      path: snapshot.path,
      name: name,
      createdAt: createdAt,
      size: await snapshot.length(),
    );
  }

  Future<List<OfflineSnapshotInfo>> listOfflineSnapshots() async {
    final dir = await _snapshotDir();
    final snapshots = <OfflineSnapshotInfo>[];
    await for (final entity in dir.list()) {
      if (entity is! File || !entity.path.toLowerCase().endsWith('.zip')) {
        continue;
      }
      final stat = await entity.stat();
      snapshots.add(
        OfflineSnapshotInfo(
          path: entity.path,
          name: entity.uri.pathSegments.last,
          createdAt: stat.modified.millisecondsSinceEpoch,
          size: stat.size,
        ),
      );
    }
    snapshots.sort((left, right) => right.createdAt.compareTo(left.createdAt));
    return snapshots;
  }

  Future<void> deleteOfflineSnapshot(String snapshotPath) async {
    final dir = await _snapshotDir();
    final file = File(snapshotPath);
    final canonicalDir = path_utils.normalize(dir.absolute.path);
    final canonicalFile = path_utils.normalize(file.absolute.path);
    if (!canonicalFile.startsWith('$canonicalDir${Platform.pathSeparator}')) {
      throw const FormatException('快照路径无效');
    }
    if (await file.exists()) await file.delete();
  }

  Future<File> createAnnotationExport() async {
    final annotations = getAnnotations();
    String csv(String value) => '"${value.replaceAll('"', '""')}"';
    final csvRows = <String>[
      'book_id,type,progress,excerpt,note,created_at',
      ...annotations.map(
        (item) => [
          csv(item.bookId),
          csv(item.type),
          item.progress.toStringAsFixed(4),
          csv(item.excerpt),
          csv(item.note),
          item.createdAt.toString(),
        ].join(','),
      ),
    ];
    final markdown = StringBuffer('# OOHStory 阅读批注\n\n');
    final plain = StringBuffer('OOHStory 阅读批注\n\n');
    final html = StringBuffer(
      '<!doctype html><meta charset="utf-8"><title>OOHStory 阅读批注</title>'
      '<style>body{max-width:760px;margin:40px auto;font:16px/1.7 system-ui;padding:0 20px}'
      'article{border-bottom:1px solid #ddd;padding:18px 0}small{color:#667}</style>'
      '<h1>OOHStory 阅读批注</h1>',
    );
    String escapeHtml(String value) => value
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    for (final item in annotations) {
      final heading = item.type == 'note'
          ? '笔记'
          : item.type == 'highlight'
          ? '高亮'
          : '书签';
      markdown
        ..writeln('## $heading · ${(item.progress * 100).round()}%')
        ..writeln()
        ..writeln('> ${item.excerpt.replaceAll('\n', ' ')}')
        ..writeln()
        ..writeln(item.note)
        ..writeln();
      plain
        ..writeln('$heading · ${(item.progress * 100).round()}%')
        ..writeln(item.excerpt)
        ..writeln(item.note)
        ..writeln();
      html.write(
        '<article><h2>$heading · ${(item.progress * 100).round()}%</h2>'
        '<blockquote>${escapeHtml(item.excerpt)}</blockquote>'
        '<p>${escapeHtml(item.note)}</p><small>${escapeHtml(item.bookId)}</small></article>',
      );
    }
    final archive = Archive()
      ..addFile(ArchiveFile.string('annotations.csv', csvRows.join('\n')))
      ..addFile(ArchiveFile.string('annotations.md', markdown.toString()))
      ..addFile(ArchiveFile.string('annotations.txt', plain.toString()))
      ..addFile(ArchiveFile.string('annotations.html', html.toString()))
      ..addFile(
        ArchiveFile.string(
          'annotations.json',
          const JsonEncoder.withIndent(
            '  ',
          ).convert(annotations.map((item) => item.toJson()).toList()),
        ),
      );
    final temp = await getTemporaryDirectory();
    final output = File(
      '${temp.path}/OOHStory-annotations-${DateTime.now().millisecondsSinceEpoch}.zip',
    );
    await output.writeAsBytes(ZipEncoder().encodeBytes(archive), flush: true);
    return output;
  }

  Future<void> restoreOfflineBackup(String filePath) async {
    final source = File(filePath);
    if (await source.length() > 1024 * 1024 * 1024) {
      throw const FormatException('离线备份不能超过 1 GB');
    }
    final bytes = await source.readAsBytes();
    final archive = ZipDecoder().decodeBytes(bytes, verify: true);
    var expandedBytes = 0;
    for (final entry in archive.files) {
      if (entry.name.contains('..') || entry.name.startsWith('/')) {
        throw const FormatException('备份包含不安全路径');
      }
      expandedBytes += entry.size;
      if (expandedBytes > 2 * 1024 * 1024 * 1024) {
        throw const FormatException('备份解压后超过 2 GB');
      }
    }
    final manifestFile = archive.findFile('manifest.json');
    if (manifestFile == null) throw const FormatException('备份缺少 manifest.json');
    final manifest = Map<String, dynamic>.from(
      jsonDecode(utf8.decode(manifestFile.content as List<int>)) as Map,
    );
    final schema = manifest['schema'] as int? ?? 0;
    if (schema != 1 && schema != _backupSchema) {
      throw const FormatException('备份版本不受支持');
    }
    final books = (manifest['books'] as List? ?? const [])
        .map(
          (item) =>
              LocalBookInfo.fromJson(Map<String, dynamic>.from(item as Map)),
        )
        .toList();
    final hashes = Map<String, dynamic>.from(
      manifest['files'] as Map? ?? const {},
    );
    final localDir = await _localBooksDir();
    final staged = Directory('${localDir.path}.restore');
    if (await staged.exists()) await staged.delete(recursive: true);
    await staged.create(recursive: true);
    for (final book in books) {
      final extension = schema == 1 ? 'txt' : book.storageExtension;
      final entry = archive.findFile('books/${book.id}.$extension');
      if (entry == null) throw FormatException('备份缺少 ${book.title} 正文');
      final content = Uint8List.fromList(entry.content as List<int>);
      final expectedHash = hashes[book.id] as String? ?? '';
      if (expectedHash.isEmpty ||
          sha256.convert(content).toString() != expectedHash) {
        throw FormatException('${book.title} 正文校验失败');
      }
      await File(
        '${staged.path}/${book.id}.$extension',
      ).writeAsBytes(content, flush: true);
    }
    for (final book in books) {
      final extension = schema == 1 ? 'txt' : book.storageExtension;
      final source = File('${staged.path}/${book.id}.$extension');
      await source.rename('${localDir.path}/${book.id}.$extension');
    }
    await staged.delete(recursive: true);
    _saveLocalBooks(books);
    _prefs.setString(
      _annotationsKey,
      jsonEncode(manifest['annotations'] as List? ?? const []),
    );
    _prefs.setString(
      _readingStatsKey,
      jsonEncode(manifest['readingStats'] as Map? ?? const {}),
    );
    _prefs.setString(
      _readerPreferencesKey,
      jsonEncode(manifest['readerPreferences'] as Map? ?? const {}),
    );
  }
}
