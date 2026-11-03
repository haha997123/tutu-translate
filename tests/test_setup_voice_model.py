import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import setup_voice_model as setup


class VoiceModelSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "models/asr/sensevoice"
        self.destination.mkdir(parents=True)
        self.data = {"model.int8.onnx": b"inert model fixture", "tokens.txt": b"fixture tokens"}
        self.manifest = {"archive_prefix": "official-export", "installed": [
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in self.data.items()]}

    def archive(self, unsafe=None, link=False):
        path = self.root / "fixture.tar.bz2"
        with tarfile.open(path, "w:bz2") as output:
            for name, data in self.data.items():
                entry = tarfile.TarInfo("official-export/" + name)
                entry.size = len(data)
                output.addfile(entry, io.BytesIO(data))
            if unsafe:
                entry = tarfile.TarInfo(unsafe)
                if link:
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = "../../outside"
                output.addfile(entry, io.BytesIO())
        self.manifest.update(archive_bytes=path.stat().st_size,
                             archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.write_manifest()
        return path

    def write_manifest(self):
        (self.destination / "provenance.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_install_hash_verify_and_repeat_preserves_files(self):
        path = self.archive()
        self.assertEqual(setup.install_archive(path, self.destination), 2)
        self.assertEqual(setup.verify(self.destination), 2)
        before = (self.destination / "model.int8.onnx").stat().st_mtime_ns
        self.assertEqual(setup.install_archive(path, self.destination), 0)
        self.assertEqual(before, (self.destination / "model.int8.onnx").stat().st_mtime_ns)

    def test_corrupt_archive_rejected_before_copy(self):
        path = self.archive()
        with path.open("ab") as stream:
            stream.write(b"corrupt")
        with self.assertRaises(setup.ModelSetupError):
            setup.install_archive(path, self.destination)
        self.assertFalse((self.destination / "model.int8.onnx").exists())

    def test_traversal_and_links_are_rejected_even_when_not_selected(self):
        for unsafe, link in [("../escape", False), ("official-export/link", True),
                             ("C:/absolute", False), ("official-export/CON", False)]:
            with self.subTest(unsafe=unsafe):
                path = self.archive(unsafe, link)
                with self.assertRaises(setup.ModelSetupError):
                    setup.install_archive(path, self.destination)
                self.assertFalse((self.destination / "model.int8.onnx").exists())

    def test_mismatched_existing_file_preserved_without_partial_copy(self):
        path = self.archive()
        (self.destination / "tokens.txt").write_bytes(b"user file")
        with self.assertRaises(setup.ModelSetupError):
            setup.install_archive(path, self.destination)
        self.assertEqual((self.destination / "tokens.txt").read_bytes(), b"user file")
        self.assertFalse((self.destination / "model.int8.onnx").exists())

    def test_no_arguments_never_downloads(self):
        with patch.object(setup, "urlopen", side_effect=AssertionError("network forbidden")), patch("builtins.print"):
            self.assertEqual(setup.main([]), 0)

    def test_copy_from_portable_install_validates_all_files_first(self):
        self.write_manifest()
        source = self.root / "portable/models/asr/sensevoice"
        source.mkdir(parents=True)
        for name, data in self.data.items():
            (source / name).write_bytes(data)
        with patch.object(setup, "urlopen", side_effect=AssertionError("network forbidden")):
            self.assertEqual(setup.main(["--model-dir", str(self.destination), "--from-install", str(self.root / "portable")]), 0)
        self.assertEqual(setup.verify(self.destination), 2)


if __name__ == "__main__":
    unittest.main()
