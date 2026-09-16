import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../../models/reader_preferences.dart';
import '../../services/local_storage_service.dart';

Future<OfflineAnnotationAttachment?> pickAnnotationAttachment({
  required LocalStorageService storage,
  required OfflineAnnotation annotation,
}) async {
  if (kIsWeb) return null;
  final result = await FilePicker.platform.pickFiles(
    dialogTitle: '为批注添加附件',
    allowMultiple: false,
    withData: false,
  );
  if (result == null || result.files.isEmpty) return null;
  final picked = result.files.single;
  final sourcePath = picked.path;
  if (sourcePath == null || sourcePath.isEmpty) {
    throw const FormatException('无法读取所选附件');
  }
  return storage.addAnnotationAttachment(
    annotationId: annotation.id,
    sourcePath: sourcePath,
    fileName: picked.name,
  );
}

Future<bool> showAnnotationAttachmentsDialog({
  required BuildContext context,
  required LocalStorageService storage,
  required OfflineAnnotation annotation,
}) async =>
    await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => _AnnotationAttachmentsDialog(
        storage: storage,
        annotation: annotation,
      ),
    ) ??
    false;

class _AnnotationAttachmentsDialog extends StatefulWidget {
  const _AnnotationAttachmentsDialog({
    required this.storage,
    required this.annotation,
  });

  final LocalStorageService storage;
  final OfflineAnnotation annotation;

  @override
  State<_AnnotationAttachmentsDialog> createState() =>
      _AnnotationAttachmentsDialogState();
}

class _AnnotationAttachmentsDialogState
    extends State<_AnnotationAttachmentsDialog> {
  late List<OfflineAnnotationAttachment> _attachments;
  String? _removingId;
  var _changed = false;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() {
    _attachments = widget.storage.getAnnotationAttachments(
      annotationId: widget.annotation.id,
    );
  }

  Future<void> _remove(OfflineAnnotationAttachment attachment) async {
    if (_removingId != null) return;
    setState(() => _removingId = attachment.id);
    try {
      await widget.storage.removeAnnotationAttachment(attachment.id);
      if (!mounted) return;
      setState(() {
        _changed = true;
        _removingId = null;
        _reload();
      });
    } on Object {
      if (!mounted) return;
      setState(() => _removingId = null);
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('无法移除附件，请稍后重试')));
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: false,
    child: AlertDialog(
      title: const Text('批注附件'),
      content: SizedBox(
        width: 440,
        child: _attachments.isEmpty
            ? const Text('还没有附件')
            : ListView.builder(
                shrinkWrap: true,
                itemCount: _attachments.length,
                itemBuilder: (context, index) {
                  final attachment = _attachments[index];
                  final removing = _removingId == attachment.id;
                  return ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.attach_file_rounded),
                    title: Text(
                      attachment.fileName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: Text(_formatBytes(attachment.byteLength)),
                    trailing: IconButton(
                      tooltip: '移除附件',
                      onPressed: _removingId == null
                          ? () => _remove(attachment)
                          : null,
                      icon: removing
                          ? const SizedBox.square(
                              dimension: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.delete_outline_rounded),
                    ),
                  );
                },
              ),
      ),
      actions: [
        TextButton(
          onPressed: _removingId == null
              ? () => Navigator.of(context).pop(_changed)
              : null,
          child: const Text('完成'),
        ),
      ],
    ),
  );

  static String _formatBytes(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
}
