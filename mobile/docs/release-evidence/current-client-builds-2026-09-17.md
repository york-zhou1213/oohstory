# OOHStory 1.27.0+75 current-client build evidence

- Date: 2026-09-17
- Source snapshot: GitHub branch `build/current-v1.27.0-20260917`
- Local source: current OOHStory worktree snapshot copied into the isolated branch
- Distribution status: all files below are unsigned unless explicitly stated

## Verification

- `flutter analyze`: 0 issues.
- Final GitHub Flutter suite: 277 passed, 1 explicitly skipped live-Joplin acceptance.
- Linux x64 Release build: passed.
- Linux Debian package metadata, ownership/mode and contents: passed.
- Android split Release APK and AAB builds: passed; `apksigner` confirms they are
  unsigned and therefore are not represented as production-signed packages.
- GitHub Actions run `35130560811` at source snapshot `392e41c`: Windows x64,
  macOS, and universal iPhone/iPad unsigned Release jobs all passed. An earlier
  run exposed a generated CocoaPods 10.14 deployment target; the final run uses
  a tracked 10.15 Podfile and Xcode deployment target.
- Windows ZIP/installer and iPhone/iPad ZIP downloaded from Actions and rechecked
  against their emitted SHA-256 files. The Apple bundle is version 1.27.0 (75),
  bundle identifier `com.oohstory.oohstory`, targets device families `[1, 2]`,
  and contains no embedded provisioning profile.
- The macOS bundle is version 1.27.0 (75), bundle identifier
  `com.oohstory.oohstory`, minimum macOS 10.15, and contains the app sandbox,
  outbound network, user-selected read/write and Keychain Sharing entitlements.

## Verified artifacts

Artifacts are kept under
`oohstory-artifacts/current-worktree-20260916/release-packages/` outside the source
tree.

| Artifact | SHA-256 |
| --- | --- |
| Android arm64-v8a unsigned APK | `eb7158f562ce18f5f9a48386302f9704f67331bffa3661c8ab65f94275164a6f` |
| Android armeabi-v7a unsigned APK | `a32bbd7b03fcdaebcebbf7514ef5cc1b04433adc0b18dfe996090a6ad27cb972` |
| Android x86_64 unsigned APK | `27d4164dfca17b1d5de44fa9da7b404a1675195d0c1e66379cf4b53b262bb6e9` |
| Android unsigned AAB | `11dbb8d440591e35c8a0012dfc31de4f19b070a6bd47ac39c92ff2ba8cb8ece5` |
| Linux amd64 `.deb` | `ff972afac113764d0b3a1a27d1a9985c972266534278d5385975d89d5670b345` |
| Windows x64 portable ZIP | `f7416a8f0d16faa9370b518d98dd4f2603342e2aa182a00062f9c7c68383ab22` |
| Windows x64 unsigned installer | `296245a9b65673a8319f104bcbeb1ba868b2482478e5574978f1a1afcb6a4230` |
| iPhone/iPad unsigned ZIP | `a224c8b8081926652aa081823eff093bf04be587193a67445ea6d38abf37d08e` |
| macOS unsigned ZIP | `f5b3d4651d9aa2b679b71d12ba16ae7d55dda647d104ec8d9e854000b7040bf3` |
| macOS unsigned DMG | `eac017b91ca62b7fa424b150a975465833de9fd677b0995aa35c091df848e264` |

## External release boundaries

Signing and real-service acceptance are intentionally not inferred from a
successful compile. Android needs the owner release keystore; Windows needs a
code-signing certificate; Apple packages need an Apple team, certificates,
provisioning and notarization/TestFlight. Dropbox/Google/Readwise/Notion and
packaged Joplin/Desktop acceptance require the corresponding external accounts,
sandboxes, credentials or real applications.
