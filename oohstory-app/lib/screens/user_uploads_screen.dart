import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../services/account_service.dart';

class UserUploadsScreen extends StatefulWidget {
  const UserUploadsScreen({super.key});

  @override
  State<UserUploadsScreen> createState() => _UserUploadsScreenState();
}

class _UserUploadsScreenState extends State<UserUploadsScreen> {
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  bool _uploading = false;
  String _message = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final items = await AccountService.instance.uploads();
      if (mounted) {
        setState(() {
          _items = items;
          _loading = false;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _message = error.toString();
          _loading = false;
        });
      }
    }
  }

  Future<void> _pickAndUpload() async {
    final selected = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['txt', 'epub'],
      allowMultiple: false,
    );
    final path = selected?.files.single.path;
    if (path == null) return;
    setState(() {
      _uploading = true;
      _message = '正在隔离、验毒并检查文件结构…';
    });
    try {
      final result = await AccountService.instance.uploadSource(path);
      if (mounted) {
        setState(() => _message = result['message'] as String? ?? '已进入归纳队列');
      }
      await _load();
    } catch (error) {
      if (mounted) setState(() => _message = error.toString());
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  String _status(String value) =>
      const {
        'quarantined': '隔离扫描中',
        'clean_queued': '已验毒 · 等待归纳',
        'processing': '正在归纳',
        'completed': '已完成',
        'rejected': '已拒绝',
      }[value] ??
      value;

  String _size(dynamic value) {
    final bytes = value is int ? value : 0;
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
  }

  @override
  Widget build(BuildContext context) {
    final user = AccountService.instance.user;
    return Scaffold(
      appBar: AppBar(title: const Text('拆书上传记录')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _uploading || user?.emailVerified != true
            ? null
            : _pickAndUpload,
        icon: _uploading
            ? const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.shield_outlined),
        label: Text(user?.emailVerified == true ? '安全上传' : '验证账户后上传'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 10, 16, 110),
              children: [
                Container(
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(
                      colors: [Color(0xFF10284F), Color(0xFF176A96)],
                    ),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: const Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(Icons.verified_user_outlined, color: Colors.white),
                      SizedBox(height: 12),
                      Text(
                        '安全隔离归纳',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 19,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      SizedBox(height: 6),
                      Text(
                        'TXT/EPUB 会先进入个人沙箱，完成结构检查、SHA-256 去重和 ClamAV 病毒扫描后才进入归纳流程。',
                        style: TextStyle(
                          color: Colors.white70,
                          height: 1.55,
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                ),
                if (_message.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    child: Text(
                      _message,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.primary,
                        fontSize: 12,
                      ),
                    ),
                  ),
                if (_items.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: 70),
                    child: Center(
                      child: Text(
                        '还没有上传记录',
                        style: TextStyle(color: Colors.grey),
                      ),
                    ),
                  )
                else
                  ..._items.map(
                    (item) => Card(
                      margin: const EdgeInsets.only(top: 11),
                      child: ListTile(
                        leading: const CircleAvatar(
                          child: Icon(Icons.description_outlined),
                        ),
                        title: Text(
                          item['original_filename'] as String? ?? '未命名文件',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        subtitle: Text(
                          '${_size(item['bytes'])} · ${item['rejection_reason'] ?? 'SHA-256 已记录'}',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        trailing: Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 5,
                          ),
                          decoration: BoxDecoration(
                            color: Theme.of(
                              context,
                            ).colorScheme.primaryContainer,
                            borderRadius: BorderRadius.circular(99),
                          ),
                          child: Text(
                            _status(item['status'] as String? ?? ''),
                            style: const TextStyle(
                              fontSize: 10,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
    );
  }
}
