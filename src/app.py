"""Interface do YTD1P Downloader."""

from __future__ import annotations

import queue
import json
import os
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import webbrowser

# Permite que o plugin local seja descoberto pelo yt-dlp quando a aplicação
# estiver sendo executada a partir da raiz do projeto.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
PLUGIN_ROOT = PROJECT_ROOT / "vendor" / "pot-wpc"
if PLUGIN_ROOT.exists() and str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _configure_bundled_runtime():
    """Prefere FFmpeg/Deno distribuídos com o executável, quando existirem."""

    runtime_root = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)) / "runtime"
    if runtime_root.is_dir():
        os.environ["PATH"] = str(runtime_root) + os.pathsep + os.environ.get("PATH", "")


_configure_bundled_runtime()

from src.downloader_core import (  # noqa: E402
    DownloadFailure,
    DownloadOptions,
    MAX_URL_LENGTH,
    Progress,
    download,
)
from src.update_checker import (  # noqa: E402
    RELEASE_PAGE_URL,
    fetch_latest_release,
    is_newer_version,
)


def _read_version() -> str:
    try:
        return (APP_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


APP_VERSION = _read_version()


class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"YTD1P Downloader v{APP_VERSION}")
        self.root.geometry("760x760")
        self.root.minsize(680, 680)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.skipped_existing = False
        self.postprocessing_seen = False

        self.url = tk.StringVar()
        self.default_output_dir = Path.home() / "Downloads" / "YTD1P"
        self.output_dir = tk.StringVar(value=str(self.default_output_dir))
        self.quality = tk.StringVar(value="auto")
        self.audio_only = tk.BooleanVar(value=False)
        self.audio_format = tk.StringVar(value="mp3")
        self.url_error = tk.StringVar()
        self.browser_session = tk.BooleanVar(value=False)
        # Chrome é o caminho mais provável para a sessão/cookies do usuário;
        # Firefox e Edge continuam disponíveis nas opções avançadas.
        self.browser = tk.StringVar(value="chrome")
        self.wpc_enabled = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Pronto para baixar")
        self.progress = tk.DoubleVar(value=0)
        self.status_label: tk.Label | None = None

        self._load_settings()
        self._build()
        self.root.after(100, self._drain_events)
        self.root.after(1200, self._check_updates_in_background)

    def _build(self):
        menu = tk.Menu(self.root)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="Verificar atualizações", command=self._check_updates_now)
        help_menu.add_command(
            label="Abrir página do projeto", command=lambda: webbrowser.open(RELEASE_PAGE_URL)
        )
        menu.add_cascade(label="Ajuda", menu=help_menu)
        self.root.configure(menu=menu)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        video_tab = ttk.Frame(notebook, padding=16)
        notebook.add(video_tab, text="Vídeo")

        ttk.Label(video_tab, text="Link do vídeo", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        url_entry = ttk.Entry(
            video_tab,
            textvariable=self.url,
            font=("Segoe UI", 11),
            validate="key",
            validatecommand=(self.root.register(self._validate_url_input), "%P"),
            invalidcommand=(self.root.register(self._reject_url_input), "%P"),
        )
        url_entry.pack(fill="x", pady=(4, 14))
        ttk.Label(video_tab, textvariable=self.url_error, foreground="#b00020").pack(
            anchor="w", pady=(0, 8)
        )

        options = ttk.LabelFrame(video_tab, text="Configurações", padding=12)
        options.pack(fill="x")

        row = ttk.Frame(options)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Qualidade do vídeo:").pack(side="left")
        self.quality_box = ttk.Combobox(
            row,
            textvariable=self.quality,
            values=("auto", "2160", "1440", "1080", "720", "480", "360"),
            state="readonly",
            width=18,
        )
        self.quality_box.pack(side="left", padx=8)
        ttk.Label(row, text="Automático = melhor disponível").pack(side="left")

        ttk.Checkbutton(
            options,
            text="Extrair somente áudio",
            variable=self.audio_only,
            command=self._toggle_audio,
        ).pack(anchor="w", pady=6)
        self.audio_row = ttk.Frame(options)
        ttk.Label(self.audio_row, text="Formato do áudio:").pack(side="left")
        ttk.Combobox(
            self.audio_row,
            textvariable=self.audio_format,
            values=("mp3", "m4a", "opus", "flac", "wav"),
            state="readonly",
            width=12,
        ).pack(side="left", padx=8)

        destination = ttk.LabelFrame(video_tab, text="Destino", padding=12)
        destination.pack(fill="x", pady=12)
        ttk.Entry(destination, textvariable=self.output_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(destination, text="Escolher pasta…", command=self._choose_folder).pack(side="left", padx=(8, 0))
        self.open_folder_button = ttk.Button(
            destination, text="Abrir pasta", command=self._open_folder
        )
        self.open_folder_button.pack(side="left", padx=(8, 0))

        advanced = ttk.LabelFrame(video_tab, text="Opções avançadas", padding=12)
        advanced.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(
            advanced,
            text="Usar sempre a sessão local do navegador",
            variable=self.browser_session,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(advanced, text="Navegador:").grid(row=0, column=1, padx=(18, 4))
        ttk.Combobox(
            advanced,
            textvariable=self.browser,
            values=("chrome", "firefox", "edge"),
            state="readonly",
            width=10,
        ).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(
            advanced,
            text="Ativar modo compatibilidade YouTube (PO Token)",
            variable=self.wpc_enabled,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.start_button = tk.Button(
            video_tab,
            text="INICIAR DOWNLOAD",
            command=self._start,
            font=("Segoe UI", 13, "bold"),
            height=2,
            bg="#1769aa",
            fg="white",
            activebackground="#0d4f80",
        )
        self.start_button.pack(fill="x", pady=(0, 8))
        self.cancel_button = ttk.Button(video_tab, text="Cancelar", command=self._cancel, state="disabled")
        self.cancel_button.pack(anchor="e")

        self.status_label = tk.Label(
            video_tab,
            textvariable=self.status,
            anchor="w",
            justify="left",
            fg="#555555",
        )
        self.status_label.pack(fill="x", anchor="w", pady=(12, 4))
        ttk.Progressbar(video_tab, variable=self.progress, maximum=100).pack(fill="x")

        details = ttk.LabelFrame(video_tab, text="Detalhes técnicos", padding=8)
        details.pack(fill="both", expand=True, pady=(12, 0))
        self.log = tk.Text(details, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

    def _toggle_audio(self):
        if self.audio_only.get():
            self.audio_row.pack(anchor="w", pady=(0, 4))
            self.quality_box.configure(state="disabled")
        else:
            self.audio_row.pack_forget()
            self.quality_box.configure(state="readonly")

    def _validate_url_input(self, proposed: str) -> bool:
        """Impede que uma colagem grande seja renderizada no campo."""

        if len(proposed) <= MAX_URL_LENGTH:
            self.url_error.set("")
        return len(proposed) <= MAX_URL_LENGTH

    def _reject_url_input(self, proposed: str):
        """Explica por que uma colagem grande não entrou no campo."""

        if len(proposed) > MAX_URL_LENGTH:
            self.url_error.set(
                f"Link muito grande — máximo de {MAX_URL_LENGTH} caracteres. "
                "Cole somente a URL do YouTube."
            )

    def _choose_folder(self):
        current = Path(self.output_dir.get()).expanduser()
        initial_dir = str(current if current.is_dir() else Path.home())
        chosen = filedialog.askdirectory(initialdir=initial_dir)
        if chosen:
            self.output_dir.set(chosen)
            self._save_settings()

    @property
    def _settings_file(self) -> Path:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / ".config"
        return base / "YTD1P" / "settings.json"

    def _load_settings(self):
        """Recupera apenas preferências locais e não armazena cookies ou links."""

        try:
            data = json.loads(self._settings_file.read_text(encoding="utf-8"))
            saved_dir = data.get("output_dir")
            if isinstance(saved_dir, str) and saved_dir.strip():
                self.output_dir.set(saved_dir)
        except (OSError, ValueError, TypeError):
            # Primeira execução ou arquivo de preferências corrompido:
            # mantém o destino padrão sem interromper a abertura do app.
            pass

    def _save_settings(self):
        """Salva somente a última pasta escolhida para facilitar o próximo uso."""

        try:
            settings_file = self._settings_file
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps({"output_dir": self.output_dir.get()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            # Preferências são opcionais; falha de gravação não pode impedir download.
            pass

    def _open_folder(self):
        folder = Path(self.output_dir.get()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except (OSError, AttributeError) as error:
            messagebox.showerror("Pasta não disponível", f"Não foi possível abrir a pasta escolhida.\n\n{error}")

    def _check_updates_in_background(self):
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_now(self):
        self._set_status("Verificando atualizações…", "working")
        self._append_log("Consultando a release pública do YTD1P…")
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self):
        try:
            release = fetch_latest_release()
            self.events.put(("update_result", release))
        except Exception as error:  # rede indisponível não impede o download local
            self.events.put(("update_error", error))

    def _handle_update_result(self, release):
        if is_newer_version(APP_VERSION, release.version):
            self._set_status(f"Atualização disponível: v{release.version}", "warning")
            self._append_log(f"Nova versão disponível: v{release.version}.")
            if messagebox.askyesno(
                "Atualização disponível",
                f"A versão v{release.version} está disponível.\n\n"
                "Deseja abrir a página para baixar a atualização?",
            ):
                webbrowser.open(release.page_url)
        else:
            self._set_status(f"YTD1P v{APP_VERSION} está atualizado.", "success")
            self._append_log("Nenhuma atualização do aplicativo encontrada.")

    def _handle_update_error(self, error):
        self._append_log(f"Atualização não verificada: {error}")
        if self.status.get() == "Verificando atualizações…":
            self._set_status(
                "Não foi possível verificar atualizações; funcionamento normal mantido.",
                "warning",
            )

    def _append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text: str, tone: str = "neutral"):
        """Atualiza o resumo visível para quem não precisa ler o terminal."""

        colors = {
            "neutral": "#555555",
            "working": "#555555",
            "success": "#167c2e",
            "warning": "#9a6700",
            "error": "#b00020",
        }
        self.status.set(text)
        if self.status_label is not None:
            self.status_label.configure(fg=colors.get(tone, colors["neutral"]))

    def _start(self):
        url = self.url.get().strip()
        if not url:
            self._set_status("Cole um link do YouTube antes de iniciar.", "error")
            messagebox.showwarning("Link ausente", "Cole o link do vídeo antes de iniciar.")
            return
        if len(url) > MAX_URL_LENGTH:
            self.url_error.set(
                f"Link muito grande — máximo de {MAX_URL_LENGTH} caracteres. "
                "Cole somente a URL do YouTube."
            )
            self._set_status("O link ultrapassa o limite permitido.", "error")
            return
        self.cancel_event.clear()
        self._save_settings()
        self.skipped_existing = False
        self.postprocessing_seen = False
        self.progress.set(0)
        self._append_log("Iniciando download…")
        self._set_status("Preparando…", "working")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self._launch_worker(self._make_options())

    def _make_options(self, use_compatibility: bool | None = None) -> DownloadOptions:
        if use_compatibility is None:
            use_compatibility = self.wpc_enabled.get()
        return DownloadOptions(
            url=self.url.get().strip(),
            output_dir=Path(self.output_dir.get()),
            mode="audio" if self.audio_only.get() else "video",
            video_limit=self.quality.get(),
            audio_format=self.audio_format.get(),
            use_browser_session=self.browser_session.get(),
            browser=self.browser.get() if self.browser_session.get() else None,
            pot_provider="wpc" if use_compatibility else "none",
            wpc_browser_path=self._find_wpc_browser() if use_compatibility else None,
        )

    @staticmethod
    def _find_wpc_browser() -> str | None:
        """Encontra um navegador Chromium para o provedor WebPoClient."""

        candidates = [
            shutil.which("chrome"),
            shutil.which("chromium"),
            shutil.which("msedge"),
        ]
        for base in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if base:
                candidates.extend(
                    [
                        str(Path(base) / "Google/Chrome/Application/chrome.exe"),
                        str(Path(base) / "Chromium/Application/chrome.exe"),
                        str(Path(base) / "Microsoft/Edge/Application/msedge.exe"),
                    ]
                )
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate))
        return None

    def _launch_worker(self, options: DownloadOptions):
        self.worker = threading.Thread(target=self._run_download, args=(options,), daemon=True)
        self.worker.start()

    def _run_download(self, options: DownloadOptions):
        try:
            download(
                options,
                callback=lambda value: self.events.put(("progress", value)),
                cancel=self.cancel_event,
                log_callback=lambda value: self.events.put(("log", value)),
            )
            self.events.put(("done", "Download concluído."))
        except Exception as error:  # a thread não deve derrubar a interface
            self.events.put(("error", (error, options)))

    def _cancel(self):
        self.cancel_event.set()
        self._set_status("Cancelando…", "warning")
        self._append_log("Cancelamento solicitado.")

    def _drain_events(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "progress":
                    self._handle_progress(value)
                elif event == "done":
                    final_status = (
                        "Já estava baixado — nenhum arquivo novo foi criado."
                        if self.skipped_existing and not self.postprocessing_seen
                        else str(value)
                    )
                    self._set_status(
                        final_status,
                        "warning" if final_status.startswith("Já estava") else "success",
                    )
                    self._append_log(final_status)
                    print(final_status, flush=True)
                    self._finish()
                elif event == "error":
                    self._handle_error(value)
                elif event == "retry_prompt":
                    self._handle_retry_prompt(value)
                elif event == "update_result":
                    self._handle_update_result(value)
                elif event == "update_error":
                    self._handle_update_error(value)
                elif event == "log":
                    self._handle_log(str(value))
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_log(self, text: str):
        lowered = text.lower()
        if "[extractaudio]" in lowered or "deleting original file" in lowered:
            self.postprocessing_seen = True
        if "already been downloaded" in lowered or "já foi baixado" in lowered:
            self.skipped_existing = True
            self._set_status(
                "Esse formato já existe; verificando se ainda falta algum processamento…",
                "warning",
            )
            self._append_log("Fonte já baixada; verificando conversão/processamento final…")
        else:
            self._append_log(text)
        print(text, flush=True)

    def _handle_progress(self, value: Progress):
        if value.percent is not None:
            self.progress.set(value.percent)
        if value.status == "downloading":
            self._set_status("Baixando…", "working")
        elif value.status == "finished":
            self._set_status("Download recebido; finalizando arquivo…", "working")

    def _handle_error(self, value):
        error, options = value
        if (
            isinstance(error, DownloadFailure)
            and options.pot_provider == "none"
            and "403" in error.technical_detail
            and not self.cancel_event.is_set()
        ):
            self._set_status(
                "O YouTube bloqueou a primeira tentativa; preparando outra forma de acesso…",
                "warning",
            )
            self._handle_retry_prompt((error, options))
            return

        self._finish()
        if isinstance(error, DownloadFailure):
            self._set_status(error.user_message, "error")
            self._append_log(error.technical_detail)
            print(f"ERRO: {error.technical_detail}", flush=True)
            messagebox.showerror("Download não concluído", error.user_message)
        else:
            self._set_status("Não foi possível concluir o download.", "error")
            self._append_log(str(error))
            print(f"ERRO: {error}", flush=True)
            messagebox.showerror("Erro", "Não foi possível concluir o download. Veja os detalhes técnicos.")

    def _handle_retry_prompt(self, value):
        error, options = value
        browser_path = options.wpc_browser_path or self._find_wpc_browser()
        if not browser_path:
            message = (
                "Para tentar o modo de compatibilidade, é necessário ter Chrome, Chromium "
                "ou Edge instalado. O Firefox não é compatível com este recurso."
            )
            self._finish()
            self._set_status(message, "error")
            self._append_log(message)
            messagebox.showerror("Navegador compatível não encontrado", message)
            return
        accepted = messagebox.askyesno(
            "Tentar modo de compatibilidade?",
            "O YouTube bloqueou este download.\n\n"
            "Deseja tentar novamente usando um navegador auxiliar para gerar a verificação?\n"
            "Uma janela do navegador ficará aberta durante a tentativa.",
        )
        if not accepted:
            self._finish()
            self._set_status(error.user_message, "error")
            self._append_log(error.technical_detail)
            messagebox.showerror("Download não concluído", error.user_message)
            return

        self.wpc_enabled.set(True)
        self._set_status("Tentando modo de compatibilidade…", "working")
        self._append_log("403 detectado. Tentando WebPoClient…")
        self._launch_worker(
            DownloadOptions(
                url=options.url,
                output_dir=options.output_dir,
                mode=options.mode,
                video_limit=options.video_limit,
                audio_format=options.audio_format,
                audio_quality=options.audio_quality,
                overwrite=options.overwrite,
                use_browser_session=options.use_browser_session,
                browser=options.browser,
                pot_provider="wpc",
                youtube_player_client="web_safari",
                wpc_browser_path=browser_path,
            )
        )

    def _finish(self):
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")


def main():
    root = tk.Tk()
    DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
