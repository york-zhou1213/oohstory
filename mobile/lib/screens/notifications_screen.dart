import 'package:flutter/material.dart';

import '../services/account_service.dart';
import '../theme/app_theme.dart';

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  final _account = AccountService.instance;
  List<Map<String, dynamic>> _items = const [];
  int _unread = 0;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await _account.notifications();
      _items = (data['items'] as List? ?? const [])
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      _unread = (data['unread_count'] as num?)?.toInt() ?? 0;
    } catch (error) {
      _error = error.toString();
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _markAll() async {
    await _account.markNotificationRead();
    await _load();
  }

  Future<void> _markOne(String id) async {
    await _account.markNotificationRead(id);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('消息中心')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
          children: [
            _hero(),
            const SizedBox(height: 14),
            if (_loading && _items.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 80),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_error != null && _items.isEmpty)
              _empty(Icons.cloud_off_outlined, _error!)
            else if (_items.isEmpty)
              _empty(Icons.mark_email_read_outlined, '收件箱很安静')
            else
              ..._items.map(_notificationCard),
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
      boxShadow: [
        BoxShadow(
          color: AppTheme.seedPurple.withValues(alpha: .24),
          blurRadius: 20,
          offset: const Offset(0, 8),
        ),
      ],
    ),
    child: Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'MESSAGE CENTER',
                style: TextStyle(
                  color: Colors.white.withValues(alpha: .6),
                  fontSize: 10,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 1.8,
                ),
              ),
              const SizedBox(height: 6),
              const Text(
                '审核与入库动态',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 20,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 5),
              Text(
                '结果、缺失资料和入库进度集中呈现',
                style: TextStyle(
                  color: Colors.white.withValues(alpha: .68),
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
        Column(
          children: [
            const Text(
              '未读',
              style: TextStyle(color: Colors.white70, fontSize: 11),
            ),
            Text(
              '$_unread',
              style: const TextStyle(
                color: Colors.white,
                fontSize: 30,
                fontWeight: FontWeight.w900,
              ),
            ),
            TextButton(
              onPressed: _unread == 0 ? null : _markAll,
              style: TextButton.styleFrom(foregroundColor: Colors.white),
              child: const Text('全部已读'),
            ),
          ],
        ),
      ],
    ),
  );

  Widget _notificationCard(Map<String, dynamic> item) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    final text = '${item['title'] ?? ''} ${item['message'] ?? ''}';
    final danger = RegExp('驳回|未通过|失败|缺少').hasMatch(text);
    final success = item['kind'] == 'submission_ingestion' && !danger;
    final color = danger
        ? const Color(0xFFE05265)
        : success
        ? const Color(0xFF16A085)
        : AppTheme.seedPurple;
    final read = (item['read_at'] as String? ?? '').isNotEmpty;
    final resourceStatus = item['resource_status'] as String? ?? '';
    final date = DateTime.tryParse(
      item['created_at'] as String? ?? '',
    )?.toLocal();
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: dark ? const Color(0xFF1E1E30) : Colors.white,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(
          color: read ? Colors.transparent : color.withValues(alpha: .34),
        ),
        boxShadow: [
          BoxShadow(
            color: color.withValues(alpha: read ? .03 : .08),
            blurRadius: 14,
          ),
        ],
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 40,
            height: 40,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: color.withValues(alpha: .1),
              borderRadius: BorderRadius.circular(13),
            ),
            child: Icon(
              danger
                  ? Icons.priority_high
                  : success
                  ? Icons.check
                  : Icons.diamond_outlined,
              color: color,
            ),
          ),
          const SizedBox(width: 13),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      danger
                          ? '需要处理'
                          : success
                          ? '入库进度'
                          : '审核动态',
                      style: TextStyle(
                        fontSize: 10,
                        color: color,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const Spacer(),
                    Text(
                      read ? '已读' : '未读',
                      style: TextStyle(fontSize: 10, color: color),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  item['title'] as String? ?? '消息',
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  item['message'] as String? ?? '',
                  style: TextStyle(
                    fontSize: 12,
                    height: 1.5,
                    color: theme.colorScheme.onSurface.withValues(alpha: .62),
                  ),
                ),
                if (resourceStatus.isNotEmpty) ...[
                  const SizedBox(height: 9),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 9,
                      vertical: 5,
                    ),
                    decoration: BoxDecoration(
                      color: color.withValues(alpha: .1),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text(
                      '当前状态：${_resourceStatusLabel(resourceStatus)}',
                      style: TextStyle(
                        color: color,
                        fontSize: 10,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                ],
                const SizedBox(height: 10),
                Row(
                  children: [
                    Text(
                      date == null
                          ? ''
                          : '${date.year}/${date.month}/${date.day} ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}',
                      style: TextStyle(
                        fontSize: 10,
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: .38,
                        ),
                      ),
                    ),
                    const Spacer(),
                    if (!read)
                      TextButton(
                        onPressed: () => _markOne(item['id'] as String),
                        child: const Text('标记已读'),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _resourceStatusLabel(String status) => switch (status) {
    'quarantined' => '等待后台检查',
    'scanning' => '后台安全检查中',
    'ai_pending' => '等待审核',
    'reviewing' => '审核中',
    'approved' => '已通过，等待入库',
    'completed' => '已入库',
    'rejected' => '已驳回',
    _ => status,
  };

  Widget _empty(IconData icon, String text) => Padding(
    padding: const EdgeInsets.only(top: 80),
    child: Column(
      children: [
        Icon(
          icon,
          size: 50,
          color: Theme.of(context).colorScheme.onSurface.withValues(alpha: .16),
        ),
        const SizedBox(height: 12),
        Text(
          text,
          style: TextStyle(
            color: Theme.of(
              context,
            ).colorScheme.onSurface.withValues(alpha: .48),
          ),
        ),
      ],
    ),
  );
}
