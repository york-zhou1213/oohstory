import 'package:flutter/material.dart';

Future<bool> showRecommendationDonationDialog(BuildContext context) async {
  return await _showRecommendationDialog(
        context,
        title: '为这本好书助力？',
        message: '捐赠 1 小时阅读经验时长，将好书推荐给更多人。',
        primaryLabel: '助力推荐',
        secondaryLabel: '再想想',
        confirmedValue: true,
      ) ??
      false;
}

Future<void> showRecommendationNoticeDialog(
  BuildContext context, {
  required String title,
  required String message,
  String primaryLabel = '知道了',
}) async {
  await _showRecommendationDialog(
    context,
    title: title,
    message: message,
    primaryLabel: primaryLabel,
    confirmedValue: false,
  );
}

Future<bool?> _showRecommendationDialog(
  BuildContext context, {
  required String title,
  required String message,
  required String primaryLabel,
  String? secondaryLabel,
  required bool confirmedValue,
}) {
  return showDialog<bool>(
    context: context,
    builder: (dialogContext) => Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(26)),
      clipBehavior: Clip.antiAlias,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 450),
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
                      Color(0xFFF4BD55),
                      Color(0xFF8D70EF),
                      Color(0xFF48B7E7),
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
                    width: 52,
                    height: 52,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(18),
                      gradient: const LinearGradient(
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                        colors: [Color(0xFFFFF7DC), Color(0xFFEEE9FF)],
                      ),
                      boxShadow: const [
                        BoxShadow(
                          color: Color(0x33B8851F),
                          blurRadius: 26,
                          offset: Offset(0, 11),
                        ),
                      ],
                    ),
                    child: const Text(
                      '✦',
                      style: TextStyle(
                        color: Color(0xFF9B6D18),
                        fontSize: 24,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  const SizedBox(height: 18),
                  Text(
                    'READING GIFT',
                    style: Theme.of(dialogContext).textTheme.labelSmall
                        ?.copyWith(
                          color: const Color(0xFF7A69D8),
                          fontWeight: FontWeight.w800,
                          letterSpacing: 1.2,
                        ),
                  ),
                  const SizedBox(height: 7),
                  Text(
                    title,
                    style: Theme.of(dialogContext).textTheme.headlineSmall
                        ?.copyWith(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    message,
                    style: Theme.of(
                      dialogContext,
                    ).textTheme.bodyMedium?.copyWith(height: 1.75),
                  ),
                  const SizedBox(height: 22),
                  if (secondaryLabel != null)
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton(
                            onPressed: () =>
                                Navigator.of(dialogContext).pop(false),
                            style: OutlinedButton.styleFrom(
                              minimumSize: const Size.fromHeight(46),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(14),
                              ),
                            ),
                            child: Text(secondaryLabel),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: FilledButton(
                            onPressed: () =>
                                Navigator.of(dialogContext).pop(confirmedValue),
                            style: FilledButton.styleFrom(
                              minimumSize: const Size.fromHeight(46),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(14),
                              ),
                            ),
                            child: Text(primaryLabel),
                          ),
                        ),
                      ],
                    )
                  else
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton(
                        onPressed: () =>
                            Navigator.of(dialogContext).pop(confirmedValue),
                        style: FilledButton.styleFrom(
                          minimumSize: const Size.fromHeight(46),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(14),
                          ),
                        ),
                        child: Text(primaryLabel),
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
