import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/theme/app_theme.dart';
import 'package:oohstory/widgets/ooh_ui.dart';
import 'dart:ui' as ui;

void main() {
  const pathProvider = MethodChannel('plugins.flutter.io/path_provider');

  setUpAll(() async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          pathProvider,
          (_) async => Directory.systemTemp.path,
        );
    final font = File('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc');
    if (font.existsSync()) {
      await ui.loadFontFromList(
        await font.readAsBytes(),
        fontFamily: 'Noto Sans CJK SC',
      );
    }
    final icons = File(
      'build/unit_test_assets/fonts/MaterialIcons-Regular.otf',
    );
    if (icons.existsSync()) {
      await ui.loadFontFromList(
        await icons.readAsBytes(),
        fontFamily: 'MaterialIcons',
      );
    }
  });

  tearDownAll(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(pathProvider, null);
  });

  testWidgets('phone cover-first design baseline', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(const _DesignPreview());
    await expectLater(
      find.byType(_DesignPreview),
      matchesGoldenFile('goldens/ooh_design_phone.png'),
    );
  });

  testWidgets('tablet cover-first design baseline', (tester) async {
    tester.view.physicalSize = const Size(1024, 768);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(const _DesignPreview());
    await expectLater(
      find.byType(_DesignPreview),
      matchesGoldenFile('goldens/ooh_design_tablet.png'),
    );
  });
}

class _DesignPreview extends StatelessWidget {
  const _DesignPreview();

  @override
  Widget build(BuildContext context) {
    final base = AppTheme.light();
    final textTheme = base.textTheme.apply(fontFamily: 'Noto Sans CJK SC');
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: base.copyWith(
        textTheme: textTheme,
        appBarTheme: base.appBarTheme.copyWith(
          titleTextStyle: textTheme.titleLarge,
        ),
        navigationRailTheme: base.navigationRailTheme.copyWith(
          selectedLabelTextStyle: base
              .navigationRailTheme
              .selectedLabelTextStyle
              ?.copyWith(fontFamily: 'Noto Sans CJK SC'),
          unselectedLabelTextStyle: base
              .navigationRailTheme
              .unselectedLabelTextStyle
              ?.copyWith(fontFamily: 'Noto Sans CJK SC'),
        ),
      ),
      home: const _PreviewBody(),
    );
  }
}

class _PreviewBody extends StatelessWidget {
  const _PreviewBody();

  static const titles = ['深空回响', '星环纪元', '长夜观测者', '群星之门'];

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final tablet = constraints.maxWidth >= 720;
        final horizontal = OohPageMetrics.horizontalPadding(
          constraints.maxWidth,
        );
        final catalog = CustomScrollView(
          slivers: [
            SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.fromLTRB(horizontal, 10, horizontal, 12),
                child: const TextField(
                  readOnly: true,
                  decoration: InputDecoration(
                    hintText: '搜索书名、作者或类型',
                    prefixIcon: Icon(Icons.search_rounded),
                  ),
                ),
              ),
            ),
            SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.symmetric(horizontal: horizontal),
                child: OohSurface(
                  child: SizedBox(
                    height: tablet ? 184 : 164,
                    child: Row(
                      children: [
                        OohBookCover(
                          imageUrl: '',
                          title: '深空回响',
                          width: tablet ? 122 : 96,
                          height: tablet ? 183 : 144,
                        ),
                        SizedBox(width: tablet ? 22 : 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '编辑精选',
                                style: theme.textTheme.labelMedium?.copyWith(
                                  color: theme.colorScheme.primary,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                              const SizedBox(height: 8),
                              Text(
                                '深空回响',
                                style: theme.textTheme.headlineSmall?.copyWith(
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                              const SizedBox(height: 6),
                              Text(
                                '林默 · 硬核科幻',
                                style: theme.textTheme.bodySmall,
                              ),
                              const Spacer(),
                              Row(
                                children: [
                                  Text(
                                    '连载 · 126章',
                                    style: theme.textTheme.labelSmall,
                                  ),
                                  const Spacer(),
                                  Icon(
                                    Icons.arrow_forward_rounded,
                                    color: theme.colorScheme.primary,
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
            const SliverToBoxAdapter(
              child: OohSectionHeader(title: '人气推荐', subtitle: '读者正在追'),
            ),
            SliverPadding(
              padding: EdgeInsets.fromLTRB(horizontal, 0, horizontal, 100),
              sliver: SliverGrid.builder(
                gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: OohPageMetrics.gridColumns(
                    constraints.maxWidth,
                  ),
                  crossAxisSpacing: tablet ? 18 : 14,
                  mainAxisSpacing: 20,
                  childAspectRatio: .53,
                ),
                itemCount: tablet ? 10 : 6,
                itemBuilder: (context, index) {
                  final title = titles[index % titles.length];
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: LayoutBuilder(
                          builder: (_, size) => OohBookCover(
                            imageUrl: '',
                            title: title,
                            width: size.maxWidth,
                            height: size.maxHeight,
                          ),
                        ),
                      ),
                      const SizedBox(height: 9),
                      Text(
                        title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.titleSmall?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text('远航者', style: theme.textTheme.bodySmall),
                    ],
                  );
                },
              ),
            ),
          ],
        );

        final body = Scaffold(
          appBar: AppBar(title: const Text('发现')),
          body: catalog,
          bottomNavigationBar: tablet
              ? null
              : NavigationBar(
                  selectedIndex: 0,
                  destinations: const [
                    NavigationDestination(
                      icon: Icon(Icons.explore_rounded),
                      label: '发现',
                    ),
                    NavigationDestination(
                      icon: Icon(Icons.local_library_outlined),
                      label: '书库',
                    ),
                    NavigationDestination(
                      icon: Icon(Icons.shelves),
                      label: '书架',
                    ),
                    NavigationDestination(
                      icon: Icon(Icons.person_outline_rounded),
                      label: '我的',
                    ),
                  ],
                ),
        );

        if (!tablet) return body;
        return Scaffold(
          body: Row(
            children: [
              NavigationRail(
                selectedIndex: 0,
                labelType: NavigationRailLabelType.all,
                destinations: const [
                  NavigationRailDestination(
                    icon: Icon(Icons.explore_rounded),
                    label: Text('发现'),
                  ),
                  NavigationRailDestination(
                    icon: Icon(Icons.local_library_outlined),
                    label: Text('书库'),
                  ),
                  NavigationRailDestination(
                    icon: Icon(Icons.shelves),
                    label: Text('书架'),
                  ),
                  NavigationRailDestination(
                    icon: Icon(Icons.person_outline_rounded),
                    label: Text('我的'),
                  ),
                ],
              ),
              const VerticalDivider(width: 1),
              Expanded(child: body),
            ],
          ),
        );
      },
    );
  }
}
