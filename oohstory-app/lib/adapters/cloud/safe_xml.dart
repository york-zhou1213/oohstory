import '../../core/errors.dart';

final RegExp _forbiddenXml = RegExp(
  r'<!\s*(?:DOCTYPE|ENTITY)',
  caseSensitive: false,
);

List<String> xmlElements(String source, String localName) {
  if (_forbiddenXml.hasMatch(source)) {
    throw const CoreException(
      CoreErrorCode.upstreamError,
      'Cloud provider returned unsafe XML',
    );
  }
  final name = RegExp.escape(localName);
  final expression = RegExp(
    '<(?:[A-Za-z_][\\w.-]*:)?$name(?:\\s[^>]*)?>([\\s\\S]*?)'
    '</(?:[A-Za-z_][\\w.-]*:)?$name\\s*>',
  );
  return expression
      .allMatches(source)
      .map((match) => match.group(1)!)
      .toList(growable: false);
}

String? xmlText(String source, String localName) {
  final values = xmlElements(source, localName);
  if (values.isEmpty) return null;
  final raw = values.first.replaceAll(RegExp(r'<[^>]*>'), '').trim();
  if (raw.isEmpty) return null;
  return _decodeEntities(raw);
}

bool hasXmlElement(String source, String localName) {
  final name = RegExp.escape(localName);
  return RegExp(
    '<(?:[A-Za-z_][\\w.-]*:)?$name(?:\\s[^>]*)?/?>',
  ).hasMatch(source);
}

String _decodeEntities(String value) {
  final decoded = value.replaceAllMapped(
    RegExp(r'&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);'),
    (match) {
      final entity = match.group(0)!;
      return switch (entity) {
        '&amp;' => '&',
        '&lt;' => '<',
        '&gt;' => '>',
        '&quot;' => '"',
        '&apos;' => "'",
        _ => String.fromCharCode(
          entity.startsWith('&#x')
              ? int.parse(entity.substring(3, entity.length - 1), radix: 16)
              : int.parse(entity.substring(2, entity.length - 1)),
        ),
      };
    },
  );
  if (RegExp(r'&[A-Za-z#][^;]{0,32};').hasMatch(decoded)) {
    throw const CoreException(
      CoreErrorCode.upstreamError,
      'Cloud provider returned unsafe XML',
    );
  }
  return decoded;
}
