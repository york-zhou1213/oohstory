enum CoreErrorCode {
  validationError('validation_error'),
  unauthorized('unauthorized'),
  forbidden('forbidden'),
  notFound('not_found'),
  revisionConflict('revision_conflict'),
  payloadTooLarge('payload_too_large'),
  rateLimitExceeded('rate_limit_exceeded'),
  internalError('internal_error'),
  upstreamError('upstream_error'),
  unsupported('unsupported');

  const CoreErrorCode(this.wireName);
  final String wireName;
}

class CoreException implements Exception {
  const CoreException(this.code, this.message, {this.correlationId});

  final CoreErrorCode code;
  final String message;
  final String? correlationId;

  Map<String, Object?> toJson() => <String, Object?>{
    'error': code.wireName,
    'message': message,
    'correlation_id': correlationId,
  };

  @override
  String toString() => 'CoreException(${code.wireName}): $message';
}
