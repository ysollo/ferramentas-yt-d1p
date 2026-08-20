"""Motor local do YTD1P Downloader.

Esta camada não conhece a interface gráfica. Ela traduz opções amigáveis em
configuração do yt-dlp, emite progresso e permite cancelamento cooperativo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from threading import Event
from typing import Callable, Literal

import yt_dlp


VideoLimit = Literal["auto", "144", "240", "360", "480", "720", "1080", "1440", "2160"]
AudioFormat = Literal["mp3", "m4a", "opus", "flac", "wav", "aac", "alac", "vorbis"]
PotProvider = Literal["none", "wpc"]
MAX_URL_LENGTH = 2048


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
    """Retorna melhor vídeo+áudio MP4 até o limite escolhido."""

    if limit == "auto":
        return "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"

    # O fallback final permite concluir mesmo quando o filtro de MP4 não
    # encontra uma combinação perfeita; o pós-processamento decide a extensão.
    return (
        f"bv*[height<={limit}][ext=mp4]+ba[ext=m4a]/"
        f"b[height<={limit}][ext=mp4]/b[height<={limit}]/b"
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
        "progress_hooks": [_progress_hook(callback, cancel)],
        "quiet": True,
        "no_warnings": False,
        "merge_output_format": "mp4" if options.mode == "video" else None,
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
