# Security and quality gates

Every release runs the repository quality gate before deployment. The gate
checks Python correctness and unused code, timezone-safe datetime usage,
dependency advisories, test coverage, bytecode compilation, and shell syntax.

Broad exception handling is rejected by default. A suppression is permitted
only at an HTTP, background-task, subprocess, or third-party provider boundary
where the boundary must isolate an unknown implementation exception. Existing
legacy boundary suppressions form a reviewed baseline; new unmarked broad
handlers fail CI. Code inside domain and storage operations must catch concrete
exception types or allow the error to propagate.

Large modules are reduced by extracting independently testable security
boundaries. EPUB parsing now lives in `epub_text.py`; browser audiobook caching
lives in the Reader's `audiobook-cache.js`. Future feature work should continue
extracting code instead of extending the legacy service and application files.
