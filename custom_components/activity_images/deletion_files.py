"""No-follow owned-file snapshots and restartable, identity-checked cleanup."""
from __future__ import annotations

import ctypes
import hashlib
import os
import stat
from contextlib import suppress
from pathlib import Path

from .generated_image import MAX_IMAGE_BYTES


def validate_filename(filename: str) -> None:
    if (not filename or Path(filename).name != filename or filename.startswith(".")
            or "\\" in filename or "\0" in filename):
        raise ValueError("Invalid image filename")


def open_root(root: Path) -> int:
    """Refuse symlinks in every component, not just the final directory."""
    if not root.is_absolute():
        raise ValueError("Expected an absolute owned image directory")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in root.parts[1:]:
            next_directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        return directory
    except BaseException:
        os.close(directory)
        raise


def identity(directory: int, filename: str) -> list:
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
            raise ValueError("Invalid or oversized deletion asset")
        digest = hashlib.sha256(stream.read(MAX_IMAGE_BYTES + 1)).hexdigest()
        after = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Image changed while reading")
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, digest]


def group_files(root: Path, files: list[str], filename: str) -> list[str]:
    """Include existing conventional derivatives even when their image is corrupt."""
    directory = open_root(root)
    try:
        expected = set(files)
        if filename.lower().endswith(".png"):
            expected.update({filename[:-4] + "_thumb.webp", filename[:-4] + "_qa.webp"})
        elif filename.endswith(("_qa.webp", "_thumb.webp")):
            stem = filename.rsplit("_", 1)[0]
            expected.update({stem + "_thumb.webp", stem + "_qa.webp"})
        present = []
        for name in sorted(expected):
            validate_filename(name)
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            present.append(name)
        return present
    finally:
        os.close(directory)


def _rename_no_replace(source: str, target: str, directory: int, quarantine: int) -> None:
    # HA runs on Linux: fail closed if atomic no-replace rename is unavailable.
    function = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if function is None:
        raise OSError("Atomic no-replace quarantine rename is unavailable")
    result = function(ctypes.c_int(directory), ctypes.c_char_p(os.fsencode(source)),
                      ctypes.c_int(quarantine), ctypes.c_char_p(os.fsencode(target)), ctypes.c_uint(1))
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def snapshot(root: Path, files: list[str]) -> dict:
    directory = open_root(root)
    try:
        info = os.fstat(directory)
        identities = {}
        for filename in sorted(set(files)):
            validate_filename(filename)
            identities[filename] = identity(directory, filename)
        return {"root": [info.st_dev, info.st_ino], "files": identities}
    finally:
        os.close(directory)


def _open_quarantine(root: Path) -> int:
    """Create only the fixed private directory beneath HA's non-public storage."""
    if root.name != "activity_images_deletions" or root.parent.name != ".storage":
        raise ValueError("Invalid private deletion directory")
    config = open_root(root.parent.parent)
    storage = None
    try:
        info = os.fstat(config)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError("Unsafe Home Assistant configuration ownership or permissions")
        with suppress(FileExistsError):
            os.mkdir(".storage", 0o700, dir_fd=config)
        storage = os.open(".storage", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=config)
        info = os.fstat(storage)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError("Unsafe Home Assistant storage ownership or permissions")
        with suppress(FileExistsError):
            os.mkdir(root.name, 0o700, dir_fd=storage)
        quarantine = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=storage)
        info = os.fstat(quarantine)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(quarantine)
            raise ValueError("Unsafe deletion quarantine ownership or permissions")
        return quarantine
    finally:
        if storage is not None:
            os.close(storage)
        os.close(config)


def _restore(quarantine: int, name: str, directory: int, filename: str) -> None:
    # Never overwrite a newly published file, including a symlink or special file.
    with suppress(FileExistsError):
        _rename_no_replace(name, filename, quarantine, directory)


def cleanup(root: Path, quarantine_root: Path, expected: dict, operation_id: str, image_id: str) -> None:
    """Quarantine first: a raced-in unrelated inode is never unlinked."""
    directory = open_root(root)
    quarantine = None
    try:
        info = os.fstat(directory)
        if [info.st_dev, info.st_ino] != expected["root"]:
            raise ValueError("Owned image directory changed; cleanup stopped")
        quarantine = _open_quarantine(quarantine_root)
        if os.fstat(quarantine).st_dev != info.st_dev:
            raise OSError("Deletion quarantine must be on the same filesystem")
        for filename, fingerprint in expected["files"].items():
            validate_filename(filename)
            name = hashlib.sha256(f"{operation_id}:{image_id}:{filename}".encode()).hexdigest()
            try:
                quarantined = identity(quarantine, name)
            except FileNotFoundError:
                try:
                    current = identity(directory, filename)
                except FileNotFoundError:
                    continue  # Already cleaned, including a crash before journal update.
                if current != fingerprint:
                    raise ValueError("Image identity changed; unrelated file retained") from None
                _rename_no_replace(filename, name, directory, quarantine)
                try:
                    quarantined = identity(quarantine, name)
                except (OSError, ValueError):
                    _restore(quarantine, name, directory, filename)
                    raise
            except (OSError, ValueError):
                _restore(quarantine, name, directory, filename)
                raise
            # A rename can change ctime. All other identity fields must match.
            if quarantined[:4] + quarantined[5:] != fingerprint[:4] + fingerprint[5:]:
                _restore(quarantine, name, directory, filename)
                raise ValueError("Image raced during quarantine; unrelated file retained")
            os.unlink(name, dir_fd=quarantine)
    finally:
        if quarantine is not None:
            os.close(quarantine)
        os.close(directory)
