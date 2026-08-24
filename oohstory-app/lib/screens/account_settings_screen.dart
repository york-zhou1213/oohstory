import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../services/account_service.dart';
import '../theme/app_theme.dart';
import '../utils/user_content_guard.dart';
import '../widgets/account_success_toast.dart';
import '../widgets/reading_identity.dart';
import '../widgets/user_content_notice_dialog.dart';

class AccountSettingsScreen extends StatefulWidget {
  const AccountSettingsScreen({super.key});

  @override
  State<AccountSettingsScreen> createState() => _AccountSettingsScreenState();
}

class _AccountSettingsScreenState extends State<AccountSettingsScreen> {
  final _account = AccountService.instance;
  final _profileKey = GlobalKey<FormState>();
  final _passwordKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _bio = TextEditingController();
  final _location = TextEditingController();
  final _currentPassword = TextEditingController();
  final _newPassword = TextEditingController();
  final _confirmPassword = TextEditingController();
  String _gender = '';
  DateTime? _birthday;
  String? _avatarUrl;
  Map<String, dynamic> _reading = const {};
  bool _loading = true;
  bool _saving = false;
  String? _error;

  static const _levels = [
    ('Ⅰ', '只如初见', '0'),
    ('Ⅱ', '此去经年', '30'),
    ('Ⅲ', '素心相赠', '100'),
    ('Ⅳ', '犹故人归', '250'),
    ('Ⅴ', '踏歌寻醉', '500'),
    ('Ⅵ', '冷暖自知', '1,000'),
    ('Ⅶ', '青青子衿', '1,800'),
    ('Ⅷ', '似水流年', '3,000'),
    ('Ⅸ', '不诉离殇', '5,000'),
    ('Ⅹ', '近月侵衣', '8,000'),
    ('Ⅺ', '对酒当歌', '12,000'),
    ('Ⅻ', '长风万里', '18,000'),
    ('ⅩⅢ', '知与谁同', '26,000'),
    ('ⅩⅣ', '扶摇九霄', '36,000'),
    ('ⅩⅤ', '凌云绝顶', '48,000'),
    ('ⅩⅥ', '摘星揽月', '62,000'),
    ('ⅩⅦ', '天人合一', '80,000'),
    ('ⅩⅧ', '水月镜花', '100,000'),
  ];

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final controller in [
      _name,
      _bio,
      _location,
      _currentPassword,
      _newPassword,
      _confirmPassword,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await _account.profile();
      final profile = Map<String, dynamic>.from(data['profile'] as Map);
      _name.text = profile['display_name'] as String? ?? '';
      _bio.text = profile['bio'] as String? ?? '';
      _location.text = profile['location'] as String? ?? '';
      _gender = profile['gender'] as String? ?? '';
      _birthday = DateTime.tryParse(profile['birthday'] as String? ?? '');
      _avatarUrl = profile['avatar_url'] as String?;
      _reading = Map<String, dynamic>.from(data['reading'] as Map);
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

  Future<void> _chooseAvatar() async {
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['jpg', 'jpeg', 'png', 'webp'],
    );
    final path = picked?.files.single.path;
    if (path == null) return;
    setState(() => _saving = true);
    try {
      final result = await _account.uploadAvatar(path);
      setState(() => _avatarUrl = result['avatar_url'] as String?);
      _message('头像已更新');
    } catch (error) {
      _message(error.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _removeAvatar() async {
    setState(() => _saving = true);
    try {
      await _account.removeAvatar();
      setState(() => _avatarUrl = null);
      _message('头像已删除');
    } catch (error) {
      _message(error.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _saveProfile() async {
    if (!(_profileKey.currentState?.validate() ?? false)) return;
    final issue = UserContentGuard.issue(_name.text, identity: true);
    if (issue != null) {
      await showUserContentNoticeDialog(context, issue: issue, identity: true);
      return;
    }
    setState(() => _saving = true);
    try {
      final birthday = _birthday == null
          ? null
          : '${_birthday!.year.toString().padLeft(4, '0')}-'
                '${_birthday!.month.toString().padLeft(2, '0')}-'
                '${_birthday!.day.toString().padLeft(2, '0')}';
      await _account.updateProfile(
        displayName: _name.text,
        bio: _bio.text,
        gender: _gender,
        birthday: birthday,
        location: _location.text,
      );
      if (!mounted) return;
      showAccountSuccessToast(context, message: '个人资料保存成功');
    } catch (error) {
      if (!mounted) return;
      final message = error.toString();
      if (UserContentGuard.isModerationMessage(message, identity: true)) {
        await showUserContentNoticeDialog(
          context,
          issue: message,
          identity: true,
        );
      } else {
        _message(message);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _changePassword() async {
    if (!(_passwordKey.currentState?.validate() ?? false)) return;
    if (_newPassword.text != _confirmPassword.text) {
      _message('两次输入的新密码不一致');
      return;
    }
    setState(() => _saving = true);
    try {
      await _account.changePassword(
        currentPassword: _currentPassword.text,
        newPassword: _newPassword.text,
      );
      _currentPassword.clear();
      _newPassword.clear();
      _confirmPassword.clear();
      if (!mounted) return;
      showAccountSuccessToast(context, message: '密码修改成功');
    } catch (error) {
      _message(error.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('资料与安全')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(child: Text(_error!))
          : ListView(
              padding: const EdgeInsets.only(bottom: 36),
              children: [
                ReadingIdentityCard(reading: _reading),
                _section('个人头像', '支持 JPEG、PNG、WebP，上传后会安全处理。', _avatarEditor()),
                _section('详细个人信息', '资料仅用于你的个人中心。', _profileForm()),
                _section('账户安全', '修改成功后，其他设备会退出登录。', _passwordForm()),
                _section(
                  '阅读等级图鉴',
                  '当前可用阅读时长达到门槛后自动升级；每次助力推荐会捐赠 1 小时阅读经验时长。',
                  _levelMap(),
                ),
              ],
            ),
    );
  }

  Widget _section(String title, String subtitle, Widget child) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 14, 16, 0),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: dark ? const Color(0xFF1E1E30) : Colors.white,
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
              color: theme.colorScheme.onSurface.withValues(alpha: .5),
            ),
          ),
          const SizedBox(height: 16),
          child,
        ],
      ),
    );
  }

  Widget _avatarEditor() => Row(
    children: [
      CircleAvatar(
        radius: 36,
        foregroundImage: _avatarUrl == null
            ? null
            : NetworkImage(
                _account.avatarUrl(_avatarUrl!),
                headers: _account.authHeaders,
              ),
        child: _avatarUrl == null ? const Icon(Icons.person, size: 34) : null,
      ),
      const SizedBox(width: 16),
      Expanded(
        child: Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            FilledButton.icon(
              onPressed: _saving ? null : _chooseAvatar,
              icon: const Icon(Icons.upload, size: 18),
              label: Text(_avatarUrl == null ? '上传头像' : '替换头像'),
            ),
            if (_avatarUrl != null)
              TextButton(
                onPressed: _saving ? null : _removeAvatar,
                child: const Text('删除'),
              ),
          ],
        ),
      ),
    ],
  );

  Widget _profileForm() => Form(
    key: _profileKey,
    child: Column(
      children: [
        TextFormField(
          controller: _name,
          maxLength: 40,
          decoration: const InputDecoration(labelText: '显示昵称'),
          validator: (value) {
            if ((value ?? '').trim().isEmpty) return '请填写昵称';
            return null;
          },
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: _bio,
          maxLength: 500,
          maxLines: 4,
          decoration: const InputDecoration(labelText: '个人简介'),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          value: _gender,
          decoration: const InputDecoration(labelText: '性别（可选）'),
          items: const [
            DropdownMenuItem(value: '', child: Text('暂不填写')),
            DropdownMenuItem(value: 'female', child: Text('女')),
            DropdownMenuItem(value: 'male', child: Text('男')),
            DropdownMenuItem(value: 'other', child: Text('其他')),
          ],
          onChanged: (value) => _gender = value ?? '',
        ),
        const SizedBox(height: 12),
        InkWell(
          onTap: () async {
            final date = await showDatePicker(
              context: context,
              firstDate: DateTime(1900),
              lastDate: DateTime.now(),
              initialDate: _birthday ?? DateTime(2000),
            );
            if (date != null) setState(() => _birthday = date);
          },
          child: InputDecorator(
            decoration: const InputDecoration(labelText: '生日（可选）'),
            child: Text(
              _birthday == null
                  ? '暂不填写'
                  : '${_birthday!.year}/${_birthday!.month}/${_birthday!.day}',
            ),
          ),
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: _location,
          maxLength: 80,
          decoration: const InputDecoration(labelText: '所在地（可选）'),
        ),
        const SizedBox(height: 4),
        SizedBox(
          width: double.infinity,
          child: FilledButton(
            onPressed: _saving ? null : _saveProfile,
            child: const Text('保存个人资料'),
          ),
        ),
      ],
    ),
  );

  Widget _passwordForm() => Form(
    key: _passwordKey,
    child: Column(
      children: [
        TextFormField(
          controller: _currentPassword,
          obscureText: true,
          decoration: const InputDecoration(labelText: '当前密码'),
          validator: (value) => (value ?? '').isEmpty ? '请输入当前密码' : null,
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: _newPassword,
          obscureText: true,
          decoration: const InputDecoration(
            labelText: '新密码',
            helperText: '至少 12 位，包含三类字符',
          ),
          validator: (value) => (value ?? '').length < 12 ? '新密码至少 12 位' : null,
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: _confirmPassword,
          obscureText: true,
          decoration: const InputDecoration(labelText: '确认新密码'),
        ),
        const SizedBox(height: 14),
        SizedBox(
          width: double.infinity,
          child: FilledButton(
            onPressed: _saving ? null : _changePassword,
            child: const Text('修改密码'),
          ),
        ),
      ],
    ),
  );

  Widget _levelMap() {
    final current = (_reading['level'] as num?)?.toInt() ?? 1;
    return Column(
      children: [
        for (var index = 0; index < _levels.length; index++)
          Container(
            margin: const EdgeInsets.only(bottom: 8),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: index + 1 == current
                  ? AppTheme.seedPurple.withValues(alpha: .12)
                  : Colors.transparent,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: index + 1 == current
                    ? AppTheme.seedPurple.withValues(alpha: .35)
                    : Theme.of(context).dividerColor.withValues(alpha: .12),
              ),
            ),
            child: Row(
              children: [
                ReadingRankBadge(
                  level: index + 1,
                  roman: _levels[index].$1,
                  size: 32,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    _levels[index].$2,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                Text(
                  '${_levels[index].$3} 小时',
                  style: const TextStyle(fontSize: 11),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
