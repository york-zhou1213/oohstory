import 'dart:convert';
import 'dart:io';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:path_provider/path_provider.dart';
import '../models/book.dart';

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
  final int lastReadAt;

  HistoryEntry({
    required this.book,
    required this.lastChapterId,
    required this.lastChapterTitle,
    required this.lastReadAt,
  });

  Map<String, dynamic> toJson() => {
        'book': book.toJson(),
        'lastChapterId': lastChapterId,
        'lastChapterTitle': lastChapterTitle,
        'lastReadAt': lastReadAt,
      };

  factory HistoryEntry.fromJson(Map<String, dynamic> json) => HistoryEntry(
        book: BookMeta.fromJson(json['book'] as Map<String, dynamic>),
        lastChapterId: json['lastChapterId'] as String? ?? '',
        lastChapterTitle: json['lastChapterTitle'] as String? ?? '',
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

  factory DownloadedBookInfo.fromJson(Map<String, dynamic> json) => DownloadedBookInfo(
        book: BookMeta.fromJson(json['book'] as Map<String, dynamic>),
        chapters: (json['chapters'] as List?)
                ?.map((e) => DownloadedChapterInfo.fromJson(e as Map<String, dynamic>))
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

  factory DownloadedChapterInfo.fromJson(Map<String, dynamic> json) => DownloadedChapterInfo(
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
  final String fileName;
  final int fileSize;
  final int addedAt;

  LocalBookInfo({
    required this.id,
    required this.title,
    required this.fileName,
    required this.fileSize,
    required this.addedAt,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'fileName': fileName,
        'fileSize': fileSize,
        'addedAt': addedAt,
      };

  factory LocalBookInfo.fromJson(Map<String, dynamic> json) => LocalBookInfo(
        id: json['id'] as String,
        title: json['title'] as String? ?? '',
        fileName: json['fileName'] as String? ?? '',
        fileSize: json['fileSize'] as int? ?? 0,
        addedAt: json['addedAt'] as int? ?? 0,
      );
}

class LocalStorageService {
  static const _favKey = 'oohstory_favorites';
  static const _histKey = 'oohstory_history';
  static const _dlIndexKey = 'oohstory_downloads_index';
  static const _localBooksKey = 'oohstory_local_books';

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
    return list.map((e) => BookMeta.fromJson(e as Map<String, dynamic>)).toList()
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
    return list.map((e) => HistoryEntry.fromJson(e as Map<String, dynamic>)).toList()
      ..sort((a, b) => b.lastReadAt.compareTo(a.lastReadAt));
  }

  void recordRead(Book book, String chapterId, String chapterTitle) {
    final history = getHistory();
    history.removeWhere((e) => e.book.id == book.id);
    history.insert(
      0,
      HistoryEntry(
        book: BookMeta.fromBook(book),
        lastChapterId: chapterId,
        lastChapterTitle: chapterTitle,
        lastReadAt: DateTime.now().millisecondsSinceEpoch,
      ),
    );
    if (history.length > 50) history.removeRange(50, history.length);
    _prefs.setString(_histKey, jsonEncode(history.map((e) => e.toJson()).toList()));
  }

  void removeFromHistory(String bookId) {
    final history = getHistory();
    history.removeWhere((e) => e.book.id == bookId);
    _prefs.setString(_histKey, jsonEncode(history.map((e) => e.toJson()).toList()));
  }

  void clearHistory() {
    _prefs.remove(_histKey);
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
    return map.map((k, v) => MapEntry(k, DownloadedBookInfo.fromJson(v as Map<String, dynamic>)));
  }

  void _saveDownloadIndex(Map<String, DownloadedBookInfo> index) {
    _prefs.setString(_dlIndexKey, jsonEncode(index.map((k, v) => MapEntry(k, v.toJson()))));
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

  Future<void> downloadChapter(Book book, Chapter chapter, String content) async {
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

  // ─── Local Books (imported TXT) ───

  Future<Directory> _localBooksDir() async {
    final appDir = await getApplicationDocumentsDirectory();
    final dir = Directory('${appDir.path}/local_books');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  List<LocalBookInfo> getLocalBooks() {
    final raw = _prefs.getString(_localBooksKey);
    if (raw == null) return [];
    final list = jsonDecode(raw) as List;
    return list.map((e) => LocalBookInfo.fromJson(e as Map<String, dynamic>)).toList()
      ..sort((a, b) => b.addedAt.compareTo(a.addedAt));
  }

  void _saveLocalBooks(List<LocalBookInfo> books) {
    _prefs.setString(_localBooksKey, jsonEncode(books.map((b) => b.toJson()).toList()));
  }

  Future<LocalBookInfo> importLocalBook(String filePath, String fileName) async {
    final source = File(filePath);
    final content = await source.readAsString();
    final id = 'local_${DateTime.now().millisecondsSinceEpoch}';
    final title = fileName.replaceAll(RegExp(r'\.(txt|TXT)$'), '');
    final dir = await _localBooksDir();
    final dest = File('${dir.path}/$id.txt');
    await dest.writeAsString(content);
    final info = LocalBookInfo(
      id: id,
      title: title,
      fileName: fileName,
      fileSize: content.length,
      addedAt: DateTime.now().millisecondsSinceEpoch,
    );
    final books = getLocalBooks();
    books.insert(0, info);
    _saveLocalBooks(books);
    return info;
  }

  Future<String?> getLocalBookContent(String bookId) async {
    final dir = await _localBooksDir();
    final file = File('${dir.path}/$bookId.txt');
    if (await file.exists()) return file.readAsString();
    return null;
  }

  Future<void> deleteLocalBook(String bookId) async {
    final books = getLocalBooks();
    books.removeWhere((b) => b.id == bookId);
    _saveLocalBooks(books);
    final dir = await _localBooksDir();
    final file = File('${dir.path}/$bookId.txt');
    if (await file.exists()) await file.delete();
  }
}
