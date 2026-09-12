import os
import re
import stat
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO, Protocol

from naos_api.errors import ImageError, NotFoundError
from naos_api.settings import Settings

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class ImageStore(Protocol):
    def exists(self, digest: str) -> bool: ...

    def open_read(self, digest: str) -> tuple[BinaryIO, int]: ...

    def write_atomic(self, digest: str, chunks: Iterable[bytes]) -> None: ...

    def delete(self, digest: str) -> None: ...


class FsImageStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, digest: str) -> Path:
        if not _DIGEST.fullmatch(digest):
            raise ImageError(f"{digest!r} is not a sha256 digest")
        return self.root / f"sha256-{digest.removeprefix('sha256:')}.qcow2"

    def _prepare_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ImageError(
                f"image store {self.root} must be a directory not writable by group or others"
            )

    def exists(self, digest: str) -> bool:
        try:
            return stat.S_ISREG(self._path(digest).lstat().st_mode)
        except FileNotFoundError:
            return False

    def open_read(self, digest: str) -> tuple[BinaryIO, int]:
        path = self._path(digest)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as err:
            raise NotFoundError(f"image {digest} is not in the store") from err
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise NotFoundError(f"image {digest} is not in the store")
        return os.fdopen(fd, "rb"), info.st_size

    def write_atomic(self, digest: str, chunks: Iterable[bytes]) -> None:
        target = self._path(digest)
        self._prepare_root()
        fd, temp = tempfile.mkstemp(dir=self.root, prefix=".import-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in chunks:
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temp, 0o444)
            os.replace(temp, target)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temp)
            raise

    def delete(self, digest: str) -> None:
        with suppress(FileNotFoundError):
            self._path(digest).unlink()


def make_store(settings: Settings) -> ImageStore:
    return FsImageStore(Path(settings.image_store_path).expanduser())
