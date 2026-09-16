import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../adapters/cloud/cloud.dart';
import '../../core/models.dart';
import '../../core/product_capabilities.dart';
import '../../theme/app_theme.dart';
import '../local_content/local_content_hub.dart';
import '../local_content/local_content_service.dart';
import 'cloud_connection.dart';
import 'cloud_library_service.dart';

typedef CloudBookPicker =
    Future<LocalPickedFile?> Function(List<String> extensions);

Future<LocalPickedFile?> _defaultCloudBookPicker(List<String> extensions) =>
    pickLocalContentFile(extensions: extensions);

class CloudConnectionsScreen extends StatefulWidget {
  const CloudConnectionsScreen({
    super.key,
    this.capabilities = ProductCapabilityProfile.production,
    this.manager,
  });

  final ProductCapabilityProfile capabilities;
  final CloudConnectionManager? manager;

  @override
  State<CloudConnectionsScreen> createState() => _CloudConnectionsScreenState();
}

class _CloudConnectionsScreenState extends State<CloudConnectionsScreen> {
  CloudConnectionManager? _manager;
  List<CloudConnectionConfig> _connections = const [];
  Object? _loadError;

  @override
  void initState() {
    super.initState();
    _initialize();
  }

  Future<void> _initialize() async {
    try {
      final manager =
          widget.manager ??
          CloudConnectionManager(
            repository: CloudConnectionRepository(
              preferences: await SharedPreferences.getInstance(),
              credentialStore: const FlutterSecureCredentialStore(),
            ),
          );
      final connections = manager.repository.load();
      if (!mounted) return;
      setState(() {
        _manager = manager;
        _connections = connections;
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _loadError = error);
    }
  }

  Future<void> _add(CloudProviderKind provider) async {
    final manager = _manager;
    if (manager == null) return;
    final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) =>
            CloudConnectionEditorScreen(provider: provider, manager: manager),
      ),
    );
    if (saved == true && mounted) {
      setState(() => _connections = manager.repository.load());
    }
  }

  Future<void> _remove(CloudConnectionConfig connection) async {
    final manager = _manager;
    if (manager == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('断开 ${connection.name}？'),
        content: const Text(
          '会删除本机连接配置、安全存储凭据和尚未发送的离线操作，不会删除任何云端文件。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('断开'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      final store = await createPersistentOfflineMutationStore(
        credentialStore: manager.repository.credentialStore,
      );
      final scope = CloudMutationScope(
        providerId: connection.provider.providerId,
        accountId: connection.id,
      );
      for (final mutation in await store.pending(scope)) {
        await store.remove(scope, mutation.idempotencyKey);
      }
    } on Object {
      // Queue cleanup is best effort; disconnect must still revoke credentials.
    }
    await manager.remove(connection);
    if (!mounted) return;
    setState(() => _connections = manager.repository.load());
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('存储与同步')),
      body: SafeArea(
        child: _loadError != null
            ? _StatusPanel(
                icon: Icons.warning_amber_rounded,
                title: '无法读取云连接配置',
                detail: '本地配置可能已损坏；云端文件没有被修改。',
              )
            : _manager == null
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
                children: [
                  Text(
                    '云端书库',
                    style: theme.textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    '凭据仅保存在系统安全存储。新增连接会先验证根目录，上传不会覆盖同名文件。',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 16),
                  if (_connections.isEmpty)
                    const _StatusPanel(
                      icon: Icons.cloud_queue_rounded,
                      title: '尚未连接云书库',
                      detail: '可先连接 WebDAV，或使用最小权限的 S3 bucket。',
                    ),
                  for (final connection in _connections) ...[
                    _connectionTile(theme, connection),
                    const SizedBox(height: 10),
                  ],
                  const SizedBox(height: 10),
                  Text('添加连接', style: theme.textTheme.titleMedium),
                  const SizedBox(height: 10),
                  if (widget.capabilities.webDavEnabled)
                    _providerButton(
                      icon: Icons.folder_shared_rounded,
                      label: '连接 WebDAV',
                      onPressed: () => _add(CloudProviderKind.webDav),
                    ),
                  if (widget.capabilities.webDavEnabled &&
                      widget.capabilities.s3Enabled)
                    const SizedBox(height: 10),
                  if (widget.capabilities.s3Enabled)
                    _providerButton(
                      icon: Icons.storage_rounded,
                      label: '连接 S3',
                      onPressed: () => _add(CloudProviderKind.s3),
                    ),
                  if (!widget.capabilities.cloudLibraryEnabled)
                    const _StatusPanel(
                      icon: Icons.lock_outline_rounded,
                      title: '云书库尚未在此构建启用',
                      detail: '需要经过真实服务验收后再通过发布开关启用。',
                    ),
                ],
              ),
      ),
    );
  }

  Widget _connectionTile(ThemeData theme, CloudConnectionConfig connection) =>
      Material(
        color: theme.colorScheme.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppTheme.cardRadius),
          side: BorderSide(color: theme.colorScheme.outlineVariant),
        ),
        clipBehavior: Clip.antiAlias,
        child: ListTile(
          leading: Icon(
            connection.provider == CloudProviderKind.webDav
                ? Icons.folder_shared_rounded
                : Icons.storage_rounded,
          ),
          title: Text(connection.name),
          subtitle: Text(
            '${connection.provider.label} · ${connection.endpoint.host}',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => CloudLibraryScreen(
                connection: connection,
                manager: _manager!,
              ),
            ),
          ),
          trailing: IconButton(
            tooltip: '断开 ${connection.name}',
            onPressed: () => _remove(connection),
            icon: const Icon(Icons.link_off_rounded),
          ),
        ),
      );

  Widget _providerButton({
    required IconData icon,
    required String label,
    required VoidCallback onPressed,
  }) => SizedBox(
    height: 50,
    child: OutlinedButton.icon(
      onPressed: onPressed,
      icon: Icon(icon),
      label: Text(label),
    ),
  );
}

