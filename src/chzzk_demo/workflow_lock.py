"""Keep independently launched Studio workers from writing one job at once."""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def studio_job_lock(job: str | Path | None) -> Iterator[None]:
    """Lock only Studio CLI workers; a process exit also releases the OS lock."""
    if os.environ.get("AUTOSHORTS_STUDIO_WORKER") != "1" or job is None:
        yield
        return

    folder = Path(job).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    # Never unlink this file: another worker could otherwise lock a new inode
    # while the original file is still locked by a surviving worker.
    with (folder / ".studio-stage.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or getattr(error, "winerror", None) == 33:
                raise RuntimeError(
                    f"이 작업 폴더에서 이전 작업이 아직 실행 중입니다. 종료된 뒤 다시 시도하세요: {folder}"
                ) from error
            raise
        try:
            # A one-byte region is enough on Windows, including an empty file.
            # Initialize it only after acquiring the lock to avoid racing writes.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
