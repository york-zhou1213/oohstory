import 'package:flutter/material.dart';
import '../models/book.dart';
import '../services/api_service.dart';
import '../screens/book_detail_screen.dart';
import 'ooh_ui.dart';

class BookCard extends StatelessWidget {
  final Book book;
  const BookCard({super.key, required this.book});

  static final ApiService _api = ApiService();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final coverUrl = book.coverUrl != null
        ? _api.fullCoverUrl(book.coverUrl)
        : _api.coverUrl(book.id);

    return Semantics(
      button: true,
      label: '打开《${book.title}》，作者${book.author}',
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => BookDetailScreen(bookId: book.id)),
        ),
        child: Padding(
          padding: const EdgeInsets.only(bottom: 2),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: LayoutBuilder(
                  builder: (context, constraints) => OohBookCover(
                    imageUrl: coverUrl,
                    title: book.title,
                    width: constraints.maxWidth,
                    height: constraints.maxHeight,
                  ),
                ),
              ),
              const SizedBox(height: 11),
              Text(
                book.title,
                style: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                  height: 1.28,
                  letterSpacing: -.1,
                ),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 5),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      book.author,
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  if (book.status != null) ...[
                    const SizedBox(width: 6),
                    Text(
                      book.status == 'finished' ? '完结' : '连载',
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
