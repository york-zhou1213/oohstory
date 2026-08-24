import 'package:flutter/material.dart';

import '../utils/user_content_guard.dart';

Future<void> showUserContentNoticeDialog(
  BuildContext context, {
  required String issue,
  bool identity = false,
}) {
  final copy = UserContentGuard.notice(issue, identity: identity);
  final color = copy.promotion
      ? const Color(0xFFD65A78)
      : const Color(0xFF397DD5);
  return showDialog<void>(
    context: context,
    builder: (dialogContext) => Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(26)),
      clipBehavior: Clip.antiAlias,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 430),
        child: Stack(
          children: [
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: Container(
                height: 4,
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    colors: [
                      Color(0xFF41B6E9),
                      Color(0xFF776EF4),
                      Color(0xFFEF78A8),
                    ],
                  ),
                ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(25, 29, 25, 23),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 48,
                    height: 48,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(16),
                      gradient: LinearGradient(
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                        colors: copy.promotion
                            ? const [Color(0xFFFFF4E6), Color(0xFFFFEAF0)]
                            : const [Color(0xFFE8F7FF), Color(0xFFEEE8FF)],
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: color.withValues(alpha: .17),
                          blurRadius: 24,
                          offset: const Offset(0, 10),
                        ),
                      ],
                    ),
                    child: Text(
                      '✦',
                      style: TextStyle(
                        color: color,
                        fontSize: 23,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  const SizedBox(height: 18),
                  Text(
                    identity ? '昵称提示' : '字里行间 · 友好提醒',
                    style: Theme.of(dialogContext).textTheme.labelSmall
                        ?.copyWith(
                          color: const Color(0xFF397DD5),
                          fontWeight: FontWeight.w800,
                          letterSpacing: 1.2,
                        ),
                  ),
                  const SizedBox(height: 7),
                  Text(
                    copy.title,
                    style: Theme.of(dialogContext).textTheme.headlineSmall
                        ?.copyWith(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    copy.message,
                    style: Theme.of(
                      dialogContext,
                    ).textTheme.bodyMedium?.copyWith(height: 1.75),
                  ),
                  const SizedBox(height: 22),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      onPressed: () => Navigator.of(dialogContext).pop(),
                      style: FilledButton.styleFrom(
                        minimumSize: const Size.fromHeight(46),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(14),
                        ),
                      ),
                      child: Text(copy.actionLabel),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    ),
  );
}
