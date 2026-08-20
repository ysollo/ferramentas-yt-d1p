"""Verificação segura de atualizações publicadas no GitHub."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Callable
from urllib.request import Request, urlopen


GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/ysollo/ferramentas-yt-d1p/releases/latest"
RELEASE_PAGE_URL = "https://github.com/ysollo/ferramentas-yt-d1p/releases/latest"


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    page_url: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_size: int | None = None
    asset_sha256: str | None = None


def normalize_version(value: str) -> tuple[int, ...]:
    """Converte tags simples como ``v0.1.1`` em uma tupla comparável."""

    match = re.search(r"(\d+(?:\.\d+){0,3})", value or "")
    if not match:
        raise ValueError(f"Versão inválida: {value!r}")
    parts = [int(part) for part in match.group(1).split(".")]
    return tuple((parts + [0, 0, 0])[:3])


def is_newer_version(current: str, candidate: str) -> bool:
    return normalize_version(candidate) > normalize_version(current)


def parse_release(payload: dict) -> ReleaseInfo:
    """Extrai apenas campos necessários de uma resposta de release."""

    if payload.get("draft") or payload.get("prerelease"):
        raise ValueError("A release não é estável.")
    version = str(payload.get("tag_name") or "").strip()
    page_url = str(payload.get("html_url") or RELEASE_PAGE_URL).strip()
    if not version:
        raise ValueError("A release não informa uma versão.")
    assets = payload.get("assets") or []
    preferred = next(
        (item for item in assets if str(item.get("name", "")).lower().endswith(".zip")),
        None,
    )
    if not preferred:
        return ReleaseInfo(version=version, page_url=page_url)
    size = preferred.get("size")
    return ReleaseInfo(
        version=version,
        page_url=page_url,
        asset_name=str(preferred.get("name") or "") or None,
        asset_url=str(preferred.get("browser_download_url") or "") or None,
        asset_size=int(size) if size is not None else None,
        asset_sha256=str(preferred.get("sha256") or "") or None,
    )


def fetch_latest_release(opener: Callable[..., object] = urlopen, timeout: float = 8.0) -> ReleaseInfo:
    """Consulta a release estável mais recente, sem autenticação."""

    request = Request(
        GITHUB_LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "YTD1P-Updater"},
    )
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_release(payload)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
