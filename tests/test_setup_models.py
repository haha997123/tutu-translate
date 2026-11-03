import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


spec = importlib.util.spec_from_file_location("setup_models", Path(__file__).resolve().parents[1] / "scripts/setup_models.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ModelSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.models = self.root / "destination/models"
        self.models.mkdir(parents=True)
        self.source = self.root / "installed/models"
        self.files = {"en_zh/metadata.json": b'{"version":"1.9"}',
                      "en_zh/README.md": b"CC BY 4.0 attribution",
                      "en_zh/model/model.bin": b"fixed inert model bytes",
                      "en_zh/sentencepiece.model": b"fixed tokenizer bytes"}
        self.model = {"direction": "en_zh", "installed": [
            {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in self.files.items()],
            "official_url": "https://example.invalid/official", "legacy_official_url": "https://example.invalid/legacy"}
        for path, data in self.files.items():
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self):
        (self.models / "provenance.json").write_text(json.dumps({"models": [self.model]}), encoding="utf-8")

    def archive(self, extra=None, duplicate=False):
        archive = self.root / "fixture.argosmodel"
        with zipfile.ZipFile(archive, "w") as output:
            for path, data in self.files.items():
                output.writestr("translate-en_zh-1_9/" + path.split("/", 1)[1], data)
            output.writestr("translate-en_zh-1_9/stanza/unused.bin", b"not selected")
            if extra:
                # ZipInfo's constructor normalizes backslashes on Windows;
                # assign the raw archive name to exercise malicious ZIPs.
                info = zipfile.ZipInfo()
                info.filename = extra
                output.writestr(info, b"unsafe")
            if duplicate:
                output.writestr("another-package/model/model.bin", self.files["en_zh/model/model.bin"])
        self.model["archive_bytes"] = archive.stat().st_size
        self.model["archive_sha256"] = setup.digest(archive)
        self.write_manifest()
        return archive

    def test_no_arguments_never_touches_files_or_network(self):
        before = list(self.models.rglob("*"))
        with patch.object(setup, "urlopen", side_effect=AssertionError("network forbidden")), patch("builtins.print"):
            self.assertEqual(setup.main([]), 0)
        self.assertEqual(list(self.models.rglob("*")), before)

    def test_copy_from_install_or_models_then_verify_and_no_overwrite(self):
        with patch.object(setup, "urlopen", side_effect=AssertionError("network forbidden")):
            self.assertEqual(setup.copy_from_install(self.source.parent, self.models), len(self.files))
            before = {p: (self.models / p).stat().st_mtime_ns for p in self.files}
            self.assertEqual(setup.copy_from_install(self.source, self.models), 0)
            self.assertEqual(setup.verify_models(self.models), len(self.files))
            self.assertEqual(before, {p: (self.models / p).stat().st_mtime_ns for p in self.files})

    def test_mismatched_existing_destination_is_preserved_before_copy(self):
        target = self.models / "en_zh/README.md"
        target.parent.mkdir()
        target.write_bytes(b"user modified contents")
        with self.assertRaises(setup.ModelSetupError):
            setup.copy_from_install(self.source, self.models)
        self.assertEqual(target.read_bytes(), b"user modified contents")
        self.assertFalse((self.models / "en_zh/model/model.bin").exists())

    def test_mismatched_source_is_rejected_without_partial_install(self):
        (self.source / "en_zh/README.md").write_bytes(b"bad attribution")
        with self.assertRaises(setup.ModelSetupError):
            setup.copy_from_install(self.source, self.models)
        self.assertEqual([p.name for p in self.models.iterdir()], ["provenance.json"])

    def test_verify_detects_missing_and_altered_files(self):
        with self.assertRaises(setup.ModelSetupError):
            setup.verify_models(self.models)
        setup.copy_from_install(self.source, self.models)
        (self.models / "en_zh/model/model.bin").write_bytes(b"x" * len(self.files["en_zh/model/model.bin"]))
        with self.assertRaises(setup.ModelSetupError):
            setup.verify_models(self.models)

    def test_download_verified_whitelist_only_without_real_network(self):
        archive = self.archive()
        def fake_fetch(model, target):
            target.write_bytes(archive.read_bytes())
        with patch.object(setup, "fetch_archive", side_effect=fake_fetch):
            self.assertEqual(setup.download_models(self.models), len(self.files))
        self.assertEqual(setup.verify_models(self.models), len(self.files))
        self.assertFalse((self.models / "en_zh/stanza").exists())

    def test_archive_hash_mismatch_and_member_hash_mismatch(self):
        archive = self.archive()
        original = self.model["archive_sha256"]
        self.model["archive_sha256"] = "0" * 64
        with self.assertRaises(setup.ModelSetupError):
            setup.extract_verified_archive(archive, self.model, self.root / "staged")
        self.model["archive_sha256"] = original
        self.model["installed"][0]["sha256"] = "0" * 64
        with self.assertRaises(setup.ModelSetupError):
            setup.extract_verified_archive(archive, self.model, self.root / "staged")

    def test_zip_traversal_absolute_and_ambiguous_members_rejected(self):
        for name in ("../escape.bin", "/escape.bin", "C:/escape.bin", "prefix\\escape.bin"):
            with self.subTest(name=name):
                archive = self.archive(extra=name)
                with self.assertRaises(setup.ModelSetupError):
                    setup.extract_verified_archive(archive, self.model, self.root / "staged")
        self.assertFalse((self.root / "escape.bin").exists())

    def test_multiple_package_roots_cannot_supply_same_required_file(self):
        archive = self.archive(duplicate=True)
        with self.assertRaises(setup.ModelSetupError):
            setup.extract_verified_archive(archive, self.model, self.root / "staged")
        self.assertFalse((self.root / "staged/en_zh").exists())

    def test_manifest_duplicate_and_traversal_rejected(self):
        self.model["installed"].append(dict(self.model["installed"][0]))
        self.write_manifest()
        with self.assertRaises(setup.ModelSetupError):
            setup.read_manifest(self.models)
        self.model["installed"].pop()
        self.model["installed"][0]["path"] = "../escape.bin"
        self.write_manifest()
        with self.assertRaises(setup.ModelSetupError):
            setup.read_manifest(self.models)

    def test_failed_download_does_not_install_any_files(self):
        self.archive()
        with patch.object(setup, "fetch_archive", side_effect=setup.ModelSetupError("offline")):
            with self.assertRaises(setup.ModelSetupError):
                setup.download_models(self.models)
        self.assertEqual([p.name for p in self.models.iterdir()], ["provenance.json"])

    def test_fetch_uses_backup_after_network_error_and_checks_archive(self):
        import io
        from urllib.error import URLError
        archive = self.archive()
        class Response(io.BytesIO):
            def geturl(self):
                return "https://example.invalid/legacy"
        target = self.root / "downloaded.argosmodel"
        with patch.object(setup, "urlopen", side_effect=[URLError("unavailable"), Response(archive.read_bytes())]) as fetch:
            setup.fetch_archive(self.model, target)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(setup.digest(target), self.model["archive_sha256"])

    def test_fetch_rejects_hash_mismatch_and_oversized_response(self):
        import io
        archive = self.archive()
        class Response(io.BytesIO):
            def geturl(self):
                return "https://example.invalid/official"
        target = self.root / "downloaded.argosmodel"
        wrong = b"x" * archive.stat().st_size
        for payload in (wrong, archive.read_bytes() + b"extra"):
            with self.subTest(oversized=len(payload) > archive.stat().st_size):
                with patch.object(setup, "urlopen", side_effect=lambda *a, **k: Response(payload)):
                    with self.assertRaises(setup.ModelSetupError):
                        setup.fetch_archive(self.model, target)
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
