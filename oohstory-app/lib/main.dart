import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:audio_service/audio_service.dart';
import 'theme/app_theme.dart';
import 'screens/home_screen.dart';
import 'screens/library_screen.dart';
import 'screens/reading_bookshelf_screen.dart';
import 'screens/profile_screen.dart';
import 'services/tts_audio_handler.dart';
import 'services/tts_service.dart';
import 'services/api_service.dart';
import 'services/account_service.dart';
import 'services/app_update_service.dart';
import 'services/ooh_origin_transport.dart'
    if (dart.library.js_interop) 'services/ooh_origin_transport_web.dart';
import 'widgets/app_update_dialog.dart';
import 'widgets/ooh_primary_navigation_bar.dart';

late TtsAudioHandler ttsHandler;
late TtsService ttsService;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  installProductionOriginOverrides();
  await AccountService.instance.initialize();
  ttsHandler = await AudioService.init(
    builder: () => TtsAudioHandler(),
    config: AudioServiceConfig(
      androidNotificationChannelId: 'com.oohstory.app.tts',
      androidNotificationChannelName: '听书',
      androidStopForegroundOnPause: false,
    ),
  );
  ttsService = TtsService(ApiService(), ttsHandler)..mode = 'smart';
  runApp(const OohStoryApp());
}

class OohStoryApp extends StatelessWidget {
  final bool checkForUpdates;

  const OohStoryApp({super.key, this.checkForUpdates = true});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'OOHStory',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: ThemeMode.system,
      locale: const Locale('zh', 'CN'),
      supportedLocales: const [Locale('zh', 'CN'), Locale('en')],
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      scrollBehavior: const OohScrollBehavior(),
      home: SplashScreen(checkForUpdates: checkForUpdates),
    );
  }
}

class SplashScreen extends StatefulWidget {
  final bool checkForUpdates;

  const SplashScreen({super.key, this.checkForUpdates = true});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  late Animation<double> _fadeIn;
  Timer? _navigationTimer;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 460),
    );
    _fadeIn = CurvedAnimation(parent: _controller, curve: Curves.easeIn);
    _controller.forward();

    _navigationTimer = Timer(const Duration(milliseconds: 720), () {
      if (mounted) {
        Navigator.of(context).pushReplacement(
          PageRouteBuilder(
            pageBuilder: (_, __, ___) =>
                MainShell(checkForUpdates: widget.checkForUpdates),
            transitionsBuilder: (_, animation, __, child) {
              return FadeTransition(opacity: animation, child: child);
            },
            transitionDuration: const Duration(milliseconds: 240),
          ),
        );
      }
    });
  }

  @override
  void dispose() {
    _navigationTimer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reduceMotion = MediaQuery.disableAnimationsOf(context);
    final logo = Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 104,
          height: 104,
          padding: const EdgeInsets.all(3),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: .08),
            borderRadius: BorderRadius.circular(28),
            border: Border.all(color: Colors.white.withValues(alpha: .15)),
            boxShadow: [
              BoxShadow(
                color: AppTheme.sky.withValues(alpha: .18),
                blurRadius: 36,
                offset: const Offset(0, 16),
              ),
            ],
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(24),
            child: Image.asset(
              'assets/oohstory-brand-icon.png',
              fit: BoxFit.cover,
            ),
          ),
        ),
        const SizedBox(height: 28),
        const Text(
          'OOHStory',
          style: TextStyle(
            fontSize: 34,
            fontWeight: FontWeight.w800,
            color: Color(0xFFF2F5FB),
            letterSpacing: -1.2,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          '阅读，听见故事',
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w500,
            color: Colors.white.withValues(alpha: .62),
            letterSpacing: .6,
          ),
        ),
      ],
    );
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFF050A14), Color(0xFF071A44), Color(0xFF103B91)],
            stops: [0, .62, 1],
          ),
        ),
        child: Center(
          child: reduceMotion
              ? logo
              : FadeTransition(opacity: _fadeIn, child: logo),
        ),
      ),
    );
  }
}

class MainShell extends StatefulWidget {
  final bool checkForUpdates;

  const MainShell({super.key, this.checkForUpdates = true});

