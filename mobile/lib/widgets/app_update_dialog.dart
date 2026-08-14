import 'package:flutter/material.dart';

import '../services/app_update_service.dart';

enum AppUpdateAction { later, update }

Future<AppUpdateAction?> showAppUpdateDialog(
  BuildContext context,
  AppUpdateInfo info,
) {
  return showDialog<AppUpdateAction>(
    context: context,
    barrierDismissible: false,
    builder: (dialogContext) => AlertDialog(
      title: const Row(
        children: [
          Icon(Icons.system_update_alt_rounded),
          SizedBox(width: 10),
          Text('发现新版本'),
        ],
      ),
      content: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '当前版本 v${AppUpdateService.currentVersionName}，可更新至 v${info.versionName}。',
              style: Theme.of(dialogContext).textTheme.bodyMedium,
            ),
            if (info.sizeBytes > 0) ...[
              const SizedBox(height: 6),
              Text(
                '安装包大小：${_formatBytes(info.sizeBytes)}',
                style: Theme.of(dialogContext).textTheme.bodySmall,
              ),
            ],
            const SizedBox(height: 14),
            Text('更新内容', style: Theme.of(dialogContext).textTheme.titleSmall),
            const SizedBox(height: 8),
            ...info.releaseNotes.map(
              (note) => Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('• '),
                    Expanded(child: Text(note)),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(dialogContext, AppUpdateAction.later),
          child: const Text('稍后'),
        ),
        FilledButton.icon(
          onPressed: () => Navigator.pop(dialogContext, AppUpdateAction.update),
          icon: const Icon(Icons.download_rounded, size: 18),
          label: const Text('立即更新'),
        ),
      ],
    ),
  );
}

String _formatBytes(int value) {
  if (value <= 0) return '未知';
  final mb = value / (1024 * 1024);
  if (mb >= 1) return '${mb.toStringAsFixed(1)} MB';
  final kb = value / 1024;
  return '${kb.toStringAsFixed(0)} KB';
}
