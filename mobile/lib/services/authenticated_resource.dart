import 'api_service.dart';

Map<String, String> oohstoryAuthenticatedResourceHeaders(
  String resourceUrl,
  Map<String, String> authenticatedHeaders,
) {
  final trustedOrigin = Uri.parse(ApiService.baseUrl);
  final parsedResource = Uri.tryParse(resourceUrl);
  if (parsedResource == null) return const <String, String>{};
  final resource = parsedResource.hasAuthority
      ? parsedResource
      : trustedOrigin.resolveUri(parsedResource);
  final isTrusted =
      resource.scheme == 'https' &&
      trustedOrigin.scheme == 'https' &&
      resource.host.toLowerCase() == trustedOrigin.host.toLowerCase() &&
      resource.port == trustedOrigin.port;
  return isTrusted
      ? Map<String, String>.unmodifiable(authenticatedHeaders)
      : const <String, String>{};
}