  @override
  State<MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<MainShell> with WidgetsBindingObserver {
  int _currentIndex = 0;
  bool _desktopNavigationExpanded = true;
  late final AppUpdateService _appUpdateService;
  bool _checkingUpdate = false;

  late final List<Widget> _screens;

  static const _labels = ['发现', '书库', '书架', '我的'];
  static const _icons = [
    Icons.explore_outlined,
    Icons.local_library_outlined,
    Icons.shelves,
    Icons.person_outline_rounded,
  ];
  static const _selectedIcons = [
    Icons.explore_rounded,
    Icons.local_library_rounded,
    Icons.shelves,
    Icons.person_rounded,
  ];
  static const _descriptions = [
    '精选作品与最新上架',
    '浏览、筛选与管理内容',
    '继续上次阅读',
    '账户、拆书与阅读设置',
  ];

  bool get _supportsInAppUpdates =>
      defaultTargetPlatform == TargetPlatform.android;

  bool get _usesCupertinoTabBar =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.iOS;

  @override
  void initState() {
    super.initState();
    _screens = [
      const HomeScreen(),
      const LibraryScreen(),
      ReadingBookshelfScreen(onBrowse: () => _selectDestination(1)),
      const ProfileScreen(),
    ];
    _appUpdateService = AppUpdateService(ApiService());
    WidgetsBinding.instance.addObserver(this);
    if (widget.checkForUpdates && _supportsInAppUpdates) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _checkForUpdates());
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (widget.checkForUpdates &&
        _supportsInAppUpdates &&
        state == AppLifecycleState.resumed) {
      unawaited(_checkForUpdates());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  Future<void> _checkForUpdates() async {
    if (_checkingUpdate || !mounted) return;
    _checkingUpdate = true;
    try {
      final info = await _appUpdateService.checkForUpdate();
      if (!mounted || info == null) return;
      await _appUpdateService.markPrompted(info);
      if (!mounted) return;
      final action = await showAppUpdateDialog(context, info);
      if (action != AppUpdateAction.update || !mounted) return;
      try {
        await _appUpdateService.openDownload(info);
      } catch (_) {
        if (!mounted) return;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('无法打开更新下载，请稍后再试')));
      }
    } catch (_) {
      // Update checks must never block normal reading.
    } finally {
      _checkingUpdate = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final tablet =
            constraints.maxWidth >= AppTheme.wideNavigationBreakpoint;
        final desktop = constraints.maxWidth >= AppTheme.expandedRailBreakpoint;
        final content = ColoredBox(
          color: theme.scaffoldBackgroundColor,
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(
                maxWidth: AppTheme.contentMaxWidth,
              ),
              child: IndexedStack(index: _currentIndex, children: _screens),
            ),
          ),
        );

