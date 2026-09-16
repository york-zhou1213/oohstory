import 'package:flutter/material.dart';

/// OOHStory's shared visual language.
///
/// Product surfaces use one cobalt accent, cool neutral surfaces and a fixed
/// radius hierarchy: 20 for content cards, 14 for controls and full pills for
/// compact filters. The aliases at the bottom keep older screens source
/// compatible while they migrate to the semantic names.
class AppTheme {
  static const brandBlue = Color(0xFF315FC7);
  static const brandBlueDark = Color(0xFF9AB5F5);
  static const brandNavy = Color(0xFF111A2E);
  static const sky = Color(0xFF4F88C9);
  static const success = Color(0xFF19875A);
  static const warning = Color(0xFFD87819);
  static const danger = Color(0xFFD64D5E);

  static const lightCanvas = Color(0xFFF7F8FA);
  static const lightSurface = Color(0xFFFFFFFF);
  static const lightSurfaceMuted = Color(0xFFEEF1F5);
  static const lightInk = Color(0xFF171A21);
  static const lightInkMuted = Color(0xFF68707E);

  static const darkCanvas = Color(0xFF0C0F15);
  static const darkSurface = Color(0xFF151922);
  static const darkSurfaceMuted = Color(0xFF202632);
  static const darkInk = Color(0xFFF2F5FB);
  static const darkInkMuted = Color(0xFFA9B2C2);

  static const double contentMaxWidth = 1440;
  static const double wideNavigationBreakpoint = 760;
  static const double expandedRailBreakpoint = 1120;
  static const double readerContentMaxWidth = 760;
  static const double cardRadius = 16;
  static const double controlRadius = 12;

