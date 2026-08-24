import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

String formatReadingDuration(num? seconds, {bool remaining = false}) {
  final raw = (seconds?.toDouble() ?? 0).clamp(0, double.infinity);
  final totalMinutes = remaining ? (raw / 60).ceil() : (raw / 60).floor();
  final hours = totalMinutes ~/ 60;
  final minutes = totalMinutes % 60;
  if (hours == 0) return '$minutes 分钟';
  if (minutes == 0) return '$hours 小时';
  return '$hours 小时 $minutes 分钟';
}

class ReadingRankBadge extends StatelessWidget {
  final int level;
  final String roman;
  final double size;

  const ReadingRankBadge({
    super.key,
    required this.level,
    required this.roman,
    this.size = 32,
  });

  List<Color> get _colors {
    if (level >= 18) {
      return const [Color(0xFFFFD76A), Color(0xFFD56BFF), Color(0xFF6746D9)];
    }
    if (level >= 14) {
      return const [Color(0xFFFFE8A3), Color(0xFFFFB44C), Color(0xFF8A5A18)];
    }
    if (level >= 8) {
      return const [Color(0xFFBDF7FF), Color(0xFF64A8FF), Color(0xFF7857E8)];
    }
    return const [Color(0xFFF5FAFF), Color(0xFF9CB9D5), Color(0xFF4C6581)];
  }

  @override
  Widget build(BuildContext context) {
    final colors = _colors;
    return Semantics(
      label: '阅读等级 $roman',
      child: Container(
        width: size,
        height: size,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: colors,
          ),
          borderRadius: BorderRadius.circular(size * .34),
          border: Border.all(
            color: Colors.white.withValues(alpha: .7),
            width: 1,
          ),
          boxShadow: [
            BoxShadow(
              color: colors[1].withValues(alpha: .42),
              blurRadius: size * .35,
              offset: Offset(0, size * .1),
            ),
          ],
        ),
        child: Text(
          roman,
          maxLines: 1,
          style: TextStyle(
            color: level >= 14
                ? const Color(0xFF30200D)
                : const Color(0xFF172536),
            fontSize: roman.length > 3 ? size * .29 : size * .38,
            fontWeight: FontWeight.w900,
            letterSpacing: -.8,
            shadows: [
              Shadow(color: Colors.white.withValues(alpha: .6), blurRadius: 3),
            ],
          ),
        ),
      ),
    );
  }
}

class ReadingIdentityCard extends StatelessWidget {
  final Map<String, dynamic> reading;
  final EdgeInsetsGeometry margin;

  const ReadingIdentityCard({
    super.key,
    required this.reading,
    this.margin = const EdgeInsets.fromLTRB(16, 14, 16, 0),
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    final level = (reading['level'] as num?)?.toInt() ?? 1;
    final roman = reading['roman'] as String? ?? 'Ⅰ';
    final name = reading['name'] as String? ?? '只如初见';
    final progress = ((reading['progress'] as num?)?.toDouble() ?? 0).clamp(
      0.0,
      1.0,
    );
    final isMax = reading['is_max'] as bool? ?? false;
    final activeDuration = formatReadingDuration(
      reading['active_seconds'] as num?,
    );
    final nextDuration = formatReadingDuration(
      reading['seconds_to_next'] as num?,
      remaining: true,
    );
    return Container(
      margin: margin,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: dark ? const Color(0xFF171829) : Colors.white,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppTheme.seedPurple.withValues(alpha: .16)),
        boxShadow: [
          BoxShadow(
            color: AppTheme.seedPurple.withValues(alpha: dark ? .12 : .08),
            blurRadius: 22,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              ReadingRankBadge(level: level, roman: roman, size: 46),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'READING IDENTITY',
                      style: TextStyle(
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: .45,
                        ),
                        fontSize: 10,
                        letterSpacing: 1.8,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '$roman · $name',
                      style: const TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ],
                ),
              ),
              Text(
                '可用 $activeDuration',
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: LinearProgressIndicator(
              minHeight: 7,
              value: progress,
              color: AppTheme.seedPurple,
              backgroundColor: AppTheme.seedPurple.withValues(alpha: .1),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            isMax ? '已达最高阅读等级' : '距离下一级还需 $nextDuration 专注阅读',
            style: TextStyle(
              fontSize: 12,
              color: theme.colorScheme.onSurface.withValues(alpha: .56),
            ),
          ),
        ],
      ),
    );
  }
}
