import 'dart:async';

import 'package:flutter/material.dart';

import '../models/book.dart';
import '../theme/app_theme.dart';
import 'ooh_ui.dart';

const int oohHomeHeroBookLimit = 7;

class OohHomeHeroCarousel extends StatefulWidget {
  final List<Book> books;
  final String Function(Book book) coverUrlFor;
  final String Function(Book book) synopsisFor;
  final String Function(Book book) chapterLabelFor;
  final ValueChanged<Book> onOpen;
  final ImageProvider<Object>? Function(Book book)? imageProviderFor;
  final Duration autoplayInterval;

  const OohHomeHeroCarousel({
    super.key,
    required this.books,
    required this.coverUrlFor,
    required this.synopsisFor,
    required this.chapterLabelFor,
    required this.onOpen,
    this.imageProviderFor,
    this.autoplayInterval = const Duration(seconds: 5),
  });

  @override
  State<OohHomeHeroCarousel> createState() => _OohHomeHeroCarouselState();
}

class _OohHomeHeroCarouselState extends State<OohHomeHeroCarousel>
    with WidgetsBindingObserver {
  final PageController _controller = PageController();
  Timer? _timer;
  int _index = 0;
  bool _reduceMotion = false;
  bool _resumed = true;

  List<Book> get _books =>
      widget.books.take(oohHomeHeroBookLimit).toList(growable: false);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) == true;
    _restartTimer();
  }

  @override
  void didUpdateWidget(covariant OohHomeHeroCarousel oldWidget) {
    super.didUpdateWidget(oldWidget);
    final previousIds = oldWidget.books
        .take(oohHomeHeroBookLimit)
        .map((book) => book.id)
        .join('|');
    final nextIds = _books.map((book) => book.id).join('|');
    if (previousIds != nextIds) {
      _index = 0;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_controller.hasClients) _controller.jumpToPage(0);
      });
    }
    _restartTimer();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _resumed = state == AppLifecycleState.resumed;
    _restartTimer();
  }

  void _restartTimer() {
    _timer?.cancel();
    if (!_resumed || _reduceMotion || _books.length <= 1) return;
    _timer = Timer(widget.autoplayInterval, _advance);
  }

  void _advance() {
    if (!mounted || _books.length <= 1) return;
    _goTo((_index + 1) % _books.length, restartTimer: false);
  }

  void _goTo(int index, {bool restartTimer = true}) {
    if (_books.isEmpty) return;
    final target = index.clamp(0, _books.length - 1);
    if (_controller.hasClients) {
      if (_reduceMotion) {
        _controller.jumpToPage(target);
      } else {
        _controller.animateToPage(
          target,
          duration: const Duration(milliseconds: 320),
          curve: Curves.easeOutCubic,
        );
      }
    } else if (mounted) {
      setState(() => _index = target);
    }
    if (restartTimer) _restartTimer();
  }

  void _onPageChanged(int index) {
    if (!mounted) return;
    setState(() => _index = index);
    _restartTimer();
  }

  Widget _networkOrMemoryImage(
    Book book, {
    required BoxFit fit,
    required Widget error,
  }) {
    final provider = widget.imageProviderFor?.call(book);
    if (provider != null) {
      return Image(image: provider, fit: fit, errorBuilder: (_, _, _) => error);
    }
    return OohNetworkImage(
      imageUrl: widget.coverUrlFor(book),
      fit: fit,
      alignment: Alignment.topCenter,
      error: error,
    );
  }

  Widget _heroPanel(
    BuildContext context,
    Book book, {
    required int bookIndex,
    required bool tablet,
    required double width,
  }) {
    final theme = Theme.of(context);
    const ink = Color(0xFF101B32);

    return Semantics(
      key: ValueKey('home-hero-page-${book.id}'),
      button: true,
      label: '打开编辑精选《${book.title}》',
      child: InkWell(
        onTap: () => widget.onOpen(book),
        child: SizedBox(
          height: tablet ? 236 : 176,
          child: Stack(
            children: [
              Positioned.fill(
                child: Opacity(
                  opacity: .42,
                  child: _networkOrMemoryImage(
                    book,
                    fit: BoxFit.cover,
                    error: const SizedBox.shrink(),
                  ),
                ),
              ),
              Positioned.fill(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.centerLeft,
                      end: Alignment.centerRight,
                      colors: [
                        ink,
                        ink.withValues(alpha: .94),
                        const Color(0xFF173967).withValues(alpha: .70),
                      ],
                      stops: const [0, .56, 1],
                    ),
                  ),
                ),
              ),
              Padding(
                padding: EdgeInsets.fromLTRB(
                  tablet ? 24 : 13,
                  tablet ? 20 : 12,
                  tablet ? 24 : 13,
                  tablet ? 20 : 12,
                ),
                child: Row(
                  children: [
                    _coverStage(context, bookIndex, tablet: tablet),
                    SizedBox(width: tablet ? 28 : 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '编辑精选',
                            style: theme.textTheme.labelMedium?.copyWith(
                              color: const Color(0xFF9EC5FF),
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                          SizedBox(height: tablet ? 8 : 4),
                          Text(
                            book.title,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style:
                                (tablet
                                        ? theme.textTheme.headlineSmall
                                        : theme.textTheme.titleLarge)
                                    ?.copyWith(
                                      color: Colors.white,
                                      fontWeight: FontWeight.w800,
                                      height: 1.16,
                                      letterSpacing: -.4,
                                    ),
                          ),
                          SizedBox(height: tablet ? 6 : 3),
                          Text(
                            book.author,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: Colors.white.withValues(alpha: .72),
                            ),
                          ),
                          if (tablet) ...[
                            const SizedBox(height: 9),
                            Text(
                              widget.synopsisFor(book),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.bodySmall?.copyWith(
                                color: Colors.white.withValues(alpha: .72),
                                height: 1.45,
                              ),
                            ),
                          ],
                          const Spacer(),
                          Row(
                            children: [
                              Expanded(
                                child: Text(
                                  [
                                    if (book.category != null) book.category!,
                                    widget.chapterLabelFor(book),
                                    book.status == 'finished' ? '完结' : '连载',
                                  ].join('  '),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: theme.textTheme.labelSmall?.copyWith(
                                    color: Colors.white.withValues(alpha: .68),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 8),
                              Text(
                                '开始阅读',
                                style: theme.textTheme.labelMedium?.copyWith(
                                  color: Colors.white,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                              const SizedBox(width: 4),
                              const Icon(
                                Icons.arrow_forward_rounded,
                                size: 18,
                                color: Colors.white,
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _coverStage(BuildContext context, int index, {required bool tablet}) {
    final books = _books;
    final previous = books[(index - 1 + books.length) % books.length];
    final current = books[index];
    final next = books[(index + 1) % books.length];
    final stageWidth = tablet ? 222.0 : 122.0;
    final stageHeight = tablet ? 190.0 : 136.0;
    final sideWidth = tablet ? 78.0 : 48.0;
    final sideHeight = sideWidth * 1.5;
    final mainWidth = tablet ? 124.0 : 82.0;
    final mainHeight = mainWidth * 1.5;

    Widget cover(
      Book stageBook, {
      required double width,
      required double height,
      required bool active,
      double angle = 0,
    }) {
      return Transform.rotate(
        angle: angle,
        child: AnimatedOpacity(
          opacity: active ? 1 : .58,
          duration: _reduceMotion
              ? Duration.zero
              : const Duration(milliseconds: 220),
          child: DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(active ? 10 : 8),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: active ? .34 : .16),
                  blurRadius: active ? 20 : 10,
                  offset: Offset(0, active ? 10 : 5),
                ),
              ],
            ),
            child: OohBookCover(
              imageUrl: widget.coverUrlFor(stageBook),
              imageProvider: widget.imageProviderFor?.call(stageBook),
              title: stageBook.title,
              width: width,
              height: height,
              borderRadius: BorderRadius.circular(active ? 10 : 8),
            ),
          ),
        ),
      );
    }

    return SizedBox(
      width: stageWidth,
      height: stageHeight,
      child: Stack(
        alignment: Alignment.center,
        children: [
          Positioned(
            left: 0,
            top: tablet ? 38 : 32,
            child: cover(
              previous,
              width: sideWidth,
              height: sideHeight,
              active: false,
              angle: -.07,
            ),
          ),
          Positioned(
            right: 0,
            top: tablet ? 38 : 32,
            child: cover(
              next,
              width: sideWidth,
              height: sideHeight,
              active: false,
              angle: .07,
            ),
          ),
          cover(current, width: mainWidth, height: mainHeight, active: true),
        ],
      ),
    );
  }

  Widget _coverTab(BuildContext context, Book book, int index) {
    final active = index == _index;
    return Semantics(
      button: true,
      selected: active,
      label: '第${index + 1}本，共${_books.length}本，${book.title}',
      child: InkWell(
        key: ValueKey('home-hero-tab-$index'),
        onTap: () => _goTo(index),
        borderRadius: BorderRadius.circular(5),
        child: AnimatedContainer(
          duration: _reduceMotion
              ? Duration.zero
              : const Duration(milliseconds: 180),
          width: active ? 23 : 18,
          height: active ? 34 : 27,
          margin: EdgeInsets.symmetric(horizontal: active ? 3 : 2.5),
          padding: EdgeInsets.all(active ? 1.5 : 0),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: active ? .16 : 0),
            borderRadius: BorderRadius.circular(5),
            border: Border.all(
              color: active ? Colors.white : Colors.transparent,
              width: 1.5,
            ),
            boxShadow: active
                ? [
                    BoxShadow(
                      color: Colors.black.withValues(alpha: .24),
                      blurRadius: 8,
                      offset: const Offset(0, 4),
                    ),
                  ]
                : null,
          ),
          child: AnimatedOpacity(
            opacity: active ? 1 : .55,
            duration: _reduceMotion
                ? Duration.zero
                : const Duration(milliseconds: 180),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: _networkOrMemoryImage(
                book,
                fit: BoxFit.cover,
                error: const ColoredBox(color: Color(0xFF263C61)),
              ),
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final books = _books;
    if (books.isEmpty) return const SizedBox.shrink();
    return LayoutBuilder(
      builder: (context, constraints) {
        final horizontal = OohPageMetrics.horizontalPadding(
          constraints.maxWidth,
        );
        final tablet = constraints.maxWidth >= 720;
        final heroHeight = tablet ? 236.0 : 176.0;
        return Padding(
          padding: EdgeInsets.fromLTRB(horizontal, 14, horizontal, 0),
          child: Material(
            key: const ValueKey('home-hero-carousel-card'),
            color: const Color(0xFF101B32),
            borderRadius: BorderRadius.circular(AppTheme.cardRadius),
            clipBehavior: Clip.antiAlias,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(
                  height: heroHeight,
                  child: PageView.builder(
                    controller: _controller,
                    itemCount: books.length,
                    onPageChanged: _onPageChanged,
                    itemBuilder: (context, index) => _heroPanel(
                      context,
                      books[index],
                      bookIndex: index,
                      tablet: tablet,
                      width: constraints.maxWidth - horizontal * 2,
                    ),
                  ),
                ),
                if (books.length > 1)
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: const Color(0xFF0B1529).withValues(alpha: .96),
                      border: Border(
                        top: BorderSide(
                          color: Colors.white.withValues(alpha: .10),
                        ),
                      ),
                    ),
                    child: Padding(
                      padding: EdgeInsets.fromLTRB(
                        tablet ? 18 : 12,
                        tablet ? 8 : 4,
                        tablet ? 18 : 12,
                        tablet ? 9 : 4,
                      ),
                      child: Row(
                        children: [
                          Text(
                            '${_index + 1} / ${books.length}',
                            style: Theme.of(context).textTheme.labelSmall
                                ?.copyWith(
                                  color: Colors.white.withValues(alpha: .72),
                                  fontWeight: FontWeight.w800,
                                ),
                          ),
                          const Spacer(),
                          ...List.generate(
                            books.length,
                            (index) => _coverTab(context, books[index], index),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    _controller.dispose();
    super.dispose();
  }
}
