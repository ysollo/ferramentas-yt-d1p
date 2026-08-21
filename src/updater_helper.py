"""Auxiliar separado para trocar uma instalação one-folder com rollback."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile


def _safe_extract(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        for member in members:
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError("ZIP de atualização contém caminho inválido.")
        bundle.extractall(destination)
    candidates = [item for item in destination.iterdir() if item.is_dir()]
    if len(candidates) == 1 and (candidates[0] / "YTD1P.exe").is_file():
        return candidates[0]
    if (destination / "YTD1P.exe").is_file():
        return destination
    raise ValueError("ZIP não contém uma distribuição YTD1P válida.")


def _rename_with_retry(source: Path, destination: Path, attempts: int = 60) -> None:
    """Tolera o lock transitório do Defender durante a verificação em nuvem."""

    last_error: OSError | None = None
    for _ in range(attempts):
        try:
            source.rename(destination)
            return
        except OSError as error:
            last_error = error
            time.sleep(0.5)
    raise last_error or OSError(f"Não foi possível renomear {source}.")


def _wait_for_process_exit(pid: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)


def install(archive: Path, install_dir: Path, pid: int, launch: bool = True) -> None:
    _wait_for_process_exit(pid)

    parent = install_dir.parent
    # A instalação pode estar numa pasta com permissões especiais. A extração
    # ocorre no TEMP e só a troca final toca no diretório do aplicativo.
    staging = Path(tempfile.gettempdir()) / f"YTD1P-update-{uuid.uuid4().hex}"
    # mkdir herda as permissões normais do TEMP; mkdtemp cria uma ACL restrita
    # que, neste Windows, impede a criação da subpasta _internal do ZIP.
    staging.mkdir()
    backup = parent / f"{install_dir.name}.backup"
    try:
        extracted = _safe_extract(archive, staging)
        if backup.exists():
            shutil.rmtree(backup)
        _rename_with_retry(install_dir, backup)
        _rename_with_retry(extracted, install_dir)
        if launch:
            os.startfile(str(install_dir / "YTD1P.exe"))
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if not install_dir.exists() and backup.exists():
            backup.rename(install_dir)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _relaunch_elevated() -> None:
    params = subprocess.list2cmdline(sys.argv[1:] + ["--elevated"])
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )
    if result <= 32:
        raise PermissionError("A atualização precisa da permissão do Windows para substituir a instalação.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.elevated and not _is_admin():
        _relaunch_elevated()
        return
    install(args.archive, args.install_dir, args.pid, launch=not args.no_launch)


if __name__ == "__main__":
    main()
