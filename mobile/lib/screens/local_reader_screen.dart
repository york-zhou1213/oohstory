import 'package:flutter/material.dart';
import '../services/local_storage_service.dart';


class LocalReaderScreen extends StatefulWidget {
  final LocalBookInfo book;
  const LocalReaderScreen({super.key, required this.book});

  @override
  State<LocalReaderScreen> createState() => _LocalReaderScreenState();
}

class _LocalReaderScreenState extends State<LocalReaderScreen> {
  final _storage = LocalStorageService();
  final _scrollController = ScrollController();
  String? _content;
  bool _loading = true;
  double _fontSize = 18;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    await _storage.init();
    final content = await _storage.getLocalBookContent(widget.book.id);
    if (mounted) setState(() { _content = content; _loading = false; });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final bgColor = isDark ? const Color(0xFF1A1A2E) : const Color(0xFFFAF8F5);
    final textColor = isDark ? Colors.white.withValues(alpha: 0.85) : const Color(0xFF2D3436);

    return Scaffold(
      backgroundColor: bgColor,
      appBar: AppBar(
        title: Text(widget.book.title, style: const TextStyle(fontSize: 16)),
        backgroundColor: bgColor,
        actions: [
          IconButton(
            icon: const Icon(Icons.text_decrease, size: 20),
            onPressed: () => setState(() => _fontSize = (_fontSize - 1).clamp(12, 28)),
          ),
          IconButton(
            icon: const Icon(Icons.text_increase, size: 20),
            onPressed: () => setState(() => _fontSize = (_fontSize + 1).clamp(12, 28)),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _content == null
              ? Center(child: Text('无法读取文件', style: TextStyle(color: theme.colorScheme.onSurface.withValues(alpha: 0.5))))
              : Scrollbar(
                  controller: _scrollController,
                  child: SingleChildScrollView(
                    controller: _scrollController,
                    padding: const EdgeInsets.fromLTRB(20, 16, 20, 80),
                    child: SelectableText(
                      _content!,
                      style: TextStyle(
                        fontSize: _fontSize,
                        height: 1.8,
                        color: textColor,
                        letterSpacing: 0.3,
                      ),
                    ),
                  ),
                ),
    );
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }
}
