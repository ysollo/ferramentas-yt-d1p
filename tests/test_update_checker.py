import io
from pathlib import Path
import tempfile
import unittest

from src.update_checker import (
    checksum_from_manifest,
    cleanup_update_artifacts,
    is_newer_version,
    normalize_version,
    parse_release,
    fetch_latest_release,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class UpdateCheckerTests(unittest.TestCase):
    def test_cleanup_update_artifacts_removes_payloads_but_keeps_error_log(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            update_dir = Path(directory)
            (update_dir / "old.zip").write_bytes(b"zip")
            (update_dir / "v0.3.1.sha256").write_text("checksum", encoding="utf-8")
            (update_dir / "updater-error.log").write_text("details", encoding="utf-8")
            (update_dir / "updater").mkdir()
            (update_dir / "updater" / "helper.exe").write_bytes(b"helper")

            cleanup_update_artifacts(update_dir)

            self.assertFalse((update_dir / "old.zip").exists())
            self.assertFalse((update_dir / "v0.3.1.sha256").exists())
            self.assertFalse((update_dir / "updater").exists())
            self.assertTrue((update_dir / "updater-error.log").exists())

    def test_versions_are_compared_without_v_prefix(self):
        self.assertEqual(normalize_version("v0.1.1"), (0, 1, 1))
        self.assertTrue(is_newer_version("0.1.1", "v0.1.2"))
        self.assertFalse(is_newer_version("0.1.1", "v0.1.1"))
        self.assertFalse(is_newer_version("0.1.2", "v0.1.1"))

    def test_parse_release_ignores_prerelease_and_picks_zip(self):
        release = parse_release(
            {
                "tag_name": "v0.2.0",
                "html_url": "https://example.test/release",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {"name": "checksums.txt", "browser_download_url": "https://example.test/checksums"},
                    {
                        "name": "YTD1P-v0.2.0-windows-x64.zip",
                        "browser_download_url": "https://example.test/app.zip",
                        "size": 123,
                    },
                ],
            }
        )
        self.assertEqual(release.asset_name, "YTD1P-v0.2.0-windows-x64.zip")
        self.assertEqual(release.asset_size, 123)

    def test_fetch_latest_release_uses_public_json(self):
        payload = b'{"tag_name":"v0.1.1","html_url":"https://example.test/release","assets":[]}'
        release = fetch_latest_release(opener=lambda request, timeout: FakeResponse(payload))
        self.assertEqual(release.version, "v0.1.1")

    def test_checksum_manifest_selects_named_asset(self):
        self.assertEqual(
            checksum_from_manifest("abc\n" + "a" * 64 + " *app.zip\n", "app.zip"),
            "a" * 64,
        )


if __name__ == "__main__":
    unittest.main()
