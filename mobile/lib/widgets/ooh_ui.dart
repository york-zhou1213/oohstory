import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:shimmer/shimmer.dart';

import '../theme/app_theme.dart';

/// Shared, product-level primitives for the native OOHStory applications.
///
/// Screens keep their business state, while these widgets keep spacing,
/// loading states, cover treatment and section rhythm consistent on phones
/// and tablets.
class OohPageMetrics {
  const OohPageMetrics._();

  static double horizontalPadding(double width) {
    if (width >= 1180) return 36;
    if (width >= 720) return 28;
    return 16;
  }

  static double sectionGap(double width) => width >= 720 ? 30 : 24;

  static int gridColumns(double width) {
    if (width >= 1180) return 6;
    if (width >= 900) return 5;
    if (width >= 620) return 4;
    if (width >= 360) return 3;
    return 2;
  }
}

class OohSectionHeader extends StatelessWidget {
  final String title;
  final String? subtitle;
  final String? actionLabel;
  final VoidCallback? onAction;

  const OohSectionHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final horizontal = OohPageMetrics.horizontalPadding(
          MediaQuery.sizeOf(context).width,
        );
        return Padding(
          padding: EdgeInsets.fromLTRB(horizontal, 28, horizontal, 14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.baseline,
                  textBaseline: TextBaseline.alphabetic,
                  children: [
                    Text(
                      title,
                      style: theme.textTheme.titleLarge?.copyWith(
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    if (subtitle != null) ...[
                      const SizedBox(width: 10),
                      Flexible(
                        child: Text(
                          subtitle!,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodySmall,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (onAction != null)
                TextButton(
                  onPressed: onAction,
                  child: Text(actionLabel ?? '查看全部'),
                ),
            ],
          ),
        );
      },
    );
  }
}

class OohBookCover extends StatelessWidget {
  final String imageUrl;
  final String title;
  final double width;
  final double? height;
  final BorderRadius? borderRadius;
  final ImageProvider? imageProvider;

  const OohBookCover({
    super.key,
    required this.imageUrl,
    required this.title,
    required this.width,
    this.height,
    this.borderRadius,
    this.imageProvider,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final radius = borderRadius ?? BorderRadius.circular(12);
    final resolvedHeight = height ?? width * 1.5;
    return Semantics(
      image: true,
      label: '《$title》封面',
      child: DecoratedBox(
        decoration: BoxDecoration(
          borderRadius: radius,
          border: Border.all(
            color: theme.colorScheme.outlineVariant.withValues(alpha: .72),
          ),
          boxShadow: [
            BoxShadow(
              color: theme.colorScheme.shadow.withValues(
                alpha: theme.brightness == Brightness.dark ? .22 : .09,
              ),
              blurRadius: 16,
              offset: const Offset(0, 7),
            ),
          ],
        ),
        child: ClipRRect(
          borderRadius: radius,
          child: imageProvider == null
              ? OohNetworkImage(
                  imageUrl: imageUrl,
                  width: width,
                  height: resolvedHeight,
                  fit: BoxFit.cover,
                  placeholder: _CoverPlaceholder(
                    width: width,
                    height: resolvedHeight,
                    title: title,
                    shimmer: true,
                  ),
                  error: _CoverPlaceholder(
                    width: width,
                    height: resolvedHeight,
                    title: title,
                  ),
                )
              : Image(
                  image: imageProvider!,
                  width: width,
                  height: resolvedHeight,
                  fit: BoxFit.cover,
                  filterQuality: FilterQuality.medium,
                  errorBuilder: (_, __, ___) => _CoverPlaceholder(
                    width: width,
                    height: resolvedHeight,
                    title: title,
                  ),
                ),
        ),
      ),
    );
  }
}

class OohNetworkImage extends StatelessWidget {
  final String imageUrl;
  final double? width;
  final double? height;
  final BoxFit fit;
  final Alignment alignment;
  final Widget? placeholder;
  final Widget? error;

  const OohNetworkImage({
    super.key,
    required this.imageUrl,
    this.width,
    this.height,
    this.fit = BoxFit.cover,
    this.alignment = Alignment.center,
    this.placeholder,
    this.error,
  });

  @override
  Widget build(BuildContext context) {
    final fallback = error ?? const SizedBox.shrink();
    if (imageUrl.trim().isEmpty) return fallback;
    final pixelRatio = MediaQuery.devicePixelRatioOf(context);
    final cacheWidth = width == null
        ? null
        : (width! * pixelRatio).round().clamp(1, 1400);
    final cacheHeight = height == null
        ? null
        : (height! * pixelRatio).round().clamp(1, 2100);
    return CachedNetworkImage(
      imageUrl: imageUrl,
      httpHeaders: const {
        'Accept':
            'image/avif,image/webp,image/apng,image/jpeg,image/*,*/*;q=0.8',
        'User-Agent': 'OOHStoryApp/1.27.0 (Flutter; official)',
      },
      width: width,
      height: height,
      fit: fit,
      alignment: alignment,
      memCacheWidth: cacheWidth,
      memCacheHeight: cacheHeight,
      maxWidthDiskCache: cacheWidth,
      maxHeightDiskCache: cacheHeight,
      useOldImageOnUrlChange: true,
      fadeInDuration: const Duration(milliseconds: 160),
      filterQuality: FilterQuality.medium,
      placeholder: (_, __) => placeholder ?? fallback,
      errorWidget: (_, __, ___) => fallback,
    );
  }
}

class _CoverPlaceholder extends StatelessWidget {
  final double width;
  final double height;
  final String title;
  final bool shimmer;

  const _CoverPlaceholder({
    required this.width,
    required this.height,
    required this.title,
    this.shimmer = false,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final fallback = Container(
      width: width,
      height: height,
      padding: EdgeInsets.fromLTRB(
        width >= 90 ? 13 : 8,
        width >= 90 ? 18 : 10,
        width >= 90 ? 13 : 8,
        width >= 90 ? 15 : 9,
      ),
      decoration: BoxDecoration(
        color: AppTheme.brandNavy,
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            AppTheme.brandNavy,
            Color.lerp(AppTheme.brandNavy, AppTheme.brandBlue, .48)!,
          ],
        ),
      ),
      child: Stack(
        children: [
          Positioned(
            top: 0,
            left: 0,
            child: Opacity(
              opacity: .82,
              child: Image.asset(
                'assets/oohstory-brand-icon.png',
                width: width.clamp(22, 38).toDouble(),
                height: width.clamp(22, 38).toDouble(),
                fit: BoxFit.cover,
              ),
            ),
          ),
          Align(
            alignment: Alignment.bottomLeft,
            child: Text(
              title,
              maxLines: width < 72 ? 2 : 3,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.titleSmall?.copyWith(
                color: Colors.white,
                fontSize: width < 72 ? 10 : null,
                fontWeight: FontWeight.w800,
                height: 1.25,
              ),
            ),
          ),
        ],
      ),
    );
    if (!shimmer) return fallback;
    return Shimmer.fromColors(
      baseColor: theme.colorScheme.surfaceContainerHighest,
      highlightColor: theme.colorScheme.surface,
      child: ColoredBox(
        color: theme.colorScheme.surfaceContainerHighest,
        child: SizedBox(width: width, height: height),
      ),
    );
  }
}

class OohLoadingState extends StatelessWidget {
  final int itemCount;

  const OohLoadingState({super.key, this.itemCount = 6});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = OohPageMetrics.gridColumns(constraints.maxWidth);
        return Semantics(
          label: '内容加载中',
          child: Shimmer.fromColors(
            baseColor: theme.colorScheme.surfaceContainerHighest,
            highlightColor: theme.colorScheme.surface,
            child: GridView.builder(
              padding: EdgeInsets.all(
                OohPageMetrics.horizontalPadding(constraints.maxWidth),
              ),
              gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: columns,
                childAspectRatio: .62,
                mainAxisSpacing: 20,
                crossAxisSpacing: 14,
              ),
              itemCount: itemCount,
              itemBuilder: (_, __) => Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        color: theme.colorScheme.surface,
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: const SizedBox.expand(),
                    ),
                  ),
                  const SizedBox(height: 10),
                  Container(height: 12, color: theme.colorScheme.surface),
                  const SizedBox(height: 7),
                  FractionallySizedBox(
                    widthFactor: .64,
                    child: Container(
                      height: 10,
                      color: theme.colorScheme.surface,
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class OohMessageState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String message;
  final String? actionLabel;
  final VoidCallback? onAction;

  const OohMessageState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 340),
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 60,
                height: 60,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: theme.colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(18),
                ),
                child: Icon(
                  icon,
                  size: 28,
                  color: theme.colorScheme.onPrimaryContainer,
                ),
              ),
              const SizedBox(height: 18),
              Text(title, style: theme.textTheme.titleLarge),
              const SizedBox(height: 8),
              Text(
                message,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              if (onAction != null) ...[
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: onAction,
                  child: Text(actionLabel ?? '重试'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class OohSurface extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry? padding;
  final VoidCallback? onTap;

  const OohSurface({super.key, required this.child, this.padding, this.onTap});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final body = Padding(
      padding: padding ?? const EdgeInsets.all(16),
      child: child,
    );
    return Material(
      color: theme.colorScheme.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTheme.cardRadius),
        side: BorderSide(color: theme.colorScheme.outlineVariant),
      ),
      clipBehavior: Clip.antiAlias,
      child: onTap == null ? body : InkWell(onTap: onTap, child: body),
    );
  }
}
