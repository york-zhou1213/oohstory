import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_html/flutter_html.dart';
import 'package:just_audio/just_audio.dart';

import '../features/local_content/local_content_service.dart';
import '../services/dictionary_definition_sanitizer.dart';
import '../services/local_dictionary_service.dart';

Future<void> showLocalDictionaryLookup({
  required BuildContext context,
  required LocalDictionaryService service,
  String initialTerm = '',
}) async {
  var term = initialTerm.trim();
  if (term.isEmpty) {
    final controller = TextEditingController();
    final action = await showDialog<_DictionaryQueryAction>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('本地词典'),
        content: TextField(
          controller: controller,
          autofocus: true,
          textInputAction: TextInputAction.search,
          maxLength: 512,
          decoration: const InputDecoration(
            hintText: '输入要查询的词',
            prefixIcon: Icon(Icons.menu_book_rounded),
          ),
          onSubmitted: (value) => Navigator.pop(
            dialogContext,
            _DictionaryQueryAction.lookup(value.trim()),
          ),
        ),
        actions: [
          TextButton.icon(
            onPressed: () => Navigator.pop(
              dialogContext,
              const _DictionaryQueryAction.manage(),
            ),
            icon: const Icon(Icons.settings_outlined),
            label: const Text('管理词典'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(
              dialogContext,
              _DictionaryQueryAction.lookup(controller.text.trim()),
            ),
            child: const Text('查询'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (action == null) return;
    if (action.manage) {
      if (context.mounted) {
        await showLocalDictionaryManager(context: context, service: service);
      }
      return;
    }
    term = action.term;
  }
  if (term.isEmpty || !context.mounted) return;
  if (!service.list().any((item) => item.enabled)) {
    await showLocalDictionaryManager(context: context, service: service);
    if (!context.mounted || !service.list().any((item) => item.enabled)) return;
  }
  try {
    final results = await service.lookup(term);
    if (!context.mounted) return;
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (_) => _DictionaryDefinitionSheet(
        service: service,
        term: term,
        results: results,
      ),
    );
  } on Object {
    if (!context.mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('词典文件无法读取或已被修改，请重新导入')));
  }
}

Future<void> showLocalDictionaryManager({
  required BuildContext context,
  required LocalDictionaryService service,
}) => showModalBottomSheet<void>(
  context: context,
  showDragHandle: true,
  isScrollControlled: true,
  builder: (_) => _DictionaryManagerSheet(service: service),
);

class _DictionaryManagerSheet extends StatefulWidget {
  const _DictionaryManagerSheet({required this.service});

  final LocalDictionaryService service;

  @override
  State<_DictionaryManagerSheet> createState() =>
      _DictionaryManagerSheetState();
}

class _DictionaryManagerSheetState extends State<_DictionaryManagerSheet> {
  bool _busy = false;
  String? _message;

  Future<void> _import() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _message = '请选择 MDX 词典文件';
    });
    try {
      final mdx = await pickLocalContentFile(extensions: const <String>['mdx']);
      if (mdx == null) {
        if (mounted) setState(() => _busy = false);
        return;
      }
      final mdxBytes = await mdx.read(
        maxBytes: LocalDictionaryService.maxDictionaryBytes,
      );
      if (!mounted) return;
      final includeMdd = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('是否有配套 MDD？'),
          content: const Text('MDD 可提供词条图片和本地发音。没有时可直接跳过。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('仅导入 MDX'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext, true),
              child: const Text('选择 MDD'),
            ),
          ],
        ),
      );
      Uint8List? mddBytes;
      if (includeMdd == true) {
        final mdd = await pickLocalContentFile(
          extensions: const <String>['mdd'],
        );
        if (mdd == null) {
          if (mounted) setState(() => _busy = false);
          return;
        }
        if (_baseName(mdx.name) != _baseName(mdd.name)) {
          throw const LocalContentException('MDX 与 MDD 文件名不一致，请选择同名配套文件');
        }
        mddBytes = await mdd.read(
          maxBytes: LocalDictionaryService.maxResourceBytes,
        );
      }
      setState(() => _message = '正在校验并复制到应用本地目录…');
      final info = await widget.service.import(
        name: mdx.name,
        mdxBytes: mdxBytes,
        mddBytes: mddBytes,
      );
      if (!mounted) return;
      setState(() {
        _busy = false;
        _message = '已导入 ${info.name}（${info.entryCount} 条）';
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _message = error is LocalContentException
            ? error.message
            : '导入失败：文件损坏、加密或超出安全上限';
      });
    }
  }

  Future<void> _remove(LocalDictionaryInfo info) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('移除 ${info.name}？'),
        content: const Text('只会删除应用自己保存的词典副本，不会删除你原来的文件。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('移除'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await widget.service.remove(info.id);
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final items = widget.service.list();
    return SafeArea(
      child: SizedBox(
        height: MediaQuery.sizeOf(context).height * .78,
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 12, 12),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      '本地 MDX 词典',
                      style: Theme.of(context).textTheme.titleLarge?.copyWith(
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                  ),
                  FilledButton.icon(
                    onPressed: _busy ? null : _import,
                    icon: const Icon(Icons.add_rounded),
                    label: const Text('导入'),
                  ),
                ],
              ),
            ),
            if (_message != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: Text(_message!),
                ),
              ),
            Expanded(
              child: items.isEmpty
                  ? const Center(
                      child: Padding(
                        padding: EdgeInsets.all(24),
                        child: Text('尚未导入词典。查询完全在本机进行，不会上传词语或词典内容。'),
                      ),
                    )
                  : ReorderableListView.builder(
                      padding: const EdgeInsets.fromLTRB(8, 0, 8, 24),
                      itemCount: items.length,
                      onReorder: (oldIndex, newIndex) async {
                        if (newIndex > oldIndex) newIndex--;
                        final delta = newIndex - oldIndex;
                        if (delta == 0) return;
                        var current = oldIndex;
                        while (current != newIndex) {
                          final step = delta.isNegative ? -1 : 1;
                          await widget.service.move(items[oldIndex].id, step);
                          current += step;
                        }
                        if (mounted) setState(() {});
                      },
                      itemBuilder: (context, index) {
                        final item = items[index];
                        return Card(
                          key: ValueKey(item.id),
                          child: ListTile(
                            leading: Switch(
                              value: item.enabled,
                              onChanged: (value) async {
                                await widget.service.setEnabled(item.id, value);
                                if (mounted) setState(() {});
                              },
                            ),
                            title: Text(item.name),
                            subtitle: Text(
                              '${item.entryCount} 条 · ${_formatBytes(item.mdxSize)}'
                              '${item.hasResources ? ' · 含 MDD' : ''}',
                            ),
                            trailing: IconButton(
                              onPressed: () => _remove(item),
                              tooltip: '移除词典',
                              icon: const Icon(Icons.delete_outline_rounded),
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
  }
}

class _DictionaryDefinitionSheet extends StatefulWidget {
  const _DictionaryDefinitionSheet({
    required this.service,
    required this.term,
    required this.results,
  });

  final LocalDictionaryService service;
  final String term;
  final List<LocalDictionaryResult> results;

  @override
  State<_DictionaryDefinitionSheet> createState() =>
      _DictionaryDefinitionSheetState();
}

class _DictionaryDefinitionSheetState
    extends State<_DictionaryDefinitionSheet> {
  AudioPlayer? _audioPlayer;
  late final Future<List<_RenderedDictionaryResult>> _rendered = _render();
  String? _playing;

  Future<List<_RenderedDictionaryResult>> _render() async {
    const sanitizer = DictionaryDefinitionSanitizer();
    final rendered = <_RenderedDictionaryResult>[];
    for (final result in widget.results) {
      final entries = <SanitizedDictionaryDefinition>[];
      for (final entry in result.entries) {
        entries.add(
          await sanitizer.sanitize(
            entry.definition,
            loadResource: (path) =>
                widget.service.resource(result.dictionaryId, path),
          ),
        );
      }
      rendered.add(_RenderedDictionaryResult(result, entries));
    }
    return rendered;
  }

  Future<void> _openLink(String? url, String dictionaryId) async {
    if (url == null) return;
    if (url.startsWith('audio://')) {
      try {
        await _play(
          dictionaryId,
          Uri.decodeComponent(url.substring('audio://'.length)),
        );
      } on FormatException {
        return;
      }
      return;
    }
    if (!url.startsWith('entry://')) return;
    final raw = url.substring('entry://'.length);
    late String term;
    try {
      term = Uri.decodeComponent(raw);
    } on FormatException {
      return;
    }
    if (!mounted) return;
    final parentContext = Navigator.of(context).context;
    Navigator.pop(context);
    await showLocalDictionaryLookup(
      context: parentContext,
      service: widget.service,
      initialTerm: term,
    );
  }

  Future<void> _play(String dictionaryId, String resource) async {
    final bytes = await widget.service.resource(dictionaryId, resource);
    final mime = bytes == null ? null : _audioMime(bytes, resource);
    if (bytes == null || mime == null || bytes.length > 4 * 1024 * 1024) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('此本地发音资源格式不受支持')));
      }
      return;
    }
    final player = _audioPlayer ??= AudioPlayer();
    await player.setAudioSource(_MemoryAudioSource(bytes, mime));
    await player.play();
    if (mounted) setState(() => _playing = resource);
  }

  @override
  Widget build(BuildContext context) => SafeArea(
    child: SizedBox(
      height: MediaQuery.sizeOf(context).height * .82,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 12, 10),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    '“${widget.term}”的本地释义',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                ),
                IconButton(
                  tooltip: '管理词典',
                  onPressed: () async {
                    final parentContext = Navigator.of(context).context;
                    Navigator.pop(context);
                    await showLocalDictionaryManager(
                      context: parentContext,
                      service: widget.service,
                    );
                  },
                  icon: const Icon(Icons.settings_outlined),
                ),
              ],
            ),
          ),
          Expanded(
            child: widget.results.isEmpty
                ? const Center(child: Text('已启用的词典中没有找到该词'))
                : FutureBuilder<List<_RenderedDictionaryResult>>(
                    future: _rendered,
                    builder: (context, snapshot) {
                      if (snapshot.hasError) {
                        return const Center(child: Text('词条内容损坏，无法安全显示'));
                      }
                      final results = snapshot.data;
                      if (results == null) {
                        return const Center(child: CircularProgressIndicator());
                      }
                      return ListView.builder(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                        itemCount: results.length,
                        itemBuilder: (context, index) {
                          final result = results[index];
                          return Card(
                            margin: const EdgeInsets.only(bottom: 12),
                            child: Padding(
                              padding: const EdgeInsets.all(16),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    result.source.dictionaryName,
                                    style: Theme.of(context)
                                        .textTheme
                                        .labelLarge
                                        ?.copyWith(fontWeight: FontWeight.w900),
                                  ),
                                  for (final entry in result.entries) ...[
                                    Html(
                                      data: entry.html,
                                      onLinkTap: (url, _, __) => unawaited(
                                        _openLink(
                                          url,
                                          result.source.dictionaryId,
                                        ),
                                      ),
                                    ),
                                    for (final audio in entry.audioResources)
                                      TextButton.icon(
                                        onPressed: () => _play(
                                          result.source.dictionaryId,
                                          audio,
                                        ),
                                        icon: Icon(
                                          _playing == audio
                                              ? Icons.volume_up_rounded
                                              : Icons.volume_up_outlined,
                                        ),
                                        label: const Text('播放本地发音'),
                                      ),
                                    const Divider(),
                                  ],
                                ],
                              ),
                            ),
                          );
                        },
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
    final player = _audioPlayer;
    if (player != null) unawaited(player.dispose());
    super.dispose();
  }
}

