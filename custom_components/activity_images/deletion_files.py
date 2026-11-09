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


def cleanup(root: Path, expected: dict, operation_id: str, image_id: str) -> None:
    """Quarantine first: a raced-in unrelated inode is never unlinked."""
    directory = open_root(root)
    quarantine = None
    try:
        info = os.fstat(directory)
        if [info.st_dev, info.st_ino] != expected["root"]:
            raise ValueError("Owned image directory changed; cleanup stopped")
        with suppress(FileExistsError):
            os.mkdir(".deletions", 0o700, dir_fd=directory)
        quarantine = os.open(".deletions", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        qinfo = os.fstat(quarantine)
        if qinfo.st_uid != os.geteuid() or stat.S_IMODE(qinfo.st_mode) != 0o700:
            raise ValueError("Unsafe deletion quarantine permissions")
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
                quarantined = identity(quarantine, name)
            # A rename can change ctime. All other identity fields must match.
            if quarantined[:4] + quarantined[5:] != fingerprint[:4] + fingerprint[5:]:
                # Restore without replacing anything created at the published name.
                with suppress(FileExistsError):
                    os.link(name, filename, src_dir_fd=quarantine, dst_dir_fd=directory, follow_symlinks=False)
                raise ValueError("Image raced during quarantine; unrelated file retained")
            os.unlink(name, dir_fd=quarantine)
    finally:
        if quarantine is not None:
            os.close(quarantine)
        os.close(directory)
