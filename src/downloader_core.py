"""Motor local do YTD1P Downloader.

Esta camada não conhece a interface gráfica. Ela traduz opções amigáveis em
configuração do yt-dlp, emite progresso e permite cancelamento cooperativo.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
from threading import Event
from typing import Callable, Literal

import yt_dlp


VideoLimit = Literal["auto", "144", "240", "360", "480", "720", "1080", "1440", "2160"]
AudioFormat = Literal["mp3", "m4a", "opus", "flac", "wav", "aac", "alac", "vorbis"]
PotProvider = Literal["none", "wpc"]
MAX_URL_LENGTH = 2048
PLAYLIST_CHECKPOINT_FILENAME = ".ytd1p-playlist-checkpoint.json"


@dataclass(frozen=True)
class DownloadOptions:
    """Opções já validadas pela interface."""

    url: str
    output_dir: Path
    mode: Literal["video", "audio"] = "video"
    video_limit: VideoLimit = "auto"
    audio_format: AudioFormat = "mp3"
    audio_quality: str = "5"
    overwrite: bool = False
    use_browser_session: bool = False
    browser: str | None = None
    pot_provider: PotProvider = "none"
    youtube_player_client: str | None = None
    wpc_browser_path: str | None = None


@dataclass(frozen=True)
class Progress:
    status: str
    percent: float | None = None
    speed: str | None = None
    eta: str | None = None
    filename: str | None = None
    message: str | None = None


ProgressCallback = Callable[[Progress], None]
LogCallback = Callable[[str], None]


class _YtdlpLogger:
    def __init__(self, callback: LogCallback):
        self.callback = callback

    def _send(self, message: str):
        clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", message).strip()
        if clean:
            self.callback(clean)

    def debug(self, message: str):
        self._send(message)

    def warning(self, message: str):
        self._send(message)

    def error(self, message: str):
        self._send(message)


def video_format_selector(limit: VideoLimit) -> str:
    """Retorna vídeo+áudio sem excluir containers antes da descoberta.

    O yt-dlp conhece os formatos disponíveis somente depois de extrair a URL.
    Por isso, filtros como ``[ext=mp4]`` são deixados de fora: alguns vídeos
    oferecem somente WebM, HLS ou combinações de codecs diferentes.
    """

    if limit == "auto":
        return "bestvideo*+(bestaudio[acodec!=opus]/bestaudio)"

    # A ordem preserva a intenção "até H": primeiro tenta vídeo separado com
    # altura/largura dentro do limite, depois um formato já combinado. Os
    # fallbacks finais evitam falhar quando o site só expõe uma representação.
    audio = "(bestaudio[acodec!=opus]/bestaudio)"
    return (
        f"bestvideo*[height={limit}]+{audio}/"
        f"bestvideo*[width={limit}]+{audio}/"
        f"bestvideo*[height<={limit}]+{audio}/"
        f"bestvideo*[width<={limit}]+{audio}/"
        f"bestvideo*+{audio}/best"
    )


def audio_format_selector() -> str:
    """Seleciona a melhor fonte que contenha apenas áudio."""

    return "ba/b"


def _progress_hook(callback: ProgressCallback | None, cancel: Event | None):
    def hook(data: dict) -> None:
        if cancel and cancel.is_set():
            raise DownloadCancelled("Download cancelado pelo usuário.")

        if not callback:
            return

        status = data.get("status", "")
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        downloaded = data.get("downloaded_bytes", 0)
        percent = (downloaded / total * 100) if total else None
        speed = data.get("speed")
        eta = data.get("eta")
        callback(
            Progress(
                status=status,
                percent=percent,
                speed=f"{speed / 1024 / 1024:.2f} MiB/s" if speed else None,
                eta=f"{eta}s" if eta is not None else None,
                filename=data.get("filename"),
            )
        )

    return hook


class DownloadCancelled(Exception):
    """Sinaliza cancelamento cooperativo, sem tratá-lo como erro do YouTube."""


class DownloadFailure(Exception):
    """Erro de download já classificado para a interface."""

    def __init__(self, user_message: str, technical_detail: str):
        super().__init__(user_message)
        self.user_message = user_message
        self.technical_detail = technical_detail


@dataclass(frozen=True)
class PlaylistEntry:
    """Item exibido na fila de uma playlist."""

    id: str
    title: str
    url: str
    duration: int | None = None


@dataclass(frozen=True)
class PlaylistInfo:
    """Metadados leves da playlist, sem baixar os vídeos."""

    id: str
    title: str
    entries: tuple[PlaylistEntry, ...]


@dataclass(frozen=True)
class PlaylistResult:
    attempted: int
    completed: int
    skipped_checkpoint: int
    failed: int


def _format_playlist_duration(seconds: object) -> int | None:
    try:
        value = int(seconds) if seconds is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value >= 0 else None


def inspect_playlist(url: str, log_callback: LogCallback | None = None) -> PlaylistInfo:
    """Lê títulos/IDs de uma playlist sem resolver ou baixar os streams."""

    if not url.strip():
        raise ValueError("O link da playlist não pode ficar vazio.")
    if len(url.strip()) > MAX_URL_LENGTH:
        raise ValueError(
            f"O link ultrapassa o limite de {MAX_URL_LENGTH} caracteres. "
            "Cole somente a URL da playlist."
        )
    config = {
        "quiet": True,
        "no_warnings": False,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": False,
        "noplaylist": False,
    }
    if log_callback:
        config["logger"] = _YtdlpLogger(log_callback)
    try:
        with yt_dlp.YoutubeDL(config) as ydl:
            data = ydl.extract_info(url.strip(), download=False)
    except yt_dlp.utils.DownloadError as error:
        user_message, technical_detail = summarize_error(error, url.strip())
        raise DownloadFailure(user_message, technical_detail) from error
    if not data or data.get("_type") != "playlist":
        raise DownloadFailure(
            "Este link não parece ser uma playlist do YouTube.",
            "A extração não retornou uma coleção de vídeos.",
        )
    entries: list[PlaylistEntry] = []
    for raw in data.get("entries") or []:
        if not raw:
            continue
        video_id = str(raw.get("id") or "").strip()
        if not video_id:
            continue
        entries.append(
            PlaylistEntry(
                id=video_id,
                title=str(raw.get("title") or video_id),
                url=str(raw.get("webpage_url") or raw.get("original_url") or f"https://www.youtube.com/watch?v={video_id}"),
                duration=_format_playlist_duration(raw.get("duration")),
            )
        )
    return PlaylistInfo(
        id=str(data.get("id") or "playlist"),
        title=str(data.get("title") or "Playlist"),
        entries=tuple(entries),
    )


def playlist_checkpoint_path(output_dir: Path) -> Path:
    return output_dir.expanduser().resolve() / PLAYLIST_CHECKPOINT_FILENAME


def _playlist_checkpoint_key(playlist: PlaylistInfo, options: DownloadOptions) -> str:
    identity = {
        "playlist": playlist.id,
        "output_dir": str(options.output_dir.expanduser().resolve()),
        "mode": options.mode,
        "video_limit": options.video_limit,
        "audio_format": options.audio_format,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def load_playlist_checkpoint(playlist: PlaylistInfo, options: DownloadOptions) -> set[str]:
    try:
        data = json.loads(playlist_checkpoint_path(options.output_dir).read_text(encoding="utf-8"))
        return {str(value) for value in data.get(_playlist_checkpoint_key(playlist, options), []) if value}
    except (OSError, ValueError, TypeError):
        return set()


def _save_playlist_checkpoint(playlist: PlaylistInfo, options: DownloadOptions, completed: set[str]) -> None:
    path = playlist_checkpoint_path(options.output_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        data[_playlist_checkpoint_key(playlist, options)] = sorted(completed)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def download_playlist(
    playlist: PlaylistInfo,
    entries: list[PlaylistEntry] | tuple[PlaylistEntry, ...],
    options: DownloadOptions,
    callback: ProgressCallback | None = None,
    cancel: Event | None = None,
    log_callback: LogCallback | None = None,
) -> PlaylistResult:
    """Baixa itens selecionados, salvando o checkpoint após cada sucesso."""

    completed = load_playlist_checkpoint(playlist, options)
    attempted = completed_now = skipped = failed = 0
    for entry in entries:
        if cancel and cancel.is_set():
            raise DownloadCancelled("Download da playlist cancelado pelo usuário.")
        if entry.id in completed:
            skipped += 1
            if log_callback:
                log_callback(f"Ignorado pelo checkpoint: {entry.title}")
            continue
        attempted += 1
        if log_callback:
            log_callback(f"Iniciando item: {entry.title}")
        try:
            download(replace(options, url=entry.url), callback=callback, cancel=cancel, log_callback=log_callback)
        except DownloadCancelled:
            raise
        except Exception as error:
            failed += 1
            if log_callback:
                log_callback(f"Falha em {entry.title}: {error}")
            continue
        completed.add(entry.id)
        completed_now += 1
        _save_playlist_checkpoint(playlist, options, completed)
        if log_callback:
            log_callback(f"Concluído: {entry.title}")
    return PlaylistResult(attempted, completed_now, skipped, failed)


def summarize_error(
    error: Exception,
    url: str = "",
    compatibility_attempted: bool = False,
) -> tuple[str, str]:
    """Converte erro técnico em mensagem acionável sem repetir a URL completa."""

    detail = str(error).replace(url, "<URL ocultada>") if url else str(error)
    detail = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", detail)
    lowered = detail.lower()
    if "403" in lowered or "forbidden" in lowered:
        if compatibility_attempted:
            return (
                "O YouTube bloqueou este stream mesmo após a tentativa de compatibilidade."
                " O link pode ser uma live sem stream baixável ou exigir uma atualização do motor.",
                detail,
            )
        return (
            "O YouTube bloqueou este formato. Você pode tentar usando a sessão do navegador.",
            detail,
        )
    if "login" in lowered or "sign in" in lowered or "age-restricted" in lowered:
        return (
            "Este vídeo exige login, confirmação de idade ou outra autorização do YouTube.",
            detail,
        )
    if "permission denied" in lowered or "permissionerror" in lowered:
        return (
            "O programa não tem permissão para gravar na pasta escolhida.",
            detail,
        )
    if "requested format is not available" in lowered or "requested format" in lowered:
        return (
            "O YouTube não disponibilizou um formato de vídeo ou áudio baixável para este link."
            " Ele pode ser uma live sem replay, estar indisponível ou exigir outro tipo de acesso.",
            detail,
        )
    return ("Não foi possível concluir o download. Veja os detalhes técnicos.", detail)


def build_options(
    options: DownloadOptions,
    callback: ProgressCallback | None = None,
    cancel: Event | None = None,
    log_callback: LogCallback | None = None,
) -> dict:
    """Monta configuração do yt-dlp sem executar o download."""

    output_dir = options.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    output_template = (
        "%(title)s [%(id)s] (audio).%(ext)s"
        if options.mode == "audio"
        else "%(title)s [%(id)s] (%(height)sp).%(ext)s"
    )
    ydl_options: dict = {
        "format": (
            audio_format_selector()
            if options.mode == "audio"
            else video_format_selector(options.video_limit)
        ),
        "outtmpl": str(output_dir / output_template),
        "windowsfilenames": True,
        "noplaylist": True,
        "overwrites": options.overwrite,
        "continuedl": True,
        "retries": 3,
        "fragment_retries": 3,
        "ignoreconfig": True,
        "sleep_interval_requests": 0.75,
        "progress_hooks": [_progress_hook(callback, cancel)],
        "quiet": True,
        "no_warnings": False,
        # Não force MP4: o container nativo é parte da solução escolhida pelo
        # yt-dlp e pode ser WebM, MP4 ou outro formato compatível.
    }

    if log_callback:
        ydl_options["logger"] = _YtdlpLogger(log_callback)

    if options.mode == "audio":
        ydl_options.update(
            {
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": options.audio_format,
                        "preferredquality": options.audio_quality,
                    }
                ],
            }
        )

    if options.use_browser_session:
        if not options.browser:
            raise ValueError("É necessário escolher um navegador para usar a sessão local.")
        ydl_options["cookiesfrombrowser"] = (options.browser,)

    if options.pot_provider == "wpc":
        # O WebPoClient gera o token para um cliente específico. Sem forçar
        # web_safari, o yt-dlp pode escolher outro cliente e continuar em 403.
        client = options.youtube_player_client or "web_safari"
        ydl_options["extractor_args"] = {"youtube": {"player_client": [client]}}
        if options.wpc_browser_path:
            ydl_options["extractor_args"]["youtubepot-wpc"] = {
                "browser_path": [options.wpc_browser_path]
            }

    return {key: value for key, value in ydl_options.items() if value is not None}


def download(
    options: DownloadOptions,
    callback: ProgressCallback | None = None,
    cancel: Event | None = None,
    log_callback: LogCallback | None = None,
) -> None:
    """Executa um download individual."""

    if not options.url.strip():
        raise ValueError("A URL não pode ficar vazia.")
    if len(options.url.strip()) > MAX_URL_LENGTH:
        raise ValueError(
            f"O link ultrapassa o limite de {MAX_URL_LENGTH} caracteres. "
            "Cole somente a URL do YouTube."
        )

    config = build_options(options, callback=callback, cancel=cancel, log_callback=log_callback)
    try:
        with yt_dlp.YoutubeDL(config) as ydl:
            ydl.download([options.url])
    except DownloadCancelled:
        raise
    except yt_dlp.utils.DownloadError as error:
        user_message, technical_detail = summarize_error(
            error,
            options.url,
            compatibility_attempted=options.pot_provider != "none" or options.use_browser_session,
        )
        raise DownloadFailure(user_message, technical_detail) from error
