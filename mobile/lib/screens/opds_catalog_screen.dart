import 'dart:async';

import 'package:flutter/material.dart';

import '../services/local_storage_service.dart';
import '../services/opds_catalog_service.dart';

class OpdsCatalogScreen extends StatefulWidget {
  final LocalStorageService storage;

  const OpdsCatalogScreen({super.key, required this.storage});

  @override
  State<OpdsCatalogScreen> createState() => _OpdsCatalogScreenState();
}

class _OpdsCatalogScreenState extends State<OpdsCatalogScreen> {
  final _service = OpdsCatalogService();
  final _urlController = TextEditingController();
  List<OpdsBookEntry> _books = const [];
  Object? _error;
  bool _loading = false;
  final Set<Uri> _downloading = {};

  Future<void> _load() async {
    final uri = Uri.tryParse(_urlController.text.trim());
    if (uri == null) {
      setState(() => _error = const FormatException('请输入有效的 OPDS 地址'));
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final books = await _service.fetch(uri);
      if (!mounted) return;
      setState(() => _books = books);
    } catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _download(OpdsBookEntry book) async {
    setState(() => _downloading.add(book.downloadUri));
    try {
      final imported = await _service.downloadAndImport(book, widget.storage);
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('「${imported.title}」已下载到离线书架')));
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('下载失败：$error')));
    } finally {
      if (mounted) setState(() => _downloading.remove(book.downloadUri));
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('OPDS / 局域网书库')),
    body: SafeArea(
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            child: TextField(
              controller: _urlController,
              keyboardType: TextInputType.url,
              textInputAction: TextInputAction.go,
              onSubmitted: (_) => unawaited(_load()),
              decoration: InputDecoration(
                labelText: 'OPDS 1 / 2 目录地址',
                hintText: 'https://library.example/opds',
                prefixIcon: const Icon(Icons.lan_outlined),
                suffixIcon: IconButton(
                  tooltip: '读取目录',
                  onPressed: _loading ? null : _load,
                  icon: const Icon(Icons.arrow_forward_rounded),
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 4),
            child: Text(
              '目录只用于发现书籍；下载后的 EPUB、PDF、CBZ 会完整保存在本机，可断网打开。',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          if (_loading) const LinearProgressIndicator(),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(20),
              child: Text('目录读取失败：$_error', textAlign: TextAlign.center),
            ),
          Expanded(
            child: _books.isEmpty && !_loading && _error == null
                ? const Center(child: Text('输入 OPDS 地址读取书库'))
                : ListView.separated(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
                    itemCount: _books.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final book = _books[index];
                      final downloading = _downloading.contains(
                        book.downloadUri,
                      );
                      return ListTile(
                        leading: CircleAvatar(
                          child: Text(book.format.toUpperCase()),
                        ),
                        title: Text(
                          book.title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        subtitle: book.author.isEmpty
                            ? null
                            : Text(book.author),
                        trailing: downloading
                            ? const SizedBox.square(
                                dimension: 22,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              )
                            : IconButton(
                                tooltip: '下载到本机',
                                onPressed: () => _download(book),
                                icon: const Icon(
                                  Icons.download_for_offline_outlined,
                                ),
                              ),
                      );
                    },
                  ),
          ),
        ],
      ),
    ),
  );

  @override
  void dispose() {
    _service.close();
    _urlController.dispose();
    super.dispose();
  }
}
