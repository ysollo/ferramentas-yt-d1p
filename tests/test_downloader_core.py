import unittest
from pathlib import Path

from src.downloader_core import (
    DownloadOptions,
    MAX_URL_LENGTH,
    audio_format_selector,
    build_options,
    video_format_selector,
    summarize_error,
)


class SelectorTests(unittest.TestCase):
    def test_auto_prefers_mp4_video_and_m4a_audio(self):
        self.assertEqual(video_format_selector("auto"), "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b")

    def test_1080_is_a_maximum_with_lower_fallback(self):
        selector = video_format_selector("1080")
        self.assertIn("height<=1080", selector)
        self.assertIn("/b[height<=1080]/b", selector)

    def test_audio_uses_audio_only_source(self):
        self.assertEqual(audio_format_selector(), "ba/b")

    def test_audio_options_include_postprocessing(self):
        output = Path(__file__).parent / "_test-output"
        output.mkdir(exist_ok=True)
        config = build_options(
            DownloadOptions(url="https://example.test/video", output_dir=output, mode="audio")
        )
        output.rmdir()
        self.assertEqual(config["format"], "ba/b")
        self.assertEqual(
            config["postprocessors"],
            [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "5",
                }
            ],
        )

    def test_403_is_summarized_without_url(self):
        message, detail = summarize_error(
            RuntimeError("ERROR: HTTP Error 403: Forbidden https://example.test/private"),
            "https://example.test/private",
        )
        self.assertIn("bloqueou", message)
        self.assertNotIn("https://example.test/private", detail)

    def test_wpc_forces_compatible_youtube_client(self):
        with TemporaryOutput() as output:
            config = build_options(
                DownloadOptions(
                    url="https://example.test/video",
                    output_dir=output,
                    pot_provider="wpc",
                )
            )
        self.assertEqual(config["extractor_args"], {"youtube": {"player_client": ["web_safari"]}})

    def test_wpc_can_use_explicit_chromium_browser_path(self):
        with TemporaryOutput() as output:
            config = build_options(
                DownloadOptions(
                    url="https://example.test/video",
                    output_dir=output,
                    pot_provider="wpc",
                    wpc_browser_path="C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
                )
            )
        self.assertEqual(
            config["extractor_args"]["youtubepot-wpc"],
            {"browser_path": ["C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"]},
        )

    def test_video_filename_includes_resolution(self):
        with TemporaryOutput() as output:
            config = build_options(
                DownloadOptions(url="https://example.test/video", output_dir=output, video_limit="1080")
            )
        self.assertIn("(%(height)sp)", config["outtmpl"])

    def test_audio_filename_identifies_audio_output(self):
        with TemporaryOutput() as output:
            config = build_options(
                DownloadOptions(url="https://example.test/video", output_dir=output, mode="audio")
            )
        self.assertIn("(audio).%(ext)s", config["outtmpl"])

    def test_unavailable_format_is_friendly_and_ansi_free(self):
        message, detail = summarize_error(
            RuntimeError("\x1b[0;31mERROR:\x1b[0m Requested format is not available"),
        )
        self.assertIn("não disponibilizou", message)
        self.assertNotIn("\\x1b", detail)

    def test_403_after_compatibility_does_not_suggest_same_retry(self):
        message, _ = summarize_error(
            RuntimeError("HTTP Error 403: Forbidden"),
            compatibility_attempted=True,
        )
        self.assertIn("mesmo após", message)
        self.assertNotIn("tentar usando a sessão", message)

    def test_rejects_unreasonably_long_url_before_download(self):
        from src.downloader_core import download

        with self.assertRaisesRegex(ValueError, "2048 caracteres"):
            download(
                DownloadOptions(
                    url="https://youtu.be/" + ("x" * MAX_URL_LENGTH),
                    output_dir=Path(__file__).parent / "_test-output",
                )
            )


class TemporaryOutput:
    def __enter__(self):
        self.path = Path(__file__).parent / "_test-output"
        self.path.mkdir(exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, traceback):
        self.path.rmdir()


if __name__ == "__main__":
    unittest.main()
