import 'dart:convert';
import 'dart:typed_data';

import 'package:html/dom.dart';
import 'package:html/parser.dart' as html_parser;

typedef DictionaryResourceLoader = Future<Uint8List?> Function(String path);

class SanitizedDictionaryDefinition {
  const SanitizedDictionaryDefinition({
    required this.html,
    required this.audioResources,
  });

  final String html;
  final List<String> audioResources;
}

class DictionaryDefinitionSanitizer {
  const DictionaryDefinitionSanitizer();

  static const _allowedTags = <String>{
    'a',
    'b',
    'blockquote',
    'br',
    'code',
    'dd',
    'div',
    'dl',
    'dt',
    'em',
    'h1',
    'h2',
    'h3',
    'h4',
    'hr',
    'i',
    'img',
    'li',
    'ol',
    'p',
    'pre',
    'small',
    'span',
    'strong',
    'sub',
    'sup',
    'table',
    'tbody',
    'td',
    'th',
    'thead',
    'tr',
    'u',
    'ul',
  };
  static const _dropWithChildren = <String>{
    'applet',
    'audio',
    'button',
    'embed',
    'form',
    'iframe',
    'input',
    'link',
    'meta',
    'object',
    'script',
    'select',
    'source',
    'svg',
    'textarea',
    'video',
  };
  static const _voidTags = <String>{'br', 'hr', 'img'};
  static const _allowedStyleProperties = <String>{
    'background-color',
    'border',
    'border-color',
    'border-radius',
    'border-style',
    'border-width',
    'color',
    'font-size',
    'font-style',
    'font-weight',
    'letter-spacing',
    'line-height',
    'margin',
    'margin-bottom',
    'margin-left',
    'margin-right',
    'margin-top',
    'padding',
    'padding-bottom',
    'padding-left',
    'padding-right',
    'padding-top',
    'text-align',
    'text-decoration',
    'white-space',
  };

  Future<SanitizedDictionaryDefinition> sanitize(
    String definition, {
    DictionaryResourceLoader? loadResource,
  }) async {
    final fragment = html_parser.parseFragment(definition);
    final audio = <String>{};
    final parts = <String>[];
    for (final node in fragment.nodes) {
      parts.add(await _render(node, loadResource, audio, 0));
    }
    return SanitizedDictionaryDefinition(
      html: parts.join(),
      audioResources: List<String>.unmodifiable(audio),
    );
  }

  Future<String> _render(
    Node node,
    DictionaryResourceLoader? loadResource,
    Set<String> audio,
    int depth,
  ) async {
    if (depth > 64) return '';
    if (node is Text) return const HtmlEscape().convert(node.data);
    if (node is! Element) return '';
    final tag = node.localName?.toLowerCase() ?? '';
    final resource = _resourcePath(node.attributes['src']);
    if (tag == 'style') {
      final sheet = _safeStyleSheet(node.text);
      return sheet == null ? '' : '<style>$sheet</style>';
    }
    if (tag == 'link' &&
        node.attributes['rel']?.toLowerCase() == 'stylesheet' &&
        loadResource != null) {
      final path = _resourcePath(node.attributes['href']);
      final bytes = path == null ? null : await loadResource(path);
      if (bytes == null || bytes.length > 128 * 1024) return '';
      try {
        final sheet = _safeStyleSheet(utf8.decode(bytes));
        return sheet == null ? '' : '<style>$sheet</style>';
      } on FormatException {
        return '';
      }
    }
    if (tag == 'audio') {
      if (resource != null) audio.add(resource);
      for (final source in node.querySelectorAll('source')) {
        final childResource = _resourcePath(source.attributes['src']);
        if (childResource != null) audio.add(childResource);
      }
      return '';
    }
    if (tag == 'source' && resource != null) {
      audio.add(resource);
      return '';
    }
    if (_dropWithChildren.contains(tag)) return '';
    final children = <String>[];
    for (final child in node.nodes) {
      children.add(await _render(child, loadResource, audio, depth + 1));
    }
    if (!_allowedTags.contains(tag)) return children.join();

    final attributes = <String>[];
    final style = _safeStyle(node.attributes['style']);
    if (style != null) attributes.add('style="${_attribute(style)}"');
    final className = node.attributes['class'];
    if (className != null &&
        className.length <= 256 &&
        RegExp(r'^[a-zA-Z0-9_ -]+$').hasMatch(className)) {
      attributes.add('class="${_attribute(className)}"');
    }
    final id = node.attributes['id'];
    if (id != null &&
        id.length <= 128 &&
        RegExp(r'^[a-zA-Z][a-zA-Z0-9_-]*$').hasMatch(id)) {
      attributes.add('id="${_attribute(id)}"');
    }
    if (tag == 'a') {
      final sound = _soundPath(node.attributes['href']);
      if (sound != null) {
        audio.add(sound);
        attributes.add(
          'href="audio://${_attribute(Uri.encodeComponent(sound))}"',
        );
      } else {
        final href = _entryHref(node.attributes['href']);
        if (href != null) attributes.add('href="${_attribute(href)}"');
      }
    } else if (tag == 'img' && resource != null && loadResource != null) {
      final bytes = await loadResource(resource);
      final mime = bytes == null ? null : _imageMime(bytes);
      if (bytes == null || mime == null || bytes.length > 4 * 1024 * 1024) {
        return '';
      }
      final source = 'data:$mime;base64,${base64Encode(bytes)}';
      attributes.add('src="${_attribute(source)}"');
      final alt = node.attributes['alt'];
      if (alt != null && alt.length <= 256) {
        attributes.add('alt="${_attribute(alt)}"');
      }
    } else if (tag == 'img') {
      return '';
    }
    final opening = attributes.isEmpty
        ? '<$tag>'
        : '<$tag ${attributes.join(' ')}>';
    if (_voidTags.contains(tag)) return opening;
    return '$opening${children.join()}</$tag>';
  }
}

