import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../services/account_service.dart';
import '../theme/app_theme.dart';
import '../widgets/account_success_toast.dart';

class SubmissionCenterScreen extends StatefulWidget {
  const SubmissionCenterScreen({super.key});

  @override
  State<SubmissionCenterScreen> createState() => _SubmissionCenterScreenState();
}

class _SubmissionCenterScreenState extends State<SubmissionCenterScreen> {
  final _account = AccountService.instance;
  final _formKey = GlobalKey<FormState>();
  final _title = TextEditingController();
  final _author = TextEditingController();
  final _summary = TextEditingController();
  final _source = TextEditingController();
  final _authorization = TextEditingController();
  List<Map<String, dynamic>> _uploads = const [];
  List<Map<String, dynamic>> _novels = const [];
  List<String> _categories = const [];
  String? _category;
  String _serialization = 'ongoing';
  String? _manuscript;
  String? _cover;
  int _step = 0;
  bool _loading = true;
  bool _submitting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final controller in [
      _title,
      _author,
      _summary,
      _source,
      _authorization,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    setState(() {
      if (!silent) _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        _account.uploads(),
        _account.novelSubmissions(),
        _account.categories(),
      ]);
      _uploads = results[0];
      _novels = results[1];
      _categories = results[2]
          .map((item) => item['name'] as String? ?? '')
          .where((name) => name.isNotEmpty)
          .toList();
      if (_category != null && !_categories.contains(_category)) {
        _category = null;
      }
    } catch (error) {
      _error = error.toString();
    }
    if (mounted) setState(() => _loading = false);
  }

  void _message(String text) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(text), behavior: SnackBarBehavior.floating),
    );
  }

  Future<String?> _pick(List<String> extensions) async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: extensions,
    );
    return result?.files.single.path;
  }

  Future<void> _uploadDeconstruction() async {
    final path = await _pick(const ['zip']);
    if (path == null) return;
    setState(() => _submitting = true);
    try {
      final result = await _account.uploadSource(path);
      if (!mounted) return;
      showAccountSuccessToast(
        context,
        message: result['message'] as String? ?? '上传成功，正在等待审核',
      );
      await _load(silent: true);
    } catch (error) {
      _message(error.toString());
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  bool _validateStep() {
    if (_step == 0) {
      if (_title.text.trim().isEmpty ||
          _author.text.trim().isEmpty ||
          _category == null ||
          _summary.text.trim().length < 20) {
        _formKey.currentState?.validate();
        _message('请完整填写作品资料');
        return false;
      }
    } else if (_step == 1 && (_manuscript == null || _cover == null)) {
      _message('请选择正文与书籍封面');
      return false;
    }
    return true;
  }

  Future<void> _submitNovel() async {
    if (_source.text.trim().isEmpty || _authorization.text.trim().length < 10) {
      _formKey.currentState?.validate();
      _message('请完整填写来源和授权说明');
      return;
    }
    setState(() => _submitting = true);
    try {
      final result = await _account.uploadNovel(
        metadata: {
          'title': _title.text.trim(),
          'author': _author.text.trim(),
          'category': _category!,
          'serialization_status': _serialization,
          'summary': _summary.text.trim(),
          'source': _source.text.trim(),
          'authorization': _authorization.text.trim(),
        },
        manuscriptPath: _manuscript!,
        coverPath: _cover!,
      );
      _message(result['message'] as String? ?? '已提交审核');
      _formKey.currentState?.reset();
      _title.clear();
      _author.clear();
      _summary.clear();
      _source.clear();
      _authorization.clear();
      _category = null;
      _serialization = 'ongoing';
      _manuscript = null;
      _cover = null;
      _step = 0;
      await _load();
    } catch (error) {
      _message(error.toString());
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('我的投稿')),
      body: _loading && _uploads.isEmpty && _novels.isEmpty
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 36),
                children: [
                  _hero(),
                  _reviewRules(),
                  if (_error != null) ...[
                    const SizedBox(height: 10),
                    Text(
                      _error!,
                      style: const TextStyle(color: Colors.redAccent),
                    ),
                  ],
                  const SizedBox(height: 14),
                  _panel(
                    title: '上传我的拆书文',
                    subtitle: '请上传 ZIP。我们会长/短篇结构审核与内容复核完后，并通过消息中心告知您上传结果。',
                    child: SizedBox(
                      width: double.infinity,
                      child: FilledButton.icon(
                        onPressed: _submitting ? null : _uploadDeconstruction,
                        icon: const Icon(Icons.archive_outlined),
                        label: const Text('选择 ZIP 并开始审核'),
                      ),
                    ),
                  ),
                  _panel(
                    title: '上传小说',
                    subtitle: '作品资料、正文封面、来源授权三步完成。',
                    child: _novelWizard(),
                  ),
                  _panel(
                    title: '审核与入库记录',
                    subtitle: '审核结果与缺失文件会同步发送到消息中心。',
                    child: _records(),
                  ),
                ],
              ),
            ),
    );
  }

  Widget _hero() => Container(
    padding: const EdgeInsets.all(20),
    decoration: BoxDecoration(
      gradient: AppTheme.heroGradient,
      borderRadius: BorderRadius.circular(22),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'CONTRIBUTOR STUDIO',
          style: TextStyle(
            color: Colors.white.withValues(alpha: .58),
            fontSize: 10,
            letterSpacing: 1.8,
            fontWeight: FontWeight.w800,
          ),
        ),
        const SizedBox(height: 7),
        const Text(
          '投稿中心',
          style: TextStyle(
            color: Colors.white,
            fontSize: 24,
            fontWeight: FontWeight.w900,
          ),
        ),
        const SizedBox(height: 5),
        Text(
          '文件会安全隔离、识别结构并完成审核，通过后才进入正式入库流程。',
          style: TextStyle(
            color: Colors.white.withValues(alpha: .7),
            fontSize: 12,
            height: 1.5,
          ),
        ),
      ],
    ),
  );

  Widget _reviewRules() {
    final theme = Theme.of(context);
    const rules = [
      '覆盖 TXT 全文、EPUB 内部章节及拆书结构内全部文本，不只检查标题、封面或开头。',
      '标题、简介、报告与正文主题必须一致；伪装成正常书籍的广告或违法内容会被驳回。',
      '禁止涉黄、涉毒、涉赌、诈骗、违法交易、广告引流、网址、邮箱、联系方式及二维码。',
    ];
    return Container(
      margin: const EdgeInsets.only(top: 14),
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF8F1),
        border: Border.all(color: const Color(0xFFE8D6C5)),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '审核范围',
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w800,
              color: const Color(0xFF493A48),
            ),
          ),
          const SizedBox(height: 7),
          for (final rule in rules)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('• ', style: TextStyle(color: Color(0xFFB46A78))),
                  Expanded(
                    child: Text(
                      rule,
                      style: theme.textTheme.bodySmall?.copyWith(
                        height: 1.55,
                        color: const Color(0xFF62565D),
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _panel({
    required String title,
    required String subtitle,
    required Widget child,
  }) {
    final theme = Theme.of(context);
    return Container(
      margin: const EdgeInsets.only(top: 14),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: theme.brightness == Brightness.dark
            ? const Color(0xFF1E1E30)
            : Colors.white,
        borderRadius: BorderRadius.circular(18),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            subtitle,
            style: TextStyle(
              fontSize: 12,
              height: 1.45,
              color: theme.colorScheme.onSurface.withValues(alpha: .52),
            ),
          ),
          const SizedBox(height: 16),
          child,
        ],
      ),
    );
  }

  Widget _novelWizard() => Form(
    key: _formKey,
    child: Column(
      children: [
        Row(
          children: List.generate(3, (index) {
            final active = index == _step;
            return Expanded(
              child: Container(
                margin: EdgeInsets.only(right: index == 2 ? 0 : 6),
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: active
                      ? AppTheme.seedPurple.withValues(alpha: .14)
                      : Theme.of(
                          context,
                        ).colorScheme.onSurface.withValues(alpha: .04),
                  borderRadius: BorderRadius.circular(10),
                ),
                alignment: Alignment.center,
                child: Text(
                  '${index + 1} ${['作品资料', '正文封面', '来源授权'][index]}',
                  style: TextStyle(
                    fontSize: 10,
                    color: active ? AppTheme.seedPurple : null,
                    fontWeight: active ? FontWeight.w800 : FontWeight.w500,
                  ),
                ),
              ),
            );
          }),
        ),
        const SizedBox(height: 16),
        IndexedStack(
          index: _step,
          children: [_metadataStep(), _filesStep(), _authorizationStep()],
        ),
        const SizedBox(height: 16),
        Row(
          children: [
            if (_step > 0)
              TextButton(
                onPressed: _submitting ? null : () => setState(() => _step--),
                child: const Text('上一步'),
              ),
            const Spacer(),
            if (_step < 2)
              FilledButton(
                onPressed: _submitting
                    ? null
                    : () {
                        if (_validateStep()) setState(() => _step++);
                      },
                child: const Text('下一步'),
              )
            else
              FilledButton.icon(
                onPressed: _submitting ? null : _submitNovel,
                icon: const Icon(Icons.send, size: 18),
                label: Text(_submitting ? '提交中…' : '提交审核'),
              ),
          ],
        ),
      ],
    ),
  );

  Widget _metadataStep() => Column(
    children: [
      TextFormField(
        controller: _title,
        maxLength: 160,
        decoration: const InputDecoration(labelText: '书名'),
        validator: (value) => (value ?? '').trim().isEmpty ? '请填写书名' : null,
      ),
      const SizedBox(height: 10),
      TextFormField(
        controller: _author,
        maxLength: 100,
        decoration: const InputDecoration(labelText: '作者'),
        validator: (value) => (value ?? '').trim().isEmpty ? '请填写作者' : null,
      ),
      const SizedBox(height: 10),
      DropdownButtonFormField<String>(
        value: _category,
        isExpanded: true,
        decoration: const InputDecoration(labelText: '系统分类'),
        hint: const Text('请选择当前书库分类'),
        items: _categories
            .map((name) => DropdownMenuItem(value: name, child: Text(name)))
            .toList(),
        onChanged: (value) => setState(() => _category = value),
        validator: (value) => value == null ? '请选择分类' : null,
      ),
      const SizedBox(height: 10),
      DropdownButtonFormField<String>(
        value: _serialization,
        decoration: const InputDecoration(labelText: '连载状态'),
        items: const [
          DropdownMenuItem(value: 'ongoing', child: Text('连载中')),
          DropdownMenuItem(value: 'finished', child: Text('已完结')),
        ],
        onChanged: (value) => _serialization = value ?? 'ongoing',
      ),
      const SizedBox(height: 10),
      TextFormField(
        controller: _summary,
        minLines: 4,
        maxLines: 7,
        maxLength: 4000,
        decoration: const InputDecoration(labelText: '作品简介'),
        validator: (value) =>
            (value ?? '').trim().length < 20 ? '简介至少 20 个字符' : null,
      ),
    ],
  );

  Widget _filesStep() => Column(
    children: [
      _fileTile(
        title: '正文 TXT / EPUB',
        path: _manuscript,
        icon: Icons.description_outlined,
        onTap: () async {
          final path = await _pick(const ['txt', 'epub']);
          if (path != null) setState(() => _manuscript = path);
        },
      ),
      const SizedBox(height: 12),
      _fileTile(
        title: '书籍封面 JPEG / PNG / WebP',
        path: _cover,
        icon: Icons.image_outlined,
        onTap: () async {
          final path = await _pick(const ['jpg', 'jpeg', 'png', 'webp']);
          if (path != null) setState(() => _cover = path);
        },
      ),
    ],
  );

  Widget _authorizationStep() => Column(
    children: [
      TextFormField(
        controller: _source,
        maxLength: 500,
        decoration: const InputDecoration(
          labelText: '作品来源',
          hintText: '原创 / 开源地址 / 授权方',
        ),
        validator: (value) => (value ?? '').trim().isEmpty ? '请填写作品来源' : null,
      ),
      const SizedBox(height: 10),
      TextFormField(
        controller: _authorization,
        minLines: 4,
        maxLines: 7,
        maxLength: 2000,
        decoration: const InputDecoration(labelText: '版权或授权说明'),
        validator: (value) =>
            (value ?? '').trim().length < 10 ? '授权说明至少 10 个字符' : null,
      ),
      const SizedBox(height: 8),
      Text(
        '提交后会先进入隔离沙箱、ClamAV 验毒与审核；未通过不会写入书库。',
        style: TextStyle(
          fontSize: 11,
          color: Theme.of(context).colorScheme.onSurface.withValues(alpha: .5),
        ),
      ),
    ],
  );

  Widget _fileTile({
    required String title,
    required String? path,
    required IconData icon,
    required VoidCallback onTap,
  }) => InkWell(
    onTap: onTap,
    borderRadius: BorderRadius.circular(14),
    child: Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppTheme.seedPurple.withValues(alpha: .07),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppTheme.seedPurple.withValues(alpha: .18)),
      ),
      child: Row(
        children: [
          Icon(icon, color: AppTheme.seedPurple),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 3),
                Text(
                  path == null ? '点击选择文件' : File(path).uri.pathSegments.last,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11),
                ),
              ],
            ),
          ),
          const Icon(Icons.chevron_right),
        ],
      ),
    ),
  );

  Widget _records() {
    final records = [
      ..._novels.map((item) => (item, '小说投稿')),
      ..._uploads.map((item) => (item, '拆书文')),
    ];
    if (records.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 20),
        child: Center(child: Text('还没有投稿记录。')),
      );
    }
    return Column(
      children: records.map((record) {
        final item = record.$1;
        final missing =
            (item['structure_report'] as Map?)?['missing_files'] as List? ??
            (item['review_result'] as Map?)?['missing_files'] as List? ??
            const [];
        return Container(
          margin: const EdgeInsets.only(bottom: 9),
          padding: const EdgeInsets.all(13),
          decoration: BoxDecoration(
            color: Theme.of(
              context,
            ).colorScheme.onSurface.withValues(alpha: .035),
            borderRadius: BorderRadius.circular(13),
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      record.$2,
                      style: const TextStyle(
                        fontSize: 10,
                        color: AppTheme.seedPurple,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      item['title'] as String? ??
                          item['original_filename'] as String? ??
                          '未命名投稿',
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                    if (missing.isNotEmpty)
                      Text(
                        '缺少：${missing.join('、')}',
                        style: const TextStyle(
                          fontSize: 11,
                          color: Colors.redAccent,
                        ),
                      ),
                    if ((item['rejection_reason'] as String? ?? '').isNotEmpty)
                      Text(
                        item['rejection_reason'] as String,
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 11,
                          color: Colors.redAccent,
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              _status(item['status'] as String? ?? ''),
            ],
          ),
        );
      }).toList(),
    );
  }

  Widget _status(String status) {
    final label = switch (status) {
      'quarantined' => '隔离扫描中',
      'clean_queued' => '归纳队列',
      'ai_pending' => '等待审核',
      'reviewing' => '审核中',
      'approved' => '等待入库',
      'completed' => '已入库',
      'rejected' => '已驳回',
      _ => status,
    };
    final danger = status == 'rejected';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: (danger ? Colors.redAccent : AppTheme.seedPurple).withValues(
          alpha: .1,
        ),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.w700,
          color: danger ? Colors.redAccent : AppTheme.seedPurple,
        ),
      ),
    );
  }
}
