enum ReaderViewMode { scroll, page, spread }

extension ReaderViewModeValue on ReaderViewMode {
  String get storageValue => switch (this) {
    ReaderViewMode.scroll => 'scroll',
    ReaderViewMode.page => 'page',
    ReaderViewMode.spread => 'spread',
  };

  String get label => switch (this) {
    ReaderViewMode.scroll => '连续滚动',
    ReaderViewMode.page => '单页翻页',
    ReaderViewMode.spread => '双页展开',
  };

  static ReaderViewMode parse(String? value) => switch (value) {
    'page' => ReaderViewMode.page,
    'spread' => ReaderViewMode.spread,
    _ => ReaderViewMode.scroll,
  };
}

class ReaderPreferences {
  final ReaderViewMode viewMode;
  final double fontSize;
  final double lineHeight;
  final int backgroundIndex;

  const ReaderPreferences({
    this.viewMode = ReaderViewMode.scroll,
    this.fontSize = 18,
    this.lineHeight = 1.8,
    this.backgroundIndex = 0,
  });

  ReaderPreferences copyWith({
    ReaderViewMode? viewMode,
    double? fontSize,
    double? lineHeight,
    int? backgroundIndex,
  }) => ReaderPreferences(
    viewMode: viewMode ?? this.viewMode,
    fontSize: fontSize ?? this.fontSize,
    lineHeight: lineHeight ?? this.lineHeight,
    backgroundIndex: backgroundIndex ?? this.backgroundIndex,
  );

  Map<String, dynamic> toJson() => {
    'viewMode': viewMode.storageValue,
    'fontSize': fontSize,
    'lineHeight': lineHeight,
    'backgroundIndex': backgroundIndex,
  };

  factory ReaderPreferences.fromJson(Map<String, dynamic> json) =>
      ReaderPreferences(
        viewMode: ReaderViewModeValue.parse(json['viewMode'] as String?),
        fontSize: (json['fontSize'] as num?)?.toDouble() ?? 18,
        lineHeight: (json['lineHeight'] as num?)?.toDouble() ?? 1.8,
        backgroundIndex: (json['backgroundIndex'] as num?)?.toInt() ?? 0,
      );
}

class OfflineAnnotation {
  final String id;
  final String bookId;
  final String type;
  final String excerpt;
  final String note;
  final double progress;
  final int createdAt;

  const OfflineAnnotation({
    required this.id,
    required this.bookId,
    required this.type,
    required this.excerpt,
    required this.note,
    required this.progress,
    required this.createdAt,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'bookId': bookId,
    'type': type,
    'excerpt': excerpt,
    'note': note,
    'progress': progress,
    'createdAt': createdAt,
  };

  factory OfflineAnnotation.fromJson(Map<String, dynamic> json) =>
      OfflineAnnotation(
        id: json['id'] as String? ?? '',
        bookId: json['bookId'] as String? ?? '',
        type: json['type'] as String? ?? 'bookmark',
        excerpt: json['excerpt'] as String? ?? '',
        note: json['note'] as String? ?? '',
        progress: (json['progress'] as num?)?.toDouble() ?? 0,
        createdAt: (json['createdAt'] as num?)?.toInt() ?? 0,
      );
}

class OfflineReadingStats {
  final int totalSeconds;
  final int sessions;
  final int lastReadAt;

  const OfflineReadingStats({
    this.totalSeconds = 0,
    this.sessions = 0,
    this.lastReadAt = 0,
  });

  Map<String, dynamic> toJson() => {
    'totalSeconds': totalSeconds,
    'sessions': sessions,
    'lastReadAt': lastReadAt,
  };

  factory OfflineReadingStats.fromJson(Map<String, dynamic> json) =>
      OfflineReadingStats(
        totalSeconds: (json['totalSeconds'] as num?)?.toInt() ?? 0,
        sessions: (json['sessions'] as num?)?.toInt() ?? 0,
        lastReadAt: (json['lastReadAt'] as num?)?.toInt() ?? 0,
      );
}