String? _entryHref(String? value) {
  if (value == null || value.length > 1024) return null;
  final trimmed = value.trim();
  final lower = trimmed.toLowerCase();
  if (!lower.startsWith('entry://') && !lower.startsWith('bword://')) {
    return null;
  }
  late String term;
  try {
    term = Uri.decodeComponent(
      trimmed.substring(trimmed.indexOf('://') + 3),
    ).trim();
  } on FormatException {
    return null;
  }
  if (term.isEmpty || term.length > 512 || term.contains('\u0000')) return null;
  return 'entry://${Uri.encodeComponent(term)}';
}

String? _resourcePath(String? value) {
  if (value == null || value.length > 1024) return null;
  final normalized = value.trim().replaceAll('\\', '/');
  if (normalized.isEmpty ||
      normalized.contains('\u0000') ||
      normalized.startsWith('//') ||
      RegExp(r'^[a-zA-Z][a-zA-Z0-9+.-]*:').hasMatch(normalized)) {
    return null;
  }
  final path = normalized.replaceFirst(RegExp(r'^/+'), '');
  final segments = path.split('/');
  if (segments.any((segment) => segment.isEmpty || segment == '..')) {
    return null;
  }
  return segments.where((segment) => segment != '.').join('/');
}

String? _soundPath(String? value) {
  if (value == null || !value.toLowerCase().startsWith('sound://')) return null;
  late String decoded;
  try {
    decoded = Uri.decodeComponent(value.substring('sound://'.length));
  } on FormatException {
    return null;
  }
  return _resourcePath(decoded);
}

String? _safeStyle(String? value) {
  if (value == null || value.length > 2048) return null;
  final safe = <String>[];
  for (final declaration in value.split(';')) {
    final separator = declaration.indexOf(':');
    if (separator <= 0) continue;
    final property = declaration.substring(0, separator).trim().toLowerCase();
    final content = declaration.substring(separator + 1).trim();
    final lower = content.toLowerCase();
    if (!_DictionaryStyleRules.allowed.contains(property) ||
        content.isEmpty ||
        content.length > 256 ||
        lower.contains('url(') ||
        lower.contains('expression') ||
        lower.contains('@import') ||
        lower.contains('javascript:') ||
        lower.contains('behavior:') ||
        content.contains('<') ||
        content.contains('>')) {
      continue;
    }
    safe.add('$property: $content');
  }
  return safe.isEmpty ? null : safe.join('; ');
}

String? _safeStyleSheet(String value) {
  if (value.length > 128 * 1024) return null;
  final withoutComments = value.replaceAll(RegExp(r'/\*[\s\S]*?\*/'), '');
  final lower = withoutComments.toLowerCase();
  if (lower.contains('@import') ||
      lower.contains('url(') ||
      lower.contains('expression') ||
      lower.contains('javascript:') ||
      lower.contains('@font-face')) {
    return null;
  }
  final result = <String>[];
  final matches = RegExp(r'([^{}]+)\{([^{}]*)\}').allMatches(withoutComments);
  for (final match in matches) {
    final selector = match.group(1)!.trim();
    if (selector.isEmpty ||
        selector.length > 512 ||
        !RegExp(r'^[a-zA-Z0-9_ .#,:>+~-]+$').hasMatch(selector)) {
      continue;
    }
    final declarations = _safeStyle(match.group(2));
    if (declarations != null) result.add('$selector {$declarations}');
  }
  return result.isEmpty ? null : result.join('\n');
}

class _DictionaryStyleRules {
  static const allowed = DictionaryDefinitionSanitizer._allowedStyleProperties;
}

String _attribute(String value) =>
    const HtmlEscape(HtmlEscapeMode.attribute).convert(value);

String? _imageMime(Uint8List bytes) {
  if (bytes.length >= 8 &&
      bytes[0] == 0x89 &&
      bytes[1] == 0x50 &&
      bytes[2] == 0x4e &&
      bytes[3] == 0x47 &&
      bytes[4] == 0x0d &&
      bytes[5] == 0x0a &&
      bytes[6] == 0x1a &&
      bytes[7] == 0x0a) {
    return 'image/png';
  }
  if (bytes.length >= 3 &&
      bytes[0] == 0xff &&
      bytes[1] == 0xd8 &&
      bytes[2] == 0xff) {
    return 'image/jpeg';
  }
  if (bytes.length >= 6) {
    final signature = ascii.decode(bytes.sublist(0, 6), allowInvalid: true);
    if (signature == 'GIF87a' || signature == 'GIF89a') return 'image/gif';
  }
  if (bytes.length >= 12 &&
      ascii.decode(bytes.sublist(0, 4), allowInvalid: true) == 'RIFF' &&
      ascii.decode(bytes.sublist(8, 12), allowInvalid: true) == 'WEBP') {
    return 'image/webp';
  }
  return null;
}
