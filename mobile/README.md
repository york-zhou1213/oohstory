# OOH Story mobile client

Flutter client for Android and iOS. The repository contains no OAuth client
secrets, signing keystore, signed APK, provisioning profile, or production API
endpoint.

## Development

```bash
flutter pub get
flutter test
flutter run --dart-define=OOHSTORY_API_BASE_URL=http://127.0.0.1:8091
```

Android emulators normally reach a host Reader through `10.0.2.2`:

```bash
flutter run --dart-define=OOHSTORY_API_BASE_URL=http://10.0.2.2:8091
```

## Release build

Configure Google OAuth only when needed as described in `GOOGLE_OAUTH.md`.
For Android signing, copy `android/key.properties.example` to
`android/key.properties`, point it at a locally protected keystore, and never
commit either file. Release builds no longer fall back to Flutter's debug key.

```bash
flutter build apk --release \
  --dart-define=OOHSTORY_API_BASE_URL=https://reader.example.com \
  --dart-define=GOOGLE_WEB_CLIENT_ID= \
  --dart-define=GOOGLE_IOS_CLIENT_ID=
```

Publish signed packages through a release system such as GitHub Releases; do
not commit APK/AAB/IPA files to the source tree.