class _MemoryAudioSource extends StreamAudioSource {
  _MemoryAudioSource(this.bytes, this.mime);

  final Uint8List bytes;
  final String mime;

  @override
  Future<StreamAudioResponse> request([int? start, int? end]) async {
    final first = (start ?? 0).clamp(0, bytes.length);
    final last = (end ?? bytes.length).clamp(first, bytes.length);
    return StreamAudioResponse(
      sourceLength: bytes.length,
      contentLength: last - first,
      offset: first,
      contentType: mime,
      stream: Stream<List<int>>.value(bytes.sublist(first, last)),
    );
  }
}

class _RenderedDictionaryResult {
  const _RenderedDictionaryResult(this.source, this.entries);
  final LocalDictionaryResult source;
  final List<SanitizedDictionaryDefinition> entries;
}

class _DictionaryQueryAction {
  const _DictionaryQueryAction.lookup(this.term) : manage = false;
  const _DictionaryQueryAction.manage() : manage = true, term = '';
  final bool manage;
  final String term;
}

String _baseName(String value) {
  final name = value.replaceAll('\\', '/').split('/').last;
  final dot = name.lastIndexOf('.');
  return (dot <= 0 ? name : name.substring(0, dot)).toLowerCase();
}

String _formatBytes(int bytes) {
  if (bytes >= 1024 * 1024) {
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MiB';
  }
  if (bytes >= 1024) return '${(bytes / 1024).toStringAsFixed(1)} KiB';
  return '$bytes B';
}

String? _audioMime(Uint8List bytes, String path) {
  if (bytes.length >= 4 &&
      bytes[0] == 0x4f &&
      bytes[1] == 0x67 &&
      bytes[2] == 0x67 &&
      bytes[3] == 0x53) {
    return 'audio/ogg';
  }
  if (bytes.length >= 12 &&
      String.fromCharCodes(bytes.sublist(0, 4)) == 'RIFF' &&
      String.fromCharCodes(bytes.sublist(8, 12)) == 'WAVE') {
    return 'audio/wav';
  }
  if ((bytes.length >= 3 &&
          String.fromCharCodes(bytes.sublist(0, 3)) == 'ID3') ||
      (bytes.length >= 2 && bytes[0] == 0xff && (bytes[1] & 0xe0) == 0xe0)) {
    return 'audio/mpeg';
  }
  if (path.toLowerCase().endsWith('.mp3') && bytes.length >= 3) {
    return 'audio/mpeg';
  }
  return null;
}
