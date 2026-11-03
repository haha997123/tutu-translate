"""Explicitly install the pinned SenseVoice-Small export; never runs the model.

No arguments only prints help. Network access requires --download. Files are
checked against the committed provenance, including before replacing anything.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from urllib.request import Request, urlopen

try:
    from setup_models import ModelSetupError, safe_path, check_file
except ModuleNotFoundError:
    from scripts.setup_models import ModelSetupError, safe_path, check_file


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "models/asr/sensevoice"


def manifest(root: Path) -> dict:
    try:
        data = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
        files = data["installed"]
        if not files or not isinstance(files, list):
            raise ValueError("empty manifest")
        seen = set()
        for item in files:
            relative = item["path"]
            safe_path(root, relative)
            if relative.casefold() in seen:
                raise ValueError("duplicate file")
            seen.add(relative.casefold())
            if type(item["bytes"]) is not int or item["bytes"] < 0:
                raise ValueError("invalid size")
            if len(item["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in item["sha256"]):
                raise ValueError("invalid hash")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ModelSetupError("Cannot read SenseVoice model provenance") from exc
    return data


def verify(root: Path) -> int:
    files = manifest(root)["installed"]
    for item in files:
        check_file(safe_path(root, item["path"]), item)
    return len(files)


def install_archive(archive: Path, root: Path) -> int:
    data = manifest(root)
    check_file(archive, {"bytes": data["archive_bytes"], "sha256": data["archive_sha256"]})
    # Validate everything before copying. Never let tarfile choose output paths.
    with tempfile.TemporaryDirectory(prefix="tutu-sensevoice-") as temporary:
        staging = Path(temporary)
        expected = {data["archive_prefix"] + "/" + f["path"]: f for f in data["installed"]}
        found = set()
        with tarfile.open(archive, "r:bz2") as source:
            for member in source:
                safe_path(staging, member.name.rstrip("/"))
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ModelSetupError("Unsupported archive entry")
                if member.name not in expected:
                    continue
                if member.name in found or not member.isfile():
                    raise ModelSetupError("Duplicate or invalid model archive entry")
                found.add(member.name)
                item = expected[member.name]
                if member.size != item["bytes"]:
                    raise ModelSetupError("Unexpected model archive file size")
                destination = safe_path(staging, item["path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, destination.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                check_file(destination, item)
        if found != set(expected):
            raise ModelSetupError("Missing model archive files")
        return copy_files(staging, root, data["installed"])


def copy_files(source: Path, root: Path, files: list[dict]) -> int:
    missing = []
    for item in files:
        check_file(safe_path(source, item["path"]), item)
        target = safe_path(root, item["path"])
        if target.exists():
            check_file(target, item)
        else:
            missing.append(item)
    for item in missing:
        target = safe_path(root, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_path(source, item["path"]), target)
    return len(missing)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify", action="store_true")
    action.add_argument("--download", action="store_true")
    action.add_argument("--archive", type=Path, help="Use an already downloaded official archive")
    action.add_argument("--from-install", type=Path, help="Copy from a complete portable app directory")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    if not any((args.verify, args.download, args.archive, args.from_install)):
        parser.print_help()
        return 0
    root = args.model_dir.resolve()
    try:
        data = manifest(root)
        if args.verify:
            print(f"Verified {verify(root)} SenseVoice files.")
        elif args.from_install:
            source = args.from_install / "models/asr/sensevoice"
            print(f"Copied {copy_files(source, root, data['installed'])} files.")
        elif args.archive:
            print(f"Installed {install_archive(args.archive, root)} files.")
        else:
            with tempfile.TemporaryDirectory(prefix="tutu-sensevoice-download-") as temporary:
                archive = Path(temporary) / "model.tar.bz2"
                request = Request(data["official_url"], headers={"User-Agent": "TutuTranslate-model-setup"})
                with urlopen(request, timeout=45) as stream, archive.open("wb") as output:
                    remaining = data["archive_bytes"]
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ModelSetupError("Incomplete model download")
                        output.write(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        raise ModelSetupError("Model download exceeds the pinned size")
                print(f"Installed {install_archive(archive, root)} files.")
    except (ModelSetupError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"Voice model setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
