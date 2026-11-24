"""Распаковка загруженных файлов: .dem, .dem.zst, .dem.bz2, .zip (с .dem внутри).

Распаковка потоковая, с ограничением размера результата (защита от «архивных бомб»).
Для .zst используется первый доступный вариант:
  1) стандартный модуль compression.zstd (Python 3.14+)
  2) пакет zstandard  (pip install zstandard)
  3) консольная утилита zstd
"""
from __future__ import annotations

import bz2
import contextlib
import shutil
import subprocess
import zipfile

MAX_UNPACKED = 2 * 1024**3  # 2 ГБ после распаковки
CHUNK = 1 << 20


class UnpackError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def detect_kind(filename: str):
    """'dem' | 'zst' | 'bz2' | 'zip' | None по имени файла."""
    n = (filename or "").lower()
    if n.endswith(".dem"):
        return "dem"
    if n.endswith(".zst"):
        return "zst"
    if n.endswith(".bz2"):
        return "bz2"
    if n.endswith(".zip"):
        return "zip"
    return None


def _copy_capped(fin, dst: str) -> None:
    total = 0
    with open(dst, "wb") as fout:
        while True:
            chunk = fin.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UNPACKED:
                raise UnpackError("Распакованный файл слишком большой (лимит 2 ГБ)")
            fout.write(chunk)
    if total == 0:
        raise UnpackError("Архив пустой")


@contextlib.contextmanager
def _open_zst(path: str):
    try:
        from compression import zstd  # Python 3.14+
    except ImportError:
        zstd = None
    if zstd is not None:
        with zstd.open(path, "rb") as f:
            yield f
        return

    try:
        import zstandard
    except ImportError:
        zstandard = None
    if zstandard is not None:
        with open(path, "rb") as raw:
            with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
                yield reader
        return

    exe = shutil.which("zstd")
    if exe:
        proc = subprocess.Popen([exe, "-dc", "--", path],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            yield proc.stdout
            if proc.wait() != 0:
                raise UnpackError("Повреждённый или неподдерживаемый .zst файл")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
        return

    raise UnpackError("Для файлов .zst на сервере нужен пакет zstandard: pip install zstandard", 503)


def unpack(src: str, dst: str, kind: str) -> None:
    """Распаковывает src в dst (.dem). Бросает UnpackError с понятным текстом."""
    try:
        if kind == "bz2":
            with bz2.open(src, "rb") as f:
                _copy_capped(f, dst)
        elif kind == "zst":
            with _open_zst(src) as f:
                _copy_capped(f, dst)
        elif kind == "zip":
            with zipfile.ZipFile(src) as z:
                members = [i for i in z.infolist()
                           if not i.is_dir() and i.filename.lower().endswith(".dem")]
                if not members:
                    raise UnpackError("В архиве нет файла .dem")
                if members[0].file_size > MAX_UNPACKED:
                    raise UnpackError("Распакованный файл слишком большой (лимит 2 ГБ)")
                with z.open(members[0]) as f:
                    _copy_capped(f, dst)
        else:
            raise UnpackError("Неподдерживаемый формат архива")
    except UnpackError:
        raise
    except Exception as e:
        raise UnpackError(f"Не удалось распаковать файл: {e}")
