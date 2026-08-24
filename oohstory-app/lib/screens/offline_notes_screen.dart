import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../models/reader_preferences.dart';
import '../services/local_storage_service.dart';

class OfflineNotesScreen extends StatefulWidget {
  const OfflineNotesScreen({super.key});

  @override
  State<OfflineNotesScreen> createState() => _OfflineNotesScreenState();
}

class _OfflineNotesScreenState extends State<OfflineNotesScreen> {
  final _storage = LocalStorageService();
  final _search = TextEditingController();
  List<OfflineAnnotation> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _initialize();
  }

  Future<void> _initialize() async {
    await _storage.init();
    _reload();
    if (mounted) setState(() => _loading = false);
  }

  void _reload() {
    final query = _search.text.trim().toLowerCase();
    final items = _storage.getAnnotations();
    setState(() {
      _items = query.isEmpty
          ? items
          : items
                .where(
                  (item) =>
                      item.excerpt.toLowerCase().contains(query) ||
                      item.note.toLowerCase().contains(query),
                )
                .toList();
    });
  }

  Future<void> _export() async {
    final archive = await _storage.createAnnotationExport();
    final outputPath = await FilePicker.platform.saveFile(
      dialogTitle: '导出阅读批注',
      fileName: archive.uri.pathSegments.last,
      type: FileType.custom,
      allowedExtensions: const ['zip'],
    );
    if (outputPath == null) return;
    await archive.copy(outputPath);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('已导出 CSV、Markdown、HTML、TXT 与 JSON')),
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('书签与批注'),
      actions: [
        IconButton(
          onPressed: _items.isEmpty ? null : _export,
          tooltip: '导出',
          icon: const Icon(Icons.ios_share_rounded),
        ),
      ],
    ),
    body: _loading
        ? const Center(child: CircularProgressIndicator())
        : Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: TextField(
                  controller: _search,
                  onChanged: (_) => _reload(),
                  decoration: const InputDecoration(
                    hintText: '搜索高亮、书签与笔记',
                    prefixIcon: Icon(Icons.search_rounded),
                  ),
                ),
              ),
              Expanded(
                child: _items.isEmpty
                    ? const Center(child: Text('还没有离线批注'))
                    : ListView.separated(
                        padding: const EdgeInsets.fromLTRB(12, 0, 12, 24),
                        itemCount: _items.length,
                        separatorBuilder: (_, __) => const Divider(height: 1),
                        itemBuilder: (context, index) {
                          final item = _items[index];
                          return ListTile(
                            leading: CircleAvatar(
                              child: Icon(switch (item.type) {
                                'note' => Icons.edit_note_rounded,
                                'highlight' => Icons.highlight_rounded,
                                _ => Icons.bookmark_rounded,
                              }),
                            ),
                            title: Text(
                              item.note.isNotEmpty ? item.note : item.excerpt,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                            subtitle: Text(
                              '${(item.progress * 100).round()}% · ${item.excerpt}',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            trailing: IconButton(
                              onPressed: () {
                                _storage.removeAnnotation(item.id);
                                _reload();
                              },
                              icon: const Icon(Icons.delete_outline_rounded),
                            ),
                          );
                        },
                      ),
              ),
            ],
          ),
  );

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }
}
