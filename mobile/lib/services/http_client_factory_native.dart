import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:http/io_client.dart' as io_client;

import 'ooh_origin_transport.dart';

http.Client createOohHttpClient() {
  final client = HttpClient()
    ..badCertificateCallback = _rejectBadCertificate
    ..connectionTimeout = const Duration(seconds: 15)
    ..idleTimeout = const Duration(seconds: 30);
  configureProductionOrigin(client);
  return io_client.IOClient(client);
}

bool _rejectBadCertificate(X509Certificate cert, String host, int port) {
  return false;
}