class CloudConnectionEditorScreen extends StatefulWidget {
  const CloudConnectionEditorScreen({
    super.key,
    required this.provider,
    required this.manager,
  });

  final CloudProviderKind provider;
  final CloudConnectionManager manager;

  @override
  State<CloudConnectionEditorScreen> createState() =>
      _CloudConnectionEditorScreenState();
}

class _CloudConnectionEditorScreenState
    extends State<CloudConnectionEditorScreen> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _name;
  late final TextEditingController _endpoint;
  late final TextEditingController _root;
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _bucket = TextEditingController();
  final _region = TextEditingController(text: 'us-east-1');
  final _accessKey = TextEditingController();
  final _secretKey = TextEditingController();
  final _sessionToken = TextEditingController();
  bool _pathStyle = true;
  bool _corsVerified = false;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _name = TextEditingController(text: widget.provider.label);
    _endpoint = TextEditingController(
      text: widget.provider == CloudProviderKind.s3
          ? 'https://s3.us-east-1.amazonaws.com'
          : '',
    );
    _root = TextEditingController(text: 'OOHStory');
    _pathStyle = widget.provider == CloudProviderKind.webDav;
  }

  @override
  void dispose() {
    for (final controller in <TextEditingController>[
      _name,
      _endpoint,
      _root,
      _username,
      _password,
      _bucket,
      _region,
      _accessKey,
      _secretKey,
      _sessionToken,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (_busy || !_formKey.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final config = CloudConnectionConfig.create(
        provider: widget.provider,
        name: _name.text,
        endpoint: Uri.parse(_endpoint.text.trim()),
        root: _root.text,
        bucket: widget.provider == CloudProviderKind.s3 ? _bucket.text : null,
        region: widget.provider == CloudProviderKind.s3 ? _region.text : null,
        pathStyle: _pathStyle,
        corsVerified: _corsVerified,
      );
      final credentials = widget.provider == CloudProviderKind.webDav
          ? CloudConnectionCredentials.webDav(
              username: _username.text,
              password: _password.text,
            )
          : CloudConnectionCredentials.s3(
              accessKey: _accessKey.text,
              secretKey: _secretKey.text,
              sessionToken: _sessionToken.text,
            );
      await widget.manager.verifyAndSave(config, credentials);
      if (!mounted) return;
      Navigator.pop(context, true);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = _connectionError(error);
      });
    }
  }

  String? _required(String? value) =>
      value == null || value.trim().isEmpty ? '此项不能为空' : null;

  @override
  Widget build(BuildContext context) {
    final isS3 = widget.provider == CloudProviderKind.s3;
    return Scaffold(
      appBar: AppBar(title: Text('连接 ${widget.provider.label}')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
          children: [
            _field(_name, '连接名称', validator: _required),
            _field(
              _endpoint,
              'HTTPS endpoint',
              hint: isS3
                  ? 'https://s3.us-east-1.amazonaws.com'
                  : 'https://dav.example.com/remote.php/dav/files/user',
              keyboardType: TextInputType.url,
              validator: _required,
            ),
            _field(_root, '限定根目录', validator: _required),
            if (!isS3) ...[
              _field(
                _username,
                '用户名',
                autofillHints: const [AutofillHints.username],
                validator: _required,
              ),
              _field(
                _password,
                '密码或应用密码',
                obscureText: true,
                autofillHints: const [AutofillHints.password],
                validator: _required,
              ),
            ] else ...[
              _field(_bucket, 'Bucket', validator: _required),
              _field(_region, 'Region', validator: _required),
              _field(_accessKey, 'Access key', validator: _required),
              _field(
                _secretKey,
                'Secret key',
                obscureText: true,
                validator: _required,
              ),
              _field(_sessionToken, 'Session token（可选）', obscureText: true),
              SwitchListTile.adaptive(
                contentPadding: EdgeInsets.zero,
                title: const Text('使用 path-style 寻址'),
                subtitle: const Text('S3-compatible 端点通常开启；AWS S3 默认关闭'),
                value: _pathStyle,
                onChanged: _busy
                    ? null
                    : (value) => setState(() => _pathStyle = value),
              ),
            ],
            if (kIsWeb)
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('我已验证此 endpoint 的 HTTPS 与 CORS'),
                subtitle: Text(
                  isS3
                      ? 'Web 正式环境应使用短期凭据或同源代理，不保存长期 S3 secret。'
                      : '浏览器会直接连接该 WebDAV 源站。',
                ),
                value: _corsVerified,
                onChanged: _busy
                    ? null
                    : (value) => setState(() => _corsVerified = value ?? false),
              ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(
                _error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            const SizedBox(height: 20),
            SizedBox(
              height: 50,
              child: FilledButton.icon(
                onPressed: _busy ? null : _save,
                icon: _busy
                    ? const SizedBox.square(
                        dimension: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.verified_user_rounded),
                label: Text(_busy ? '正在验证根目录…' : '验证并保存'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _field(
    TextEditingController controller,
    String label, {
    String? hint,
    bool obscureText = false,
    TextInputType? keyboardType,
    Iterable<String>? autofillHints,
    String? Function(String?)? validator,
  }) => Padding(
    padding: const EdgeInsets.only(bottom: 14),
    child: TextFormField(
      controller: controller,
      decoration: InputDecoration(labelText: label, hintText: hint),
      obscureText: obscureText,
      keyboardType: keyboardType,
      autofillHints: autofillHints,
      validator: validator,
      enabled: !_busy,
    ),
  );
}

class CloudLibraryScreen extends StatefulWidget {
  const CloudLibraryScreen({
    super.key,
    required this.connection,
    required this.manager,
    this.service,
    this.picker = _defaultCloudBookPicker,
  });

  final CloudConnectionConfig connection;
  final CloudConnectionManager manager;
  final CloudLibraryService? service;
  final CloudBookPicker picker;

  @override
  State<CloudLibraryScreen> createState() => _CloudLibraryScreenState();
}

class _CloudLibraryScreenState extends State<CloudLibraryScreen> {
  CloudLibraryService? _service;
  String _path = '';
  List<CloudEntry> _entries = const [];
  String? _nextCursor;
  bool _busy = false;
  String? _message;
  bool _messageIsError = false;
  int _pendingMutations = 0;
  final Set<String> _requestedCursors = <String>{};

  @override
  void initState() {
    super.initState();
    _initialize();
  }

  Future<void> _initialize() async {
    final injected = widget.service;
    if (injected != null) {
      _service = injected;
    } else {
      final adapter = widget.manager.adapterFor(widget.connection);
      OfflineCloudSynchronizer? synchronizer;
      try {
        synchronizer = OfflineCloudSynchronizer(
          adapter: adapter,
          accountId: widget.connection.id,
          store: await createPersistentOfflineMutationStore(
            credentialStore: widget.manager.repository.credentialStore,
          ),
        );
      } on Object {
        synchronizer = null;
      }
      _service = CloudLibraryService(
        adapter: adapter,
        offlineSynchronizer: synchronizer,
      );
    }
    if (!mounted) return;
    setState(() {});
    await _retryPending(silent: true);
    await _load(reset: true);
  }

  Future<void> _load({required bool reset}) async {
    final service = _service;
    if (service == null) return;
    if (_busy) return;
    final requestedCursor = reset ? null : _nextCursor;
    if (!reset && requestedCursor == null) return;
    if (!reset && !_requestedCursors.add(requestedCursor!)) {
      _showError('云服务返回了重复分页游标，已停止继续加载');
      return;
    }
    if (reset) _requestedCursors.clear();
    setState(() {
      _busy = true;
      _message = null;
      _messageIsError = false;
    });
    try {
      final page = await service.list(
        _path,
        cursor: reset ? null : _nextCursor,
      );
      if (!mounted) return;
      if (page.nextCursor != null && page.nextCursor == requestedCursor) {
        _showError('云服务返回了重复分页游标，已停止继续加载');
        return;
      }
      setState(() {
        _entries = reset
            ? page.items
            : <CloudEntry>[..._entries, ...page.items];
        _nextCursor = page.nextCursor;
        _busy = false;
      });
    } on Object catch (error) {
      _showError(service.describeError(error));
    }
  }

  Future<void> _openEntry(CloudEntry entry) async {
    final service = _service;
    if (service == null) return;
    if (entry.isDirectory) {
      setState(() {
        _path = entry.path;
        _entries = const [];
        _nextCursor = null;
      });
      await _load(reset: true);
      return;
    }
    if (!service.canOpen(entry) || _busy) return;
    setState(() {
      _busy = true;
      _message = '正在安全下载并解析 ${_fileName(entry.path)}…';
      _messageIsError = false;
    });
    try {
      final book = await service.open(entry);
      if (!mounted) return;
      setState(() => _busy = false);
      await Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => LocalContentHubScreen(initialBook: book),
        ),
      );
    } on Object catch (error) {
      _showError(service.describeError(error));
    }
  }

  Future<void> _upload() async {
    final service = _service;
    if (service == null) return;
    if (_busy) return;
    LocalPickedFile? file;
    try {
      file = await widget.picker(LocalContentService.bookExtensions);
      if (file == null || !mounted) return;
      final selected = file;
      setState(() {
        _busy = true;
        _message = '正在条件上传 ${selected.name}…';
        _messageIsError = false;
      });
      final result = await service.uploadWithOfflineFallback(_path, selected);
      if (!mounted) return;
      await _refreshPendingCount();
      setState(() {
        _busy = false;
        _message = result.queued ? '网络暂不可用，已加密保存到待同步队列' : '上传完成；未覆盖任何同名文件';
        _messageIsError = false;
      });
      if (!result.queued) await _load(reset: true);
    } on Object catch (error) {
      _showError(service.describeError(error));
    }
  }

  Future<void> _delete(CloudEntry entry) async {
    final service = _service;
    if (service == null || !service.canDelete(entry) || _busy) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('删除 ${_fileName(entry.path)}？'),
        content: const Text('将按当前 ETag 条件删除云端文件；文件若已变化则拒绝删除。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    setState(() => _busy = true);
    try {
      final result = await service.deleteWithOfflineFallback(entry);
      if (!mounted) return;
      await _refreshPendingCount();
      setState(() {
        _busy = false;
        _message = result.queued
            ? '网络暂不可用，删除操作已加入待同步队列'
            : '已删除 ${_fileName(entry.path)}';
        _messageIsError = false;
      });
      if (!result.queued) await _load(reset: true);
    } on Object catch (error) {
      _showError(service.describeError(error));
    }
  }

  Future<void> _retryPending({bool silent = false}) async {
    final service = _service;
    if (service == null || !service.hasOfflineQueue || (!silent && _busy)) {
      return;
    }
    if (!silent && mounted) {
      setState(() {
        _busy = true;
        _message = '正在重试待同步操作…';
        _messageIsError = false;
      });
    }
    try {
      final applied = await service.replayPending();
      await _refreshPendingCount();
      if (!mounted) return;
      if (!silent || applied > 0) {
        setState(() {
          _busy = false;
          _message = applied == 0 ? '没有待同步操作' : '已完成 $applied 个待同步操作';
          _messageIsError = false;
        });
      }
    } on Object catch (error) {
      await _refreshPendingCount();
      if (!silent) _showError(service.describeError(error));
    }
  }

  Future<void> _refreshPendingCount() async {
    final service = _service;
    if (service == null || !service.hasOfflineQueue) return;
    final count = await service.pendingMutationCount();
    if (mounted) setState(() => _pendingMutations = count);
  }

  void _goParent() {
    if (_busy || _path.isEmpty) return;
    final separator = _path.lastIndexOf('/');
    setState(() {
      _path = separator < 0 ? '' : _path.substring(0, separator);
      _entries = const [];
      _nextCursor = null;
    });
    _load(reset: true);
  }

  void _showError(String message) {
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = message;
      _messageIsError = true;
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final service = _service;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.connection.name),
        actions: [
          if (service?.hasOfflineQueue ?? false)
            IconButton(
              tooltip: _pendingMutations == 0
                  ? '检查待同步操作'
                  : '重试 $_pendingMutations 个待同步操作',
              onPressed: _busy ? null : _retryPending,
              icon: Badge(
                isLabelVisible: _pendingMutations > 0,
                label: Text('$_pendingMutations'),
                child: const Icon(Icons.sync_rounded),
              ),
            ),
          IconButton(
            tooltip: '上传电子书',
            onPressed: _busy ? null : _upload,
            icon: const Icon(Icons.cloud_upload_outlined),
          ),
          IconButton(
            tooltip: '刷新',
            onPressed: _busy ? null : () => _load(reset: true),
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: service == null
          ? const Center(child: CircularProgressIndicator())
          : SafeArea(
              child: Column(
                children: [
                  Material(
                    color: theme.colorScheme.surfaceContainerLow,
                    child: ListTile(
                      leading: IconButton(
                        tooltip: '返回上级目录',
                        onPressed: _path.isEmpty || _busy ? null : _goParent,
                        icon: const Icon(Icons.arrow_upward_rounded),
                      ),
                      title: Text(_path.isEmpty ? '/' : '/$_path'),
                      subtitle: Text(
                        '${widget.connection.provider.label} · 受限于 /${widget.connection.root}',
                      ),
                    ),
                  ),
                  if (_message != null)
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(12),
                      color: _messageIsError
                          ? theme.colorScheme.errorContainer
                          : theme.colorScheme.primaryContainer,
                      child: Text(_message!),
                    ),
                  if (_busy) const LinearProgressIndicator(minHeight: 2),
                  Expanded(
                    child: _entries.isEmpty && !_busy
                        ? const _StatusPanel(
                            icon: Icons.folder_open_rounded,
                            title: '此目录为空',
                            detail: '可使用右上角上传受支持的电子书。',
                          )
                        : ListView.builder(
                            itemCount:
                                _entries.length + (_nextCursor == null ? 0 : 1),
                            itemBuilder: (context, index) {
                              if (index == _entries.length) {
                                return Padding(
                                  padding: const EdgeInsets.all(16),
                                  child: OutlinedButton(
                                    onPressed: _busy
                                        ? null
                                        : () => _load(reset: false),
                                    child: const Text('加载更多'),
                                  ),
                                );
                              }
                              final entry = _entries[index];
                              final canOpen = service.canOpen(entry);
                              return ListTile(
                                leading: Icon(
                                  entry.isDirectory
                                      ? Icons.folder_rounded
                                      : canOpen
                                      ? Icons.menu_book_rounded
                                      : Icons.insert_drive_file_outlined,
                                ),
                                title: Text(_fileName(entry.path)),
                                subtitle: entry.isDirectory
                                    ? const Text('目录')
                                    : Text(
                                        canOpen ? '点按后在本机安全解析' : '当前阅读器不支持此格式',
                                      ),
                                onTap: entry.isDirectory || canOpen
                                    ? () => _openEntry(entry)
                                    : null,
                                trailing: service.canDelete(entry)
                                    ? IconButton(
                                        tooltip: '条件删除',
                                        onPressed: _busy
                                            ? null
                                            : () => _delete(entry),
                                        icon: const Icon(
                                          Icons.delete_outline_rounded,
                                        ),
                                      )
                                    : null,
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

class _StatusPanel extends StatelessWidget {
  const _StatusPanel({
    required this.icon,
    required this.title,
    required this.detail,
  });

  final IconData icon;
  final String title;
  final String detail;

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(28),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 44),
          const SizedBox(height: 12),
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          Text(detail, textAlign: TextAlign.center),
        ],
      ),
    ),
  );
}

String _connectionError(Object error) {
  if (error is! Exception) return '连接验证失败；未保存配置或凭据';
  final text = error.toString().toLowerCase();
  if (text.contains('https')) return '必须使用 HTTPS endpoint';
  if (text.contains('cors')) return '此 Web 构建尚未确认目标源站 CORS';
  if (text.contains('credential') || text.contains('unauthorized')) {
    return '凭据无效或权限不足；未保存连接';
  }
  return '无法访问指定根目录；未保存连接，请检查地址、权限和网络';
}

String _fileName(String path) =>
    path.split('/').where((part) => part.isNotEmpty).lastOrNull ?? '/';
