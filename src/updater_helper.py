"""Auxiliar separado para trocar uma instalação one-folder com rollback."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
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


def install(archive: Path, install_dir: Path, pid: int, launch: bool = True) -> None:
    for _ in range(120):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.25)

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
        install_dir.rename(backup)
        extracted.rename(install_dir)
        if launch:
            os.startfile(str(install_dir / "YTD1P.exe"))
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if not install_dir.exists() and backup.exists():
            backup.rename(install_dir)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()
    install(args.archive, args.install_dir, args.pid, launch=not args.no_launch)


if __name__ == "__main__":
    main()