  static const heroGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF111A2E), Color(0xFF20345E)],
    stops: [0, 1],
  );

  static ThemeData light() => _theme(Brightness.light);

  static ThemeData dark() => _theme(Brightness.dark);

  static ThemeData _theme(Brightness brightness) {
    final dark = brightness == Brightness.dark;
    final primary = dark ? brandBlueDark : brandBlue;
    final surface = dark ? darkSurface : lightSurface;
    final canvas = dark ? darkCanvas : lightCanvas;
    final mutedSurface = dark ? darkSurfaceMuted : lightSurfaceMuted;
    final ink = dark ? darkInk : lightInk;
    final mutedInk = dark ? darkInkMuted : lightInkMuted;
    final scheme = ColorScheme(
      brightness: brightness,
      primary: primary,
      onPrimary: dark ? const Color(0xFF07122E) : Colors.white,
      primaryContainer: dark
          ? const Color(0xFF1A326A)
          : const Color(0xFFDCE6FF),
      onPrimaryContainer: dark
          ? const Color(0xFFD9E3FF)
          : const Color(0xFF0C286E),
      secondary: dark ? const Color(0xFF72D6F7) : const Color(0xFF087FA8),
      onSecondary: dark ? const Color(0xFF05242E) : Colors.white,
      secondaryContainer: dark
          ? const Color(0xFF123B49)
          : const Color(0xFFD4F3FF),
      onSecondaryContainer: dark
          ? const Color(0xFFC9F3FF)
          : const Color(0xFF073B4C),
      tertiary: dark ? const Color(0xFF8ADCB4) : success,
      onTertiary: dark ? const Color(0xFF072B1A) : Colors.white,
      tertiaryContainer: dark
          ? const Color(0xFF143D2A)
          : const Color(0xFFD7F5E6),
      onTertiaryContainer: dark
          ? const Color(0xFFD7F5E6)
          : const Color(0xFF0C3D27),
      error: dark ? const Color(0xFFFFA4AC) : danger,
      onError: dark ? const Color(0xFF52000A) : Colors.white,
      errorContainer: dark ? const Color(0xFF5C222B) : const Color(0xFFFFDADD),
      onErrorContainer: dark
          ? const Color(0xFFFFDADD)
          : const Color(0xFF65000D),
      surface: surface,
      onSurface: ink,
      surfaceContainerHighest: mutedSurface,
      onSurfaceVariant: mutedInk,
      outline: dark ? const Color(0xFF465164) : const Color(0xFFC8D0DE),
      outlineVariant: dark ? const Color(0xFF293244) : const Color(0xFFE1E6EF),
      shadow: const Color(0xFF071A44),
      scrim: const Color(0xFF05070C),
      inverseSurface: dark ? lightSurface : darkSurface,
      onInverseSurface: dark ? lightInk : darkInk,
      inversePrimary: dark ? brandBlue : brandBlueDark,
    );

    final baseText = ThemeData(
      brightness: brightness,
      useMaterial3: true,
    ).textTheme;
    final textTheme = baseText.copyWith(
      displaySmall: baseText.displaySmall?.copyWith(
        color: ink,
        fontWeight: FontWeight.w800,
        letterSpacing: -1.2,
        height: 1.08,
      ),
      headlineLarge: baseText.headlineLarge?.copyWith(
        color: ink,
        fontWeight: FontWeight.w800,
        letterSpacing: -.8,
        height: 1.12,
      ),
      headlineMedium: baseText.headlineMedium?.copyWith(
        color: ink,
        fontWeight: FontWeight.w800,
        letterSpacing: -.6,
        height: 1.15,
      ),
      titleLarge: baseText.titleLarge?.copyWith(
        color: ink,
        fontWeight: FontWeight.w700,
        letterSpacing: -.35,
      ),
      titleMedium: baseText.titleMedium?.copyWith(
        color: ink,
        fontWeight: FontWeight.w700,
        letterSpacing: -.2,
      ),
      bodyLarge: baseText.bodyLarge?.copyWith(color: ink, height: 1.58),
      bodyMedium: baseText.bodyMedium?.copyWith(color: ink, height: 1.5),
      bodySmall: baseText.bodySmall?.copyWith(color: mutedInk, height: 1.4),
      labelLarge: baseText.labelLarge?.copyWith(fontWeight: FontWeight.w700),
    );

    final cardShape = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(cardRadius),
      side: BorderSide(color: scheme.outlineVariant),
    );
    final controlShape = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(controlRadius),
    );

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: canvas,
      canvasColor: canvas,
      textTheme: textTheme,
      visualDensity: VisualDensity.standard,
      cupertinoOverrideTheme: MaterialBasedCupertinoThemeData(
        materialTheme: ThemeData(
          brightness: brightness,
          colorScheme: scheme,
          textTheme: textTheme,
        ),
      ),
      dividerColor: scheme.outlineVariant,
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: ZoomPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
          TargetPlatform.macOS: CupertinoPageTransitionsBuilder(),
        },
      ),
      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        backgroundColor: canvas,
        surfaceTintColor: Colors.transparent,
        foregroundColor: ink,
        titleTextStyle: textTheme.titleLarge,
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: cardShape,
        color: surface,
        surfaceTintColor: Colors.transparent,
      ),
      navigationBarTheme: NavigationBarThemeData(
        height: 68,
        elevation: 0,
        backgroundColor: surface.withValues(alpha: .98),
        surfaceTintColor: Colors.transparent,
        indicatorColor: scheme.primaryContainer.withValues(alpha: .72),
        indicatorShape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
        iconTheme: WidgetStateProperty.resolveWith(
          (states) => IconThemeData(
            size: 24,
            color: states.contains(WidgetState.selected) ? primary : mutedInk,
          ),
        ),
        labelTextStyle: WidgetStateProperty.resolveWith(
          (states) => TextStyle(
            fontSize: 11,
            fontWeight: states.contains(WidgetState.selected)
                ? FontWeight.w700
                : FontWeight.w600,
            color: states.contains(WidgetState.selected) ? primary : mutedInk,
          ),
        ),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: surface,
        indicatorColor: scheme.primaryContainer,
        selectedIconTheme: IconThemeData(color: primary),
        unselectedIconTheme: IconThemeData(color: mutedInk),
        selectedLabelTextStyle: TextStyle(
          color: primary,
          fontWeight: FontWeight.w700,
        ),
        unselectedLabelTextStyle: TextStyle(
          color: mutedInk,
          fontWeight: FontWeight.w600,
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: mutedSurface,
        selectedColor: scheme.primaryContainer,
        disabledColor: mutedSurface.withValues(alpha: .55),
        labelStyle: TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w600,
          color: ink,
        ),
        shape: const StadiumBorder(),
        side: BorderSide.none,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(48, 48),
          backgroundColor: primary,
          foregroundColor: scheme.onPrimary,
          disabledBackgroundColor: mutedSurface,
          disabledForegroundColor: mutedInk,
          shape: controlShape,
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
          textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(48, 48),
          foregroundColor: primary,
          side: BorderSide(color: scheme.outline),
          shape: controlShape,
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 13),
          textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: primary,
          shape: controlShape,
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: ink,
          highlightColor: scheme.primaryContainer,
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surface,
        labelStyle: TextStyle(color: mutedInk),
        hintStyle: TextStyle(color: mutedInk.withValues(alpha: .76)),
        prefixIconColor: mutedInk,
        suffixIconColor: mutedInk,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(controlRadius),
          borderSide: BorderSide(color: scheme.outlineVariant),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(controlRadius),
          borderSide: BorderSide(color: scheme.outlineVariant),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(controlRadius),
          borderSide: BorderSide(color: primary, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(controlRadius),
          borderSide: BorderSide(color: scheme.error),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 14,
        ),
      ),
      searchBarTheme: SearchBarThemeData(
        elevation: const WidgetStatePropertyAll(0),
        backgroundColor: WidgetStatePropertyAll(surface),
        surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
        overlayColor: WidgetStatePropertyAll(
          scheme.primaryContainer.withValues(alpha: .42),
        ),
        shape: WidgetStatePropertyAll(controlShape),
        side: WidgetStatePropertyAll(BorderSide(color: scheme.outlineVariant)),
        hintStyle: WidgetStatePropertyAll(
          textTheme.bodyMedium?.copyWith(color: mutedInk),
        ),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: mutedInk,
        textColor: ink,
        titleTextStyle: textTheme.bodyLarge?.copyWith(
          fontWeight: FontWeight.w700,
        ),
        subtitleTextStyle: textTheme.bodySmall,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(controlRadius),
        ),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          side: WidgetStatePropertyAll(
            BorderSide(color: scheme.outlineVariant),
          ),
          shape: WidgetStatePropertyAll(controlShape),
          textStyle: const WidgetStatePropertyAll(
            TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
          ),
        ),
      ),
      dialogTheme: DialogThemeData(
        elevation: 0,
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        shape: cardShape,
      ),
      bottomSheetTheme: BottomSheetThemeData(
        elevation: 0,
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: dark ? darkInk : darkSurface,
        contentTextStyle: TextStyle(color: dark ? lightInk : darkInk),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(color: primary),
      dividerTheme: DividerThemeData(
        color: scheme.outlineVariant,
        thickness: 1,
      ),
    );
  }

  static BoxShadow softShadow(Brightness brightness) => BoxShadow(
    color: (brightness == Brightness.dark ? Colors.black : brandNavy)
        .withValues(alpha: brightness == Brightness.dark ? .28 : .08),
    blurRadius: 26,
    offset: const Offset(0, 10),
  );

  // Compatibility aliases for older surfaces. They intentionally resolve to
  // the new cobalt system so the whole app moves as one visual product.
  static const Color seedPurple = brandBlue;
  static const Color accentPink = sky;
}
