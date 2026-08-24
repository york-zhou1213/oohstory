import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/services/api_service.dart';
import 'package:oohstory/services/ooh_origin_transport.dart';

void main() {
  test('release builds always have a usable production origin', () {
    expect(productionOriginIp, '154.218.0.70');
    expect(productionOriginIp.trim(), isNotEmpty);
  });

  test('production media uses the trusted origin while retaining TLS host', () {
    final uri = Uri.parse(
      'https://oohstory.com/api/v1/books/example/cover?v=cover-hash',
    );

    expect(productionTargetHost(uri, originIp: '203.0.113.40'), '203.0.113.40');
    expect(uri.host, 'oohstory.com');
    expect(uri.scheme, 'https');
  });

  test('unrelated hosts never inherit the OOHStory origin override', () {
    final uri = Uri.parse('https://example.org/cover.jpg');
    expect(productionTargetHost(uri, originIp: '203.0.113.40'), 'example.org');
  });

  test('cover paths are canonicalized without weakening HTTPS', () {
    final api = ApiService();
    addTearDown(api.dispose);

    expect(
      api.fullCoverUrl('/api/v1/books/example/cover?v=hash'),
      'https://oohstory.com/api/v1/books/example/cover?v=hash',
    );
    expect(
      api.fullCoverUrl('https://www.oohstory.com/api/v1/books/example/cover'),
      'https://oohstory.com/api/v1/books/example/cover',
    );
    expect(api.fullCoverUrl('http://oohstory.com/cover.jpg'), isEmpty);
  });
}
