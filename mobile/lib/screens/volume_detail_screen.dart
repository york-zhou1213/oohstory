import 'package:flutter/material.dart';
import '../models/book.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import 'reader_screen.dart';

String volumeChapterDisplayTitle(String chapterTitle, String volumeTitle) {
  final original = chapterTitle.trim();
  final volume = volumeTitle.trim();
  if (original.isEmpty || volume.isEmpty) return original;
  const separatorPattern = r'[\s\-–—_:：·・/\\]';
  final parts = volume
      .split(RegExp('$separatorPattern+', unicode: true))
      .where((part) => part.isNotEmpty)
      .toList();
  if (parts.isEmpty) return original;
  final prefix = parts.map(RegExp.escape).join('$separatorPattern*');
  final repeatedPrefix = RegExp(
    '^$separatorPattern*$prefix$separatorPattern+',
    caseSensitive: false,
    unicode: true,
  );
  final stripped = original.replaceFirst(repeatedPrefix, '').trim();
  return stripped.isEmpty ? original : stripped;
}

String volumeDisplayTitle(String bookTitle, String volumeTitle) {
  final book = bookTitle.trim();
  final volume = volumeTitle.trim();
  final generic = RegExp(
    r'^第[0-9０-９一二三四五六七八九十百零〇两]+卷$',
    unicode: true,
  ).hasMatch(volume);
  return generic && book.isNotEmpty ? '$book $volume' : volume;
}

class VolumeDetailScreen extends StatefulWidget {
  final String bookId;
  final Volume volume;
  final List<Chapter> chapters;
  final Book? book;

  const VolumeDetailScreen({
    super.key,
    required this.bookId,
    required this.volume,
    required this.chapters,
    this.book,
  });

  @override
  State<VolumeDetailScreen> createState() => _VolumeDetailScreenState();
}

class _VolumeDetailScreenState extends State<VolumeDetailScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final _api = ApiService();

  @override
  void initState() {
    super.initState();
    final hasIllustrations = widget.volume.illustrationPaths.isNotEmpty;
    _tabController = TabController(
      length: hasIllustrations ? 2 : 1,
      vsync: this,
    );
  }

  @override
  void dispose() {
    _tabController.dispose();
    _api.dispose();
    super.dispose();
  }

  void _openReader(String chapterId) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => ReaderScreen(
          bookId: widget.bookId,
          chapterId: chapterId,
          chapters: widget.chapters,
          book: widget.book,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final vol = widget.volume;
    final hasIllustrations = vol.illustrationPaths.isNotEmpty;
    final displayTitle = volumeDisplayTitle(
      widget.book?.title ?? '',
      vol.title,
    );

    return Scaffold(
      appBar: AppBar(
        title: Text(
          displayTitle,
          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        bottom: TabBar(
          controller: _tabController,
          tabs: [
            const Tab(text: '章节目录'),
            if (hasIllustrations)
              Tab(text: '插画 (${vol.illustrationPaths.length})'),
          ],
          labelStyle: const TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w600,
          ),
          indicatorColor: AppTheme.seedPurple,
          labelColor: AppTheme.seedPurple,
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          _buildChapterList(theme),
          if (hasIllustrations) _buildIllustrationGrid(theme),
        ],
      ),
    );
  }

  Widget _buildChapterList(ThemeData theme) {
    final volChapters = widget.chapters;
    if (volChapters.isEmpty) {
      return const Center(child: Text('暂无章节'));
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: volChapters.length,
      itemBuilder: (context, i) {
        final ch = volChapters[i];
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: ListTile(
            dense: true,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(10),
            ),
            leading: Container(
              width: 28,
              height: 28,
              decoration: BoxDecoration(
                color: AppTheme.seedPurple.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(7),
              ),
              child: Center(
                child: Text(
                  '${i + 1}',
                  style: TextStyle(
                    fontSize: 11,
                    color: AppTheme.seedPurple,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ),
            title: Text(
              volumeChapterDisplayTitle(ch.title, widget.volume.title),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium?.copyWith(
                fontWeight: FontWeight.w500,
              ),
            ),
            trailing: ch.wordCount != null
                ? Text(
                    '${ch.wordCount}字',
                    style: TextStyle(
                      fontSize: 11,
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
                    ),
                  )
                : null,
            onTap: () => _openReader(ch.id),
          ),
        );
      },
    );
  }

  Widget _buildIllustrationGrid(ThemeData theme) {
    final paths = widget.volume.illustrationPaths;
    return GridView.builder(
      padding: const EdgeInsets.all(12),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        childAspectRatio: 0.7,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
      ),
      itemCount: paths.length,
      itemBuilder: (context, i) {
        final url = _api.illustrationUrl(widget.bookId, paths[i]);
        return GestureDetector(
          onTap: () => _showFullImage(context, i, paths),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: Image.network(
              url,
              fit: BoxFit.cover,
              errorBuilder: (_, __, ___) => Container(
                decoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: const Center(
                  child: Icon(Icons.broken_image_outlined, size: 32),
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  void _showFullImage(BuildContext context, int index, List<String> allPaths) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => _IllustrationViewer(
          bookId: widget.bookId,
          paths: allPaths,
          initialIndex: index,
        ),
      ),
    );
  }
}

class _IllustrationViewer extends StatefulWidget {
  final String bookId;
  final List<String> paths;
  final int initialIndex;

  const _IllustrationViewer({
    required this.bookId,
    required this.paths,
    required this.initialIndex,
  });

  @override
  State<_IllustrationViewer> createState() => _IllustrationViewerState();
}

class _IllustrationViewerState extends State<_IllustrationViewer> {
  late PageController _pageController;
  late int _currentIndex;
  final _api = ApiService();

  @override
  void initState() {
    super.initState();
    _currentIndex = widget.initialIndex;
    _pageController = PageController(initialPage: _currentIndex);
  }

  @override
  void dispose() {
    _pageController.dispose();
    _api.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(
          '${_currentIndex + 1} / ${widget.paths.length}',
          style: const TextStyle(fontSize: 14),
        ),
      ),
      body: PageView.builder(
        controller: _pageController,
        itemCount: widget.paths.length,
        onPageChanged: (i) => setState(() => _currentIndex = i),
        itemBuilder: (context, i) {
          final url = _api.illustrationUrl(widget.bookId, widget.paths[i]);
          return InteractiveViewer(
            minScale: 0.5,
            maxScale: 4.0,
            child: Center(
              child: Image.network(
                url,
                fit: BoxFit.contain,
                loadingBuilder: (_, child, progress) {
                  if (progress == null) return child;
                  return Center(
                    child: CircularProgressIndicator(
                      value: progress.expectedTotalBytes != null
                          ? progress.cumulativeBytesLoaded /
                                progress.expectedTotalBytes!
                          : null,
                      color: Colors.white54,
                    ),
                  );
                },
                errorBuilder: (_, __, ___) => const Center(
                  child: Icon(
                    Icons.broken_image_outlined,
                    size: 48,
                    color: Colors.white38,
                  ),
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}