        if (desktop) {
          return Scaffold(
            body: SafeArea(
              child: Row(
                children: [
                  _DesktopNavigationPanel(
                    expanded: _desktopNavigationExpanded,
                    selectedIndex: _currentIndex,
                    labels: _labels,
                    icons: _icons,
                    selectedIcons: _selectedIcons,
                    onSelected: _selectDestination,
                    onToggleExpanded: () => setState(
                      () => _desktopNavigationExpanded =
                          !_desktopNavigationExpanded,
                    ),
                  ),
                  VerticalDivider(
                    width: 1,
                    color: theme.colorScheme.outlineVariant,
                  ),
                  Expanded(
                    child: Column(
                      children: [
                        _DesktopWorkspaceHeader(
                          title: _labels[_currentIndex],
                          description: _descriptions[_currentIndex],
                        ),
                        Divider(
                          height: 1,
                          color: theme.colorScheme.outlineVariant,
                        ),
                        Expanded(child: content),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          );
        }

        if (tablet) {
          return Scaffold(
            body: SafeArea(
              child: Row(
                children: [
                  NavigationRail(
                    extended: false,
                    selectedIndex: _currentIndex,
                    onDestinationSelected: _selectDestination,
                    leading: Padding(
                      padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
                      child: const _BrandLockup(compact: true),
                    ),
                    groupAlignment: -.72,
                    destinations: List.generate(
                      _labels.length,
                      (index) => NavigationRailDestination(
                        icon: Icon(_icons[index]),
                        selectedIcon: Icon(_selectedIcons[index]),
                        label: Text(_labels[index]),
                      ),
                    ),
                  ),
                  VerticalDivider(
                    width: 1,
                    color: theme.colorScheme.outlineVariant,
                  ),
                  Expanded(child: content),
                ],
              ),
            ),
          );
        }

        final PreferredSizeWidget phoneHeader = _usesCupertinoTabBar
            ? CupertinoNavigationBar(
                middle: _currentIndex == 0
                    ? const _BrandLockup()
                    : Text(
                        _labels[_currentIndex],
                        style: theme.textTheme.titleMedium,
                      ),
                backgroundColor: theme.colorScheme.surface.withValues(
                  alpha: .92,
                ),
                border: Border(
                  bottom: BorderSide(color: theme.colorScheme.outlineVariant),
                ),
              )
            : AppBar(
                toolbarHeight: 64,
                titleSpacing: 16,
                title: _currentIndex == 0
                    ? const _BrandLockup()
                    : Text(_labels[_currentIndex]),
              );

        return Scaffold(
          appBar: phoneHeader,
          body: content,
          bottomNavigationBar: _usesCupertinoTabBar
              ? CupertinoTabBar(
                  height: 64,
                  currentIndex: _currentIndex,
                  onTap: _selectDestination,
                  activeColor: theme.colorScheme.onPrimaryContainer,
                  inactiveColor: theme.colorScheme.onSurfaceVariant,
                  backgroundColor: theme.colorScheme.surface.withValues(
                    alpha: .98,
                  ),
                  border: Border(
                    top: BorderSide(color: theme.colorScheme.outlineVariant),
                  ),
                  items: List.generate(
                    _labels.length,
                    (index) => BottomNavigationBarItem(
                      icon: _CupertinoBottomDestination(
                        icon: _icons[index],
                        label: _labels[index],
                      ),
                      activeIcon: _CupertinoBottomDestination(
                        icon: _selectedIcons[index],
                        label: _labels[index],
                        selected: true,
                      ),
                      label: '',
                    ),
                  ),
                )
              : OohPrimaryNavigationBar(
                  selectedIndex: _currentIndex,
                  onDestinationSelected: _selectDestination,
                  destinations: List.generate(
                    _labels.length,
                    (index) => OohPrimaryDestination(
                      label: _labels[index],
                      icon: _icons[index],
                      selectedIcon: _selectedIcons[index],
                    ),
                  ),
                ),
        );
      },
    );
  }

  void _selectDestination(int index) {
    if (index == _currentIndex) return;
    unawaited(HapticFeedback.selectionClick());
    setState(() => _currentIndex = index);
  }
}

class _DesktopNavigationPanel extends StatelessWidget {
  final bool expanded;
  final int selectedIndex;
  final List<String> labels;
  final List<IconData> icons;
  final List<IconData> selectedIcons;
  final ValueChanged<int> onSelected;
  final VoidCallback onToggleExpanded;

  const _DesktopNavigationPanel({
    required this.expanded,
    required this.selectedIndex,
    required this.labels,
    required this.icons,
    required this.selectedIcons,
    required this.onSelected,
    required this.onToggleExpanded,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final reduceMotion = MediaQuery.disableAnimationsOf(context);
    return AnimatedContainer(
      duration: reduceMotion
          ? Duration.zero
          : const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      width: expanded ? 244 : 82,
      color: colors.surface,
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            height: 52,
            child: Align(
              alignment: expanded ? Alignment.centerLeft : Alignment.center,
              child: _BrandLockup(compact: !expanded),
            ),
          ),
          const SizedBox(height: 22),
          for (var index = 0; index < labels.length; index++) ...[
            _DesktopNavigationDestination(
              expanded: expanded,
              selected: selectedIndex == index,
              label: labels[index],
              icon: selectedIndex == index
                  ? selectedIcons[index]
                  : icons[index],
              onTap: () => onSelected(index),
            ),
            const SizedBox(height: 6),
          ],
          const Spacer(),
          if (expanded)
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 0, 10, 12),
              child: Text(
                '跨设备阅读，进度保持一致',
                maxLines: 2,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: colors.onSurfaceVariant,
                ),
              ),
            ),
          IconButton(
            onPressed: onToggleExpanded,
            tooltip: expanded ? '收起侧栏' : '展开侧栏',
            alignment: expanded ? Alignment.centerRight : Alignment.center,
            icon: Icon(
              expanded
                  ? Icons.keyboard_double_arrow_left_rounded
                  : Icons.keyboard_double_arrow_right_rounded,
            ),
          ),
        ],
      ),
    );
  }
}

class _DesktopNavigationDestination extends StatelessWidget {
  final bool expanded;
  final bool selected;
  final String label;
  final IconData icon;
  final VoidCallback onTap;

