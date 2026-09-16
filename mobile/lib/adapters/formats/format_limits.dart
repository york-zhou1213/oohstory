class FormatLimits {
  const FormatLimits({
    this.maxInputBytes = 64 * 1024 * 1024,
    this.maxExpandedBytes = 256 * 1024 * 1024,
    this.maxEntryBytes = 64 * 1024 * 1024,
    this.maxEntries = 4096,
    this.maxPages = 2000,
    this.maxExpansionRatio = 100,
  });

  final int maxInputBytes;
  final int maxExpandedBytes;
  final int maxEntryBytes;
  final int maxEntries;
  final int maxPages;
  final int maxExpansionRatio;
}
