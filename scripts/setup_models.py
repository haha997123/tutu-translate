"""Prepare the two pinned offline models without importing or executing them.

No arguments prints help. Network access happens only with --download.
The model files and archives are checked against models/provenance.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile


DEFAULT_MODELS = Path(__file__).resolve().parents[1] / "models"


class ModelSetupError(RuntimeError):
    pass


def safe_path(root: Path, relative: str) -> Path:
    """Reject ambiguous Windows names and paths outside the requested root."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ModelSetupError(f"Unsafe model path: {relative!r}")
    parts = PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or any(p in ("", ".", "..") for p in relative.split("/")):
        raise ModelSetupError(f"Unsafe model path: {relative!r}")
    if any(p.endswith((".", " ")) for p in parts):
        raise ModelSetupError(f"Ambiguous Windows model path: {relative!r}")
    if any(re.search(r'[<>|"*?\x00-\x1f]', p) or
           re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.I)
           for p in parts):
        raise ModelSetupError(f"Unsafe Windows model path: {relative!r}")
    root = Path(root).resolve()
    path = root.joinpath(*parts)
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ModelSetupError(f"Model path leaves its directory: {relative!r}") from exc
    if path.is_symlink():
        raise ModelSetupError(f"Refusing symbolic-link model file: {relative!r}")
    return path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def check_file(path: Path, expected: dict) -> None:
    if not path.is_file():
        raise ModelSetupError(f"Missing model file: {expected.get('path', path.name)}")
    if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
        raise ModelSetupError(f"Size or SHA-256 mismatch: {expected.get('path', path.name)}")


