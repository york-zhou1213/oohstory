class Book {
  final String id;
  final String title;
  final String author;
  final String? description;
  final String? category;
  final int? wordCount;
  final int? chapterCount;
  final String? status;
  final String? coverUrl;
  final int? views;
  final int? likes;

  Book({
    required this.id,
    required this.title,
    required this.author,
    this.description,
    this.category,
    this.wordCount,
    this.chapterCount,
    this.status,
    this.coverUrl,
    this.views,
    this.likes,
  });

  factory Book.fromJson(Map<String, dynamic> json) => Book(
        id: (json['public_id'] ?? json['id'] ?? '') as String,
        title: json['title'] as String? ?? '',
        author: json['author'] as String? ?? '',
        description: json['summary'] as String? ?? json['description'] as String?,
        category: json['category'] as String?,
        wordCount: json['approx_word_count'] as int? ?? json['word_count'] as int?,
        chapterCount: json['approx_chapter_count'] as int? ?? json['chapter_count'] as int?,
        status: json['serialization_status'] as String? ?? json['status'] as String?,
        coverUrl: json['cover_url'] as String?,
        views: json['views'] as int?,
        likes: json['likes'] as int?,
      );
}

class Chapter {
  final String id;
  final String? label;
  final String title;
  final int position;
  final String? content;
  final int? wordCount;
  final String? previousId;
  final String? nextId;

  Chapter({
    required this.id,
    this.label,
    required this.title,
    required this.position,
    this.content,
    this.wordCount,
    this.previousId,
    this.nextId,
  });

  String get displayTitle => label != null && label!.isNotEmpty ? '$label $title' : title;

  factory Chapter.fromJson(Map<String, dynamic> json) => Chapter(
        id: json['id'].toString(),
        label: json['label'] as String?,
        title: json['title'] as String? ?? '',
        position: json['position'] as int? ?? json['id'] as int? ?? 0,
        content: json['content'] as String?,
        wordCount: json['word_count'] as int? ?? json['byte_count'] as int?,
        previousId: json['previous_id']?.toString(),
        nextId: json['next_id']?.toString(),
      );
}

class Deconstruction {
  final String slug;
  final String title;
  final String? publicId;
  final String? coverUrl;
  final String? progress;
  final double progressPercent;
  final int completedChapters;
  final int totalChapters;
  final List<DeconstructionDoc> documents;
  final List<DeconstructionSubdir> subdirectories;

  Deconstruction({
    required this.slug,
    required this.title,
    this.publicId,
    this.coverUrl,
    this.progress,
    this.progressPercent = 0,
    this.completedChapters = 0,
    this.totalChapters = 0,
    this.documents = const [],
    this.subdirectories = const [],
  });

  bool get isCompleted => progressPercent >= 100;
  bool get isActive => progressPercent > 0 && progressPercent < 100;
  String get statusText => isActive ? '拆解中' : isCompleted ? '已完成' : '档案已建立';

  factory Deconstruction.fromJson(Map<String, dynamic> json) => Deconstruction(
        slug: json['slug'] as String,
        title: json['title'] as String? ?? '',
        publicId: json['public_id'] as String?,
        coverUrl: json['cover_url'] as String?,
        progress: json['progress'] as String?,
        progressPercent: (json['progress_percent'] as num?)?.toDouble() ?? 0,
        completedChapters: json['completed_chapters'] as int? ?? 0,
        totalChapters: json['total_chapters'] as int? ?? 0,
        documents: (json['documents'] as List?)
                ?.map((e) => DeconstructionDoc.fromJson(e as Map<String, dynamic>))
                .toList() ??
            [],
        subdirectories: (json['subdirectories'] as List?)
                ?.map((e) => DeconstructionSubdir.fromJson(e as Map<String, dynamic>))
                .toList() ??
            [],
      );
}

class DeconstructionDoc {
  final String filename;
  final String label;
  final String? content;

  DeconstructionDoc({required this.filename, required this.label, this.content});

  factory DeconstructionDoc.fromJson(Map<String, dynamic> json) => DeconstructionDoc(
        filename: json['filename'] as String? ?? '',
        label: json['label'] as String? ?? '',
        content: json['content'] as String?,
      );
}

class DeconstructionSubdir {
  final String name;
  final String label;
  final List<DeconstructionEntry> items;

  DeconstructionSubdir({required this.name, required this.label, required this.items});

  factory DeconstructionSubdir.fromJson(Map<String, dynamic> json) => DeconstructionSubdir(
        name: json['name'] as String? ?? '',
        label: json['label'] as String? ?? '',
        items: (json['items'] as List?)
                ?.map((e) => DeconstructionEntry.fromJson(e as Map<String, dynamic>))
                .toList() ??
            [],
      );
}

class DeconstructionEntry {
  final String? filename;
  final String label;
  final String type;
  final String? name;
  final List<DeconstructionEntry> items;

  DeconstructionEntry({
    this.filename,
    required this.label,
    required this.type,
    this.name,
    this.items = const [],
  });

  bool get isDirectory => type == 'directory';

  factory DeconstructionEntry.fromJson(Map<String, dynamic> json) => DeconstructionEntry(
        filename: json['filename'] as String?,
        label: json['label'] as String? ?? '',
        type: json['type'] as String? ?? 'file',
        name: json['name'] as String?,
        items: (json['items'] as List?)
                ?.map((e) => DeconstructionEntry.fromJson(e as Map<String, dynamic>))
                .toList() ??
            [],
      );
}

class Volume {
  final int id;
  final String title;
  final List<int> chapterIds;
  final int illustrationCount;

  Volume({
    required this.id,
    required this.title,
    required this.chapterIds,
    this.illustrationCount = 0,
  });

  factory Volume.fromJson(Map<String, dynamic> json) => Volume(
        id: json['id'] as int? ?? 0,
        title: json['title'] as String? ?? '',
        chapterIds: (json['chapter_ids'] as List?)?.map((e) => e as int).toList() ?? [],
        illustrationCount: json['illustration_count'] as int? ?? 0,
      );
}

class ChapterCatalog {
  final List<Chapter> chapters;
  final List<Volume> volumes;

  ChapterCatalog({required this.chapters, this.volumes = const []});

  bool get hasVolumes => volumes.isNotEmpty;
}

class TtsVoice {
  final String key;
  final String label;
  final String gender;
  final String desc;

  TtsVoice({
    required this.key,
    required this.label,
    required this.gender,
    required this.desc,
  });

  factory TtsVoice.fromJson(Map<String, dynamic> json) => TtsVoice(
        key: json['key'] as String,
        label: json['label'] as String? ?? '',
        gender: json['gender'] as String? ?? '',
        desc: json['desc'] as String? ?? '',
      );
}
