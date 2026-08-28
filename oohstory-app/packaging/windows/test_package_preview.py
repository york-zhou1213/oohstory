from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import package_preview


SOURCE_COMMIT = "1" * 40
WINDOWS_TREE = "2" * 40
RUN_ID = "123456789"


class PackagePreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        files = {
            "oohstory.exe": b"portable executable",
            "flutter_windows.dll": b"flutter runtime",
            "plugin.dll": b"plugin runtime",
            "data/icudtl.dat": b"icu data",
            "data/app.so": b"aot data",
            "data/flutter_assets/AssetManifest.bin": b"assets",
            "data/flutter_assets/assets/oohstory-brand-icon.png": b"icon",
        }
        for relative, content in files.items():
            path = self.bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create(self, output: str = "output") -> tuple[Path, Path]:
        return package_preview.create_preview(
            self.bundle,
            self.root / output,
            SOURCE_COMMIT,
            RUN_ID,
            WINDOWS_TREE,
        )

    def test_create_and_verify_complete_portable_archive(self) -> None:
        archive, manifest = self._create()
        package_preview.verify_preview(archive, manifest, SOURCE_COMMIT, RUN_ID, WINDOWS_TREE)
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(metadata["source_commit"], SOURCE_COMMIT)
        self.assertEqual(metadata["workflow_run_id"], RUN_ID)
        self.assertEqual(metadata["windows_tree"], WINDOWS_TREE)
        self.assertEqual(metadata["archive"]["sha256"], package_preview.sha256_file(archive))
        self.assertEqual(metadata["executable"]["path"], f"{package_preview.PACKAGE_ROOT}/OohStory.exe")
        executable_records = [
            record for record in metadata["files"] if record["path"] == metadata["executable"]["path"]
        ]
        self.assertEqual(executable_records, [metadata["executable"]])
        with zipfile.ZipFile(archive) as preview:
            names = preview.namelist()
        self.assertIn(f"{package_preview.PACKAGE_ROOT}/flutter_windows.dll", names)
        self.assertIn(f"{package_preview.PACKAGE_ROOT}/data/flutter_assets/AssetManifest.bin", names)
        self.assertNotIn(f"{package_preview.PACKAGE_ROOT}/oohstory.exe", names)

    def test_archive_and_manifest_are_deterministic(self) -> None:
        first_archive, first_manifest = self._create("first")
        second_archive, second_manifest = self._create("second")
        self.assertEqual(package_preview.sha256_file(first_archive), package_preview.sha256_file(second_archive))
        self.assertEqual(first_manifest.read_bytes(), second_manifest.read_bytes())

    def test_missing_executable_is_rejected(self) -> None:
        (self.bundle / "oohstory.exe").unlink()
        with self.assertRaisesRegex(package_preview.PreviewError, "missing required paths"):
            self._create()

    def test_tampered_archive_is_rejected(self) -> None:
        archive, manifest = self._create()
        with archive.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(package_preview.PreviewError, "archive SHA-256 mismatch"):
            package_preview.verify_preview(archive, manifest, SOURCE_COMMIT, RUN_ID, WINDOWS_TREE)

    def test_tampered_executable_identity_is_rejected(self) -> None:
        archive, manifest = self._create()
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        mutations = {
            "sha256": "0" * 64,
            "size_bytes": metadata["executable"]["size_bytes"] + 1,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                tampered = json.loads(json.dumps(metadata))
                tampered["executable"][field] = value
                tampered_manifest = self.root / f"tampered-executable-{field}.json"
                tampered_manifest.write_text(json.dumps(tampered), encoding="utf-8")
                with self.assertRaisesRegex(
                    package_preview.PreviewError,
                    "manifest executable record mismatch",
                ):
                    package_preview.verify_preview(
                        archive,
                        tampered_manifest,
                        SOURCE_COMMIT,
                        RUN_ID,
                        WINDOWS_TREE,
                    )

    def test_workflow_enforces_lockfile_and_tracked_source_identity(self) -> None:
        workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "windows-preview.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("FLUTTER_VERSION: 3.32.8", workflow)
        self.assertIn(
            "FLUTTER_TOOL_SOURCE_SHA256: f7c7f31d28a8ce3894ca16e4d78940710bec0c06c1d40f360d05f1b3c8e1362c",
            workflow,
        )
        self.assertIn(
            "FLUTTER_TOOL_PATCHED_SHA256: 33cf59e3bbf4a61907b6ca656c19ee40505e6eafdc70a17563c882e5fd0bbe6a",
            workflow,
        )
        self.assertIn("18 => 'Visual Studio 18 2026'", workflow)
        regression = workflow.index("- name: Regress tracked-source drift guard")
        mutation = workflow.index("tracked-source drift guard did not detect mutation")
        resolve = workflow.index("flutter pub get --enforce-lockfile")
        tracked_drift = workflow.index("git diff --quiet --exit-code $env:GITHUB_SHA --", resolve)
        build = workflow.index("- name: Build native Windows release")
        no_second_resolution = workflow.index("flutter build windows --release --no-pub")
        self.assertLess(regression, mutation)
        self.assertLess(mutation, resolve)
        self.assertLess(resolve, tracked_drift)
        self.assertLess(tracked_drift, build)
        self.assertGreater(no_second_resolution, build)

    def test_unsafe_archive_entry_is_rejected(self) -> None:
        archive, manifest = self._create()
        unsafe_archive = self.root / package_preview.ARCHIVE_NAME
        with zipfile.ZipFile(unsafe_archive, "w") as output:
            output.writestr("../escape.txt", b"unsafe")
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        metadata["archive"] = {
            "name": package_preview.ARCHIVE_NAME,
            "sha256": package_preview.sha256_file(unsafe_archive),
            "size_bytes": unsafe_archive.stat().st_size,
        }
        unsafe_manifest = self.root / "unsafe-manifest.json"
        unsafe_manifest.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(package_preview.PreviewError, "unsafe archive entry"):
            package_preview.verify_preview(unsafe_archive, unsafe_manifest, SOURCE_COMMIT, RUN_ID, WINDOWS_TREE)

    def test_windows_drive_archive_entry_is_rejected(self) -> None:
        with self.assertRaisesRegex(package_preview.PreviewError, "unsafe archive entry"):
            package_preview._safe_archive_path("C:/escape.txt")


if __name__ == "__main__":
    unittest.main()
