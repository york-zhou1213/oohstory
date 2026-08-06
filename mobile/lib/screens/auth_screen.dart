import 'package:flutter/material.dart';

import '../services/account_service.dart';
import '../services/local_storage_service.dart';
import '../utils/user_content_guard.dart';
import '../widgets/user_content_notice_dialog.dart';

class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _name = TextEditingController();
  final _invite = TextEditingController();
  bool _register = false;
  bool _busy = false;
  bool _obscure = true;
  String _error = '';

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _name.dispose();
    _invite.dispose();
    super.dispose();
  }

  Future<void> _finish(
    Future<void> Function() operation, {
    bool contentIdentity = false,
  }) async {
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      await operation();
      final storage = LocalStorageService();
      await storage.init();
      await AccountService.instance.mergeLocalState(storage);
      if (mounted) Navigator.of(context).pop(true);
    } catch (error) {
      if (!mounted) return;
      final message = error.toString();
      if (contentIdentity &&
          UserContentGuard.isModerationMessage(message, identity: true)) {
        await showUserContentNoticeDialog(
          context,
          issue: message,
          identity: true,
        );
      } else {
        setState(() => _error = message);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (_register) {
      final issue = UserContentGuard.issue(_name.text, identity: true);
      if (issue != null) {
        await showUserContentNoticeDialog(
          context,
          issue: issue,
          identity: true,
        );
        return;
      }
    }
    await _finish(
      () => _register
          ? AccountService.instance.register(
              email: _email.text,
              password: _password.text,
              displayName: _name.text,
              invitationCode: _invite.text,
            )
          : AccountService.instance.login(
              email: _email.text,
              password: _password.text,
            ),
      contentIdentity: _register,
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    return Scaffold(
      body: Stack(
        children: [
          Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: dark
                      ? const [
                          Color(0xFF090E1B),
                          Color(0xFF132744),
                          Color(0xFF143D58),
                        ]
                      : const [
                          Color(0xFFEAF6FF),
                          Color(0xFFF8FBFF),
                          Color(0xFFEDEBFF),
                        ],
                ),
              ),
            ),
          ),
          Positioned(
            top: -80,
            right: -55,
            child: _orb(const Color(0xFF4BB7EA), 230),
          ),
          Positioned(
            bottom: -90,
            left: -80,
            child: _orb(const Color(0xFF7B6CF6), 260),
          ),
          SafeArea(
            child: Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(20),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 440),
                  child: Container(
                    padding: const EdgeInsets.fromLTRB(24, 22, 24, 26),
                    decoration: BoxDecoration(
                      color: theme.colorScheme.surface.withValues(alpha: .94),
                      borderRadius: BorderRadius.circular(28),
                      border: Border.all(
                        color: Colors.white.withValues(alpha: .55),
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: const Color(0xFF071B38).withValues(alpha: .18),
                          blurRadius: 48,
                          offset: const Offset(0, 22),
                        ),
                      ],
                    ),
                    child: Form(
                      key: _formKey,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Row(
                            children: [
                              Container(
                                width: 48,
                                height: 48,
                                decoration: BoxDecoration(
                                  gradient: const LinearGradient(
                                    colors: [
                                      Color(0xFF3CA9E6),
                                      Color(0xFF246CC2),
                                    ],
                                  ),
                                  borderRadius: BorderRadius.circular(15),
                                ),
                                child: const Icon(
                                  Icons.auto_stories_rounded,
                                  color: Colors.white,
                                ),
                              ),
                              const SizedBox(width: 13),
                              const Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    'OOH STORY',
                                    style: TextStyle(
                                      fontWeight: FontWeight.w900,
                                      letterSpacing: 1.2,
                                    ),
                                  ),
                                  Text(
                                    '把故事带到每一台设备',
                                    style: TextStyle(
                                      fontSize: 11,
                                      color: Colors.grey,
                                    ),
                                  ),
                                ],
                              ),
                              const Spacer(),
                              IconButton(
                                onPressed: () => Navigator.pop(context),
                                icon: const Icon(Icons.close_rounded),
                              ),
                            ],
                          ),
                          const SizedBox(height: 24),
                          Text(
                            _register ? '建立你的阅读宇宙' : '欢迎回来',
                            style: theme.textTheme.headlineSmall?.copyWith(
                              fontWeight: FontWeight.w900,
                              letterSpacing: -.5,
                            ),
                          ),
                          const SizedBox(height: 6),
                          Text(
                            _register
                                ? '现在开放注册，邀请码为选填项，一个账户同步三端。'
                                : '继续上次没有读完的故事。',
                            style: TextStyle(
                              color: theme.colorScheme.onSurface.withValues(
                                alpha: .56,
                              ),
                              fontSize: 13,
                            ),
                          ),
                          const SizedBox(height: 22),
                          Container(
                            padding: const EdgeInsets.all(4),
                            decoration: BoxDecoration(
                              color: theme.colorScheme.surfaceContainerHighest
                                  .withValues(alpha: .55),
                              borderRadius: BorderRadius.circular(14),
                            ),
                            child: Row(
                              children: [
                                Expanded(
                                  child: _tab(
                                    '登录',
                                    !_register,
                                    () => setState(() {
                                      _register = false;
                                      _error = '';
                                    }),
                                  ),
                                ),
                                Expanded(
                                  child: _tab(
                                    '注册',
                                    _register,
                                    () => setState(() {
                                      _register = true;
                                      _error = '';
                                    }),
                                  ),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(height: 19),
                          AnimatedSize(
                            duration: const Duration(milliseconds: 220),
                            child: _register
                                ? Padding(
                                    padding: const EdgeInsets.only(bottom: 13),
                                    child: _field(
                                      _name,
                                      '昵称',
                                      Icons.person_outline_rounded,
                                      validator: (value) =>
                                          value != null && value.length > 40
                                          ? '昵称不能超过 40 个字符'
                                          : null,
                                    ),
                                  )
                                : const SizedBox.shrink(),
                          ),
                          AnimatedSize(
                            duration: const Duration(milliseconds: 220),
                            child: _register
                                ? Padding(
                                    padding: const EdgeInsets.only(bottom: 13),
                                    child: _field(
                                      _invite,
                                      '邀请码（选填）',
                                      Icons.vpn_key_outlined,
                                      validator: (value) =>
                                          value != null &&
                                              value.trim().isNotEmpty &&
                                              value.trim().length < 20
                                          ? '请输入有效邀请码'
                                          : null,
                                    ),
                                  )
                                : const SizedBox.shrink(),
                          ),
                          _field(
                            _email,
                            '电子邮箱',
                            Icons.alternate_email_rounded,
                            keyboardType: TextInputType.emailAddress,
                            validator: (value) {
                              if (value == null || !value.contains('@')) {
                                return '请输入有效邮箱';
                              }
                              return null;
                            },
                          ),
                          const SizedBox(height: 13),
                          _field(
                            _password,
                            _register ? '密码（至少 12 位）' : '密码',
                            Icons.lock_outline_rounded,
                            obscure: _obscure,
                            suffix: IconButton(
                              onPressed: () =>
                                  setState(() => _obscure = !_obscure),
                              icon: Icon(
                                _obscure
                                    ? Icons.visibility_outlined
                                    : Icons.visibility_off_outlined,
                              ),
                            ),
                            validator: (value) {
                              if (value == null || value.isEmpty) {
                                return '请输入密码';
                              }
                              if (_register && value.length < 12) {
                                return '密码至少需要 12 位';
                              }
                              return null;
                            },
                          ),
                          AnimatedSize(
                            duration: const Duration(milliseconds: 180),
                            child: _error.isEmpty
                                ? const SizedBox(height: 18)
                                : Padding(
                                    padding: const EdgeInsets.symmetric(
                                      vertical: 12,
                                    ),
                                    child: Text(
                                      _error,
                                      style: const TextStyle(
                                        color: Color(0xFFD84C61),
                                        fontSize: 12,
                                      ),
                                    ),
                                  ),
                          ),
                          FilledButton(
                            onPressed: _busy ? null : _submit,
                            style: FilledButton.styleFrom(
                              minimumSize: const Size.fromHeight(50),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(14),
                              ),
                            ),
                            child: _busy
                                ? const SizedBox(
                                    width: 20,
                                    height: 20,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                      color: Colors.white,
                                    ),
                                  )
                                : Text(
                                    _register ? '创建账户' : '安全登录',
                                    style: const TextStyle(
                                      fontWeight: FontWeight.w800,
                                    ),
                                  ),
                          ),
                          const Padding(
                            padding: EdgeInsets.symmetric(vertical: 18),
                            child: Row(
                              children: [
                                Expanded(child: Divider()),
                                Padding(
                                  padding: EdgeInsets.symmetric(horizontal: 12),
                                  child: Text(
                                    '或',
                                    style: TextStyle(
                                      fontSize: 11,
                                      color: Colors.grey,
                                    ),
                                  ),
                                ),
                                Expanded(child: Divider()),
                              ],
                            ),
                          ),
                          OutlinedButton.icon(
                            onPressed:
                                !_busy &&
                                    !_register &&
                                    AccountService.instance.googleAvailable
                                ? () => _finish(
                                    () => AccountService.instance
                                        .signInWithGoogle(),
                                  )
                                : null,
                            icon: const Text(
                              'G',
                              style: TextStyle(
                                fontWeight: FontWeight.w900,
                                color: Color(0xFF4285F4),
                              ),
                            ),
                            label: Text(
                              _register
                                  ? '注册后在个人中心绑定 Google'
                                  : AccountService.instance.googleAvailable
                                  ? '使用已绑定的 Google 账户登录'
                                  : 'Google 登录待配置',
                            ),
                            style: OutlinedButton.styleFrom(
                              minimumSize: const Size.fromHeight(48),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(14),
                              ),
                            ),
                          ),
                          const SizedBox(height: 17),
                          Text(
                            '密码使用 Argon2id 加密；登录令牌只保存在系统安全密钥库。',
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              fontSize: 10.5,
                              color: theme.colorScheme.onSurface.withValues(
                                alpha: .42,
                              ),
                            ),
                          ),
                        ],
                      ),
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

  Widget _orb(Color color, double size) => Container(
    width: size,
    height: size,
    decoration: BoxDecoration(
      shape: BoxShape.circle,
      color: color.withValues(alpha: .18),
    ),
  );

  Widget _tab(String text, bool selected, VoidCallback onTap) => InkWell(
    onTap: onTap,
    borderRadius: BorderRadius.circular(11),
    child: AnimatedContainer(
      duration: const Duration(milliseconds: 180),
      padding: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(
        color: selected
            ? Theme.of(context).colorScheme.surface
            : Colors.transparent,
        borderRadius: BorderRadius.circular(11),
        boxShadow: selected
            ? [
                BoxShadow(
                  color: Colors.black.withValues(alpha: .06),
                  blurRadius: 10,
                ),
              ]
            : null,
      ),
      child: Text(
        text,
        textAlign: TextAlign.center,
        style: TextStyle(
          fontWeight: FontWeight.w800,
          color: selected ? Theme.of(context).colorScheme.primary : Colors.grey,
        ),
      ),
    ),
  );

  Widget _field(
    TextEditingController controller,
    String label,
    IconData icon, {
    bool obscure = false,
    Widget? suffix,
    TextInputType? keyboardType,
    String? Function(String?)? validator,
  }) => TextFormField(
    controller: controller,
    obscureText: obscure,
    keyboardType: keyboardType,
    validator: validator,
    autofillHints: label.contains('邮箱') ? const [AutofillHints.email] : null,
    decoration: InputDecoration(
      labelText: label,
      prefixIcon: Icon(icon, size: 20),
      suffixIcon: suffix,
      filled: true,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide.none,
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(
          color: Theme.of(context).dividerColor.withValues(alpha: .5),
        ),
      ),
    ),
  );
}