  const _DesktopNavigationDestination({
    required this.expanded,
    required this.selected,
    required this.label,
    required this.icon,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Semantics(
      button: true,
      selected: selected,
      label: label,
      onTap: onTap,
      excludeSemantics: true,
      child: Material(
        color: selected ? colors.primaryContainer : Colors.transparent,
        borderRadius: BorderRadius.circular(AppTheme.controlRadius),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(AppTheme.controlRadius),
          child: SizedBox(
            height: 50,
            child: Row(
              mainAxisAlignment: expanded
                  ? MainAxisAlignment.start
                  : MainAxisAlignment.center,
              children: [
                if (expanded) const SizedBox(width: 14),
                Icon(
                  icon,
                  size: 23,
                  color: selected
                      ? colors.onPrimaryContainer
                      : colors.onSurfaceVariant,
                ),
                if (expanded) ...[
                  const SizedBox(width: 14),
                  Expanded(
                    child: Text(
                      label,
                      maxLines: 1,
                      overflow: TextOverflow.fade,
                      softWrap: false,
                      style: Theme.of(context).textTheme.labelLarge?.copyWith(
                        color: selected
                            ? colors.onPrimaryContainer
                            : colors.onSurface,
                        fontWeight: selected
                            ? FontWeight.w800
                            : FontWeight.w600,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _DesktopWorkspaceHeader extends StatelessWidget {
  final String title;
  final String description;

  const _DesktopWorkspaceHeader({
    required this.title,
    required this.description,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Container(
      height: 76,
      color: colors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 28),
      child: Row(
        children: [
          Expanded(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: theme.textTheme.titleLarge),
                const SizedBox(height: 2),
                Text(
                  description,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: colors.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
          _PlatformBadge(label: _platformLabel()),
        ],
      ),
    );
  }
}

class _PlatformBadge extends StatelessWidget {
  final String label;

  const _PlatformBadge({required this.label});

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
        color: colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(AppTheme.controlRadius),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.devices_rounded, size: 17, color: colors.primary),
          const SizedBox(width: 7),
          Text(
            label,
            style: Theme.of(context).textTheme.labelMedium?.copyWith(
              color: colors.onSurfaceVariant,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

String _platformLabel() {
  if (kIsWeb) return 'Web';
  return switch (defaultTargetPlatform) {
    TargetPlatform.android => 'Android',
    TargetPlatform.iOS => 'iOS',
    TargetPlatform.macOS => 'macOS',
    TargetPlatform.windows => 'Windows',
    TargetPlatform.linux => 'Linux',
    TargetPlatform.fuchsia => 'Fuchsia',
  };
}

class _CupertinoBottomDestination extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;

  const _CupertinoBottomDestination({
    required this.icon,
    required this.label,
    this.selected = false,
  });

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Semantics(
      label: label,
      selected: selected,
      excludeSemantics: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        width: 72,
        height: 48,
        decoration: BoxDecoration(
          color: selected ? colors.primaryContainer : Colors.transparent,
          borderRadius: BorderRadius.circular(16),
        ),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 21),
            const SizedBox(height: 2),
            Text(
              label,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: selected
                    ? colors.onPrimaryContainer
                    : colors.onSurfaceVariant,
                fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                height: 1.05,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class OohScrollBehavior extends MaterialScrollBehavior {
  const OohScrollBehavior();

  @override
  ScrollPhysics getScrollPhysics(BuildContext context) {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.iOS) {
      return const BouncingScrollPhysics(
        parent: AlwaysScrollableScrollPhysics(),
      );
    }
    return const ClampingScrollPhysics(parent: AlwaysScrollableScrollPhysics());
  }
}

class _BrandLockup extends StatelessWidget {
  final bool compact;

  const _BrandLockup({this.compact = false});

  @override
  Widget build(BuildContext context) {
    final logo = ClipRRect(
      borderRadius: BorderRadius.circular(9),
      child: Image.asset(
        'assets/oohstory-brand-icon.png',
        width: 34,
        height: 34,
        fit: BoxFit.cover,
      ),
    );
    if (compact) return logo;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        logo,
        const SizedBox(width: 10),
        Text(
          'OOHStory',
          style: Theme.of(context).textTheme.titleLarge?.copyWith(
            fontWeight: FontWeight.w800,
            letterSpacing: -.5,
          ),
        ),
      ],
    );
  }
}
