import 'dart:io';

// The native app must not traverse the public Cloudflare challenge path.
//
// This address is the public, TLS-terminating OOHStory origin proxy. It is not
// a credential. Keeping a production default makes ad-hoc release builds safe:
// forgetting a --dart-define must never ship an app whose API and covers are
// guaranteed to receive an HTML challenge instead of JSON/JPEG responses.
const productionOriginIp = String.fromEnvironment(
  'OOHSTORY_ORIGIN_IP',
  defaultValue: '154.218.0.70',
);

/// Installs the same production-origin transport for every Dart network
/// consumer, including Image.network and cached_network_image.
///
/// API calls already use [configureProductionOrigin] explicitly. Flutter's
/// image pipeline creates its own HttpClient, so without a global override
/// cover requests fall back through Cloudflare and native clients receive an
/// interactive challenge that they cannot complete.
void installProductionOriginOverrides() {
  if (productionOriginIp.trim().isEmpty) return;
  HttpOverrides.global = OohStoryHttpOverrides();
}

class OohStoryHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) {
    final client = super.createHttpClient(context)
      ..connectionTimeout = const Duration(seconds: 15)
      ..idleTimeout = const Duration(seconds: 30);
    configureProductionOrigin(client);
    return client;
  }
}

void configureProductionOrigin(HttpClient client) {
  if (productionOriginIp.isEmpty) return;
  client.findProxy = (_) => 'DIRECT';
  client.connectionFactory = connectToProductionOrigin;
}

Future<ConnectionTask<Socket>> connectToProductionOrigin(
  Uri url,
  String? proxyHost,
  int? proxyPort,
) async {
  final host = productionTargetHost(url);
  final rawTask = await Socket.startConnect(host, url.port);
  if (url.scheme != 'https') return rawTask;
  final secureSocket = rawTask.socket.then(
    (socket) => SecureSocket.secure(socket, host: url.host),
  );
  return ConnectionTask.fromSocket<Socket>(secureSocket, rawTask.cancel);
}

String productionTargetHost(Uri url, {String? originIp}) {
  final configuredOrigin = (originIp ?? productionOriginIp).trim();
  if (url.host == 'oohstory.com' && configuredOrigin.isNotEmpty) {
    return configuredOrigin;
  }
  return url.host;
}