def read_manifest(models_root: Path) -> list[dict]:
    try:
        models = json.loads((Path(models_root) / "provenance.json").read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ModelSetupError("Cannot read models/provenance.json") from exc
    if not isinstance(models, list) or not models:
        raise ModelSetupError("Model manifest is empty")
    paths = set()
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("installed"), list) or not model["installed"]:
            raise ModelSetupError("Invalid model manifest")
        direction = model.get("direction")
        if not isinstance(direction, str) or not re.fullmatch(r"[a-z]{2}_[a-z]{2}", direction):
            raise ModelSetupError("Invalid model direction")
        for entry in model["installed"]:
            if not isinstance(entry, dict):
                raise ModelSetupError("Invalid model file metadata")
            relative = entry.get("path")
            safe_path(models_root, relative)
            if PurePosixPath(relative).parts[0] != direction:
                raise ModelSetupError("Model file is outside its direction directory")
            if relative.casefold() in paths:
                raise ModelSetupError(f"Duplicate model path: {relative}")
            paths.add(relative.casefold())
            if (type(entry.get("bytes")) is not int or entry["bytes"] < 0 or
                    not isinstance(entry.get("sha256"), str) or
                    not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                raise ModelSetupError(f"Invalid file checksum metadata: {relative}")
    return models


def verify_models(models_root: Path = DEFAULT_MODELS) -> int:
    models_root = Path(models_root)
    entries = [entry for model in read_manifest(models_root) for entry in model["installed"]]
    for entry in entries:
        check_file(safe_path(models_root, entry["path"]), entry)
    return len(entries)


def missing_entries(models_root: Path, models: list[dict]) -> list[dict]:
    """Check all existing destinations before any copy or download occurs."""
    missing = []
    for model in models:
        for entry in model["installed"]:
            target = safe_path(models_root, entry["path"])
            if target.exists():
                check_file(target, entry)
            else:
                missing.append(entry)
    return missing


def publish_missing(staged: Path, models_root: Path, entries: list[dict]) -> None:
    """Exclusive creation never overwrites files that appeared during setup."""
    created = []
    try:
        for entry in entries:
            source = safe_path(staged, entry["path"])
            check_file(source, entry)
            target = safe_path(models_root, entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            # Check again after directory creation, including any path links.
            target = safe_path(models_root, entry["path"])
            try:
                output = target.open("xb")
            except FileExistsError:
                check_file(target, entry)
                continue
            created.append(target)
            with output, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, 1024 * 1024)
            check_file(target, entry)
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise


def copy_from_install(source: Path, models_root: Path = DEFAULT_MODELS) -> int:
    source, models_root = Path(source), Path(models_root)
    if (source / "models").is_dir():
        source = source / "models"
    models = read_manifest(models_root)
    missing = missing_entries(models_root, models)
    # Validate even source files whose destinations already exist.
    for model in models:
        for entry in model["installed"]:
            check_file(safe_path(source, entry["path"]), entry)
    with tempfile.TemporaryDirectory(prefix="tutu-model-setup-") as temp:
        staged = Path(temp)
        for entry in missing:
            target = safe_path(staged, entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(source, entry["path"]), target)
            check_file(target, entry)
        publish_missing(staged, models_root, missing)
    verify_models(models_root)
    return len(missing)


def extract_verified_archive(archive: Path, model: dict, staged: Path) -> None:
    """Extract only whitelist members, with one safe package prefix."""
    check_file(archive, {"bytes": model["archive_bytes"], "sha256": model["archive_sha256"]})
    entries = model["installed"]
    by_suffix = {"/".join(PurePosixPath(e["path"]).parts[1:]): e for e in entries}
    matched = {}
    seen_members = set()
    package_prefix = None
    with zipfile.ZipFile(archive) as package:
        # Validate the entire central directory before writing any output.
        for member in package.infolist():
            # ZipInfo normalizes Windows backslashes and truncates NULs in
            # filename; validate its original central-directory spelling too.
            original_name = member.orig_filename.rstrip("/")
            if original_name:
                safe_path(staged, original_name)
            name = member.filename.rstrip("/")
            if not name:
                continue
            safe_path(staged, name)
            if name.casefold() in seen_members:
                raise ModelSetupError(f"Duplicate model archive member: {name}")
            seen_members.add(name.casefold())
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ModelSetupError("Refusing symbolic link in model archive")
            if member.is_dir():
                continue
            parts = PurePosixPath(name).parts
            suffix = "/".join(parts[1:])
            if len(parts) < 2 or suffix not in by_suffix:
                continue
            if package_prefix is None:
                package_prefix = parts[0]
            if package_prefix != parts[0] or suffix in matched:
                raise ModelSetupError(f"Ambiguous or duplicate model archive member: {name}")
            entry = by_suffix[suffix]
            if member.file_size != entry["bytes"] or member.flag_bits & 1:
                raise ModelSetupError(f"Invalid model archive member: {name}")
            matched[suffix] = member
        if set(matched) != set(by_suffix):
            raise ModelSetupError("Model archive is missing required whitelist members")
        for suffix, member in matched.items():
            entry = by_suffix[suffix]
            target = safe_path(staged, entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as input_stream, target.open("xb") as output:
                remaining = entry["bytes"]
                while remaining:
                    chunk = input_stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ModelSetupError("Unexpected end of model archive member")
                    output.write(chunk)
                    remaining -= len(chunk)
                if input_stream.read(1):
                    raise ModelSetupError("Oversized model archive member")
            check_file(target, entry)


def fetch_archive(model: dict, destination: Path) -> None:
    expected = {"bytes": model["archive_bytes"], "sha256": model["archive_sha256"]}
    errors = []
    urls = list(dict.fromkeys(model.get(key) for key in ("official_url", "legacy_official_url") if model.get(key)))
    for url in urls:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ModelSetupError("Only HTTPS model sources are allowed")
        try:
            request = Request(url, headers={"User-Agent": "TutuTranslate-model-setup/1.0"})
            with urlopen(request, timeout=60) as response, destination.open("wb") as output:
                if not response.geturl().startswith("https://"):
                    raise ModelSetupError("Model download redirected to an insecure URL")
                received = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected["bytes"]:
                        raise ModelSetupError("Model download is larger than the pinned archive")
                    output.write(chunk)
            check_file(destination, expected)
            return
        except (OSError, ValueError, ModelSetupError) as exc:
            errors.append(type(exc).__name__)
            destination.unlink(missing_ok=True)
    raise ModelSetupError("Pinned model download failed; no model files installed (" + ", ".join(errors) + ")")


def download_models(models_root: Path = DEFAULT_MODELS) -> int:
    models_root = Path(models_root)
    models = read_manifest(models_root)
    missing = missing_entries(models_root, models)
    if not missing:
        return 0
    missing_paths = {entry["path"] for entry in missing}
    with tempfile.TemporaryDirectory(prefix="tutu-model-setup-") as temp:
        staged = Path(temp) / "files"
        for model in models:
            if not any(entry["path"] in missing_paths for entry in model["installed"]):
                continue
            archive = Path(temp) / (model["direction"] + ".argosmodel")
            print(f"Downloading pinned {model['direction']} 1.9 model...", flush=True)
            fetch_archive(model, archive)
            extract_verified_archive(archive, model, staged)
        publish_missing(staged, models_root, missing)
    verify_models(models_root)
    return len(missing)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify", action="store_true", help="Check every installed file; never download or modify files")
    action.add_argument("--from-install", type=Path, metavar="DIRECTORY", help="Copy missing files from a complete app folder or its models folder")
    action.add_argument("--download", action="store_true", help="Explicitly download and verify pinned official model archives")
    args = parser.parse_args(argv)
    if not (args.verify or args.from_install is not None or args.download):
        parser.print_help()
        return 0
    try:
        if args.verify:
            print(f"Verified {verify_models()} model files.")
        elif args.from_install is not None:
            print(f"Copied {copy_from_install(args.from_install)} missing files; all models verified.")
        else:
            print(f"Installed {download_models()} missing files; all models verified.")
    except (ModelSetupError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Model setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
