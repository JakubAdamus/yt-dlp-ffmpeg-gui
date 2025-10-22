import os
import sys
import re
import json
import zipfile
import shutil
import subprocess
import threading
import queue
import platform
import ctypes as ct
from enum import StrEnum
from typing import Any, Dict, NamedTuple, Protocol, cast
from dataclasses import dataclass

import tkinter as tk
from tkinter import ttk
from tkinter import Event, filedialog, messagebox
import requests


CONNECT_TIMEOUT = 10  # in seconds
READ_TIMEOUT = 300  # in seconds
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)


class SupportedPlatform(StrEnum):
    YT = "YT"
    VIMEO = "Vimeo"


class ExecutableName(StrEnum):
    YT_DLP = "yt-dlp.exe"
    FFMPEG = "ffmpeg.exe"


class DownloadResult(NamedTuple):
    success: bool
    top_window: tk.Toplevel
    error_message: str | None = None
    file_path: str | None = None


class ResultQueue[T]:
    def __init__(self) -> None:
        self._queue: queue.Queue[T] = queue.Queue()

    def put(self, value: T) -> None:
        self._queue.put(value)

    def get_next(self) -> T:
        return self._queue.get_nowait()


# pylint: disable=too-few-public-methods
class Command[T](Protocol):
    def run(self) -> T: ...


@dataclass
class DownloadVideoCommand(Command[bool]):
    cmd: list[str]

    def run(self) -> bool:
        subprocess.run(self.cmd, check=True)
        return True


@dataclass
class UpdateYtDlpCommand(Command[str]):
    yt_dlp_path: str

    def run(self) -> str:
        kwargs: Dict[str, Any] = {}

        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        old_version_result = subprocess.run(
            [self.yt_dlp_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
            **kwargs,
        )
        old_version = old_version_result.stdout.strip()

        subprocess.run([self.yt_dlp_path, "-U"], check=True, **kwargs)

        new_version_result = subprocess.run(
            [self.yt_dlp_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
            **kwargs,
        )
        new_version = new_version_result.stdout.strip()

        return new_version if new_version != old_version else ""


@dataclass
class GetVideoInfoCommand(Command[dict[str, Any]]):
    yt_dlp_path: str
    url: str

    def run(self) -> dict[str, Any]:
        cmd: list[str] = [self.yt_dlp_path, "-j", self.url]
        kwargs: Dict[str, Any] = {"capture_output": True, "text": True}

        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(cmd, check=True, **kwargs)

        return cast(dict[str, Any], json.loads(result.stdout))


def dark_title_bar(window: tk.Tk | tk.Toplevel) -> None:

    def supports_dark_title_bar() -> bool:
        if platform.system() != "Windows":
            return False

        version = platform.version().split(".")

        try:
            build = int(version[-1])

        except ValueError:
            return False

        return build >= 17763

    if not supports_dark_title_bar():
        return

    window.update_idletasks()

    # pylint: disable=invalid-name
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    set_window_attribute = ct.windll.dwmapi.DwmSetWindowAttribute
    get_parent = ct.windll.user32.GetParent
    hwnd = get_parent(window.winfo_id())

    value = ct.c_int(2)
    set_window_attribute(
        hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ct.byref(value), ct.sizeof(value)
    )


def validate_regex(value: str, pattern: str) -> bool:
    return re.fullmatch(pattern, value.strip()) is not None


def validate_url(url: str) -> bool:
    if not url:
        return False

    url_regex = r"^https?://[^\s/$.?#].[^\s]*$"
    return validate_regex(url, url_regex)


def get_path(executable: ExecutableName) -> str:
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.join(exe_dir, executable)


def parse_time(time_str: str) -> int | None:
    time_pattern = r"^(\d+)(?:[:.](\d{1,2}))?(?:[:.](\d{1,2}))?$"
    match = re.match(time_pattern, time_str.strip())

    if not match:
        return None

    if match.group(2) is None and match.group(3) is None:
        seconds = int(match.group(1))
        return seconds

    hours = int(match.group(1))
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    total_seconds = hours * 3600 + minutes * 60 + seconds

    return total_seconds


# pylint: disable=too-many-instance-attributes, too-many-statements
class VideoDownloader(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.download_queue: queue.Queue[DownloadResult] = queue.Queue()
        self.dark_bg = "#2b2b2b"
        self.widget_bg = "#3c3c3c"
        self.active_bg = "#505050"
        self.text_fg = "#ffffff"
        self.disabled_bg = "#2a2a2a"
        self.disabled_fg = "#7a7a7a"

        self.default_font = ("Segoe UI", 11)
        self.option_add("*Font", self.default_font)

        self.title("Video Downloader")
        self.geometry("435x535")
        self.resizable(False, False)

        self.configure(bg=self.dark_bg)

        self.style = ttk.Style(self)
        self.style.theme_use("clam")

        self.after(10, lambda: dark_title_bar(self))

        self.style.configure("TLabel", background=self.dark_bg, foreground=self.text_fg)

        self.style.configure(
            "TButton",
            background=self.widget_bg,
            foreground=self.text_fg,
            bordercolor=self.text_fg,
            font=self.default_font,
            relief="flat",
        )

        self.style.map(
            "TButton",
            background=[("active", self.active_bg), ("disabled", self.disabled_bg)],
            foreground=[("active", self.text_fg), ("disabled", self.disabled_fg)],
        )

        self.style.configure(
            "TCombobox",
            fieldbackground=self.widget_bg,
            background=self.widget_bg,
            foreground=self.text_fg,
            arrowcolor=self.text_fg,
        )

        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.widget_bg), ("active", self.active_bg)],
            foreground=[("readonly", self.text_fg), ("active", self.text_fg)],
            background=[("readonly", self.widget_bg), ("active", self.active_bg)],
        )

        entry_opts: dict[str, Any] = {
            "bg": self.widget_bg,
            "fg": self.text_fg,
            "insertbackground": self.text_fg,
            "highlightthickness": 0,
            "relief": "flat",
            "disabledbackground": self.disabled_bg,
            "disabledforeground": self.disabled_fg,
        }

        ttk.Label(self, text="Select platform:").pack(pady=5)
        plaftorm_list = [platform.value for platform in SupportedPlatform]
        self.platform_cbx = ttk.Combobox(
            self, values=plaftorm_list, state="readonly", width=10
        )
        self.platform_cbx.set("YT")
        self.platform_cbx.pack(pady=5)
        self.platform_cbx.bind("<<ComboboxSelected>>", self.on_platform_change)

        ttk.Label(self, text="Enter the URL of the video:").pack(pady=5)
        self.url_entry = tk.Entry(self, width=50, **entry_opts)
        self.url_entry.pack(pady=5)

        self.url_entry.bind("<FocusOut>", self.update_resolutions)
        self.url_entry.bind("<Return>", self.update_resolutions)

        self.resolution_label = ttk.Label(self, text="Select resolution:")
        self.resolution_label.pack(pady=5)
        self.resolution_combobox = ttk.Combobox(self, state="readonly", width=10)
        self.resolution_combobox.pack(pady=5)
        self.resolution_combobox.bind("<<ComboboxSelected>>", self.on_resolution_change)

        self.start_label = ttk.Label(self, text="Start time (hh:mm:ss):")
        self.start_label.pack(pady=5)
        self.start_time_entry = tk.Entry(self, width=20, **entry_opts)
        self.start_time_entry.pack(pady=5)

        self.end_label = ttk.Label(self, text="End time (hh:mm:ss):")
        self.end_label.pack(pady=5)
        self.end_time_entry = tk.Entry(self, width=20, **entry_opts)
        self.end_time_entry.pack(pady=5)

        self.crf_label = ttk.Label(self, text="Select CRF value:")
        self.crf_label.pack(pady=5)
        self.crf_combobox = ttk.Combobox(
            self, values=[str(i) for i in range(18, 29)], state="readonly", width=10
        )
        self.crf_combobox.set("23")
        self.crf_combobox.pack(pady=5)
        self.crf_combobox.bind("<<ComboboxSelected>>", self.on_crf_change)

        self.auth_switch_value = tk.BooleanVar(value=False)
        self.auth_switch = tk.Checkbutton(
            self,
            text="Authorize YT",
            variable=self.auth_switch_value,
            onvalue=True,
            offvalue=False,
            bg=self.dark_bg,
            fg=self.text_fg,
            selectcolor=self.dark_bg,
            activebackground=self.dark_bg,
            activeforeground=self.text_fg,
        )
        self.auth_switch.pack(pady=5)

        self.download_button = ttk.Button(
            self,
            text="Download Video",
            command=self.download_video,
            width=20,
            state="disabled",
        )
        self.download_button.pack(side="bottom", pady=20)

        self.after(100, self.check_dependencies)

    def update_resolutions(  # pylint: disable=unused-argument
        self, event: Event[tk.Entry]
    ) -> None:
        url = self.url_entry.get()
        selected_platform = self.platform_cbx.get()

        if not validate_url(url):
            self.download_button.config(state="disabled")
            return

        if selected_platform != SupportedPlatform.YT:
            self.download_button.config(state="normal")
            return

        self.download_button.config(state="disabled")
        yt_dlp_path = self._get_executable(ExecutableName.YT_DLP)

        if yt_dlp_path is None:
            return

        command = GetVideoInfoCommand(yt_dlp_path=yt_dlp_path, url=url)
        result_queue: ResultQueue[dict[str, Any]] = ResultQueue()

        thread = threading.Thread(
            target=self.run_command_in_thread,
            args=(command, result_queue),
            daemon=True,
        )
        thread.start()

        def update_gui(info: dict[str, Any]) -> None:
            resolutions = {
                f["height"]
                for f in info.get("formats", [])
                if f.get("height") and f["height"] >= 144
            }

            if not resolutions:
                messagebox.showerror(
                    "No resolution available", "Failed to retrieve video information."
                )
                self.resolution_combobox["values"] = []
                return

            res_list = [str(r) for r in sorted(resolutions, key=int)]
            self.resolution_combobox["values"] = res_list
            self.resolution_combobox.set(res_list[-1])
            self.download_button.config(state="normal")

        def check_queue() -> None:
            try:
                info = result_queue.get_next()
            except queue.Empty:
                self.after(100, check_queue)
            else:
                update_gui(info)

        check_queue()

    def run_command_in_thread[T](
        self, command: Command[T], result_queue: ResultQueue[T]
    ) -> None:
        try:
            result = command.run()
        except subprocess.CalledProcessError:
            msg = "Download failed."
            selected_platform = self.platform_cbx.get()
            authorize = self.auth_switch_value.get()

            if selected_platform == "YT" and not authorize:
                msg += "\nTry again with 'Authorize YT' checked."

            self.after(0, lambda: messagebox.showerror("Error", msg))
        else:
            result_queue.put(result)

    def check_dependencies(self) -> None:
        yt_dlp_path = self._get_executable(ExecutableName.YT_DLP, show_error=False)

        if yt_dlp_path is None:
            messagebox.showinfo("Installation", "Downloading yt-dlp.exe...")
            self.download_file(
                "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
                get_path(ExecutableName.YT_DLP),
            )

        else:
            command = UpdateYtDlpCommand(yt_dlp_path=yt_dlp_path)
            result_queue = ResultQueue[bool]()
            self.url_entry.config(state="disabled")

            thread = threading.Thread(
                target=self.run_command_in_thread,
                args=(command, result_queue),
                daemon=True,
            )
            thread.start()

            def check_queue_periodically() -> None:
                try:
                    new_version = result_queue.get_next()
                except queue.Empty:
                    self.after(100, check_queue_periodically)
                else:
                    if new_version:
                        self.after(
                            0,
                            lambda: messagebox.showinfo(
                                "Update completed",
                                f"yt-dlp has been updated.\nVersion: {new_version}",
                            ),
                        )
                finally:
                    self.url_entry.config(state="normal")

            check_queue_periodically()

        ffmpeg_path = self._get_executable(ExecutableName.FFMPEG)

        if ffmpeg_path is None:
            base_path = os.path.dirname(os.path.abspath(sys.executable))
            archive_path = os.path.join(base_path, "ffmpeg.zip")

            messagebox.showinfo("Installation", "Downloading ffmpeg.exe...")
            self.download_file(
                "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
                archive_path,
            )

    def download_file(self, url: str, dest: str) -> None:
        top = tk.Toplevel(self)
        top.title("Downloading")
        top.configure(bg=self.dark_bg)
        self.after(10, lambda: dark_title_bar(top))

        tk.Label(
            top,
            text=f"Downloading {os.path.basename(dest)}",
            fg=self.text_fg,
            bg=self.dark_bg,
        ).pack(pady=10)

        green = "#00aa00"

        self.style.configure(
            "Dark.Horizontal.TProgressbar",
            troughcolor=self.widget_bg,
            background=green,
            bordercolor=self.widget_bg,
            lightcolor=green,
            darkcolor=green,
        )

        progress = ttk.Progressbar(
            top,
            length=300,
            mode="determinate",
            maximum=100,
            style="Dark.Horizontal.TProgressbar",
        )
        progress.pack(pady=10)

        def worker() -> None:
            try:
                response = requests.get(url, stream=True, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                total_size = int(response.headers.get("Content-Length", 0))
                downloaded = 0

                with open(dest, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                percent = int(downloaded * 100 / total_size)
                                self.after(0, progress.config, {"value": percent})

            except requests.RequestException as e:
                self.download_queue.put(
                    DownloadResult(
                        success=False,
                        error_message=f"Network error: {e}",
                        top_window=top,
                    )
                )
            except (OSError, IOError) as e:
                self.download_queue.put(
                    DownloadResult(
                        success=False, error_message=f"File error: {e}", top_window=top
                    )
                )
            else:
                self.download_queue.put(
                    DownloadResult(success=True, file_path=dest, top_window=top)
                )

        self.url_entry.config(state="disabled")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        def extract_ffmpeg(file_path: str) -> None:
            """Extract ffmpeg.exe safely and clean up temporary files."""
            try:
                base_path = os.path.dirname(os.path.abspath(sys.executable))

                with zipfile.ZipFile(file_path, "r") as zip_ref:
                    for member in zip_ref.namelist():
                        if member.replace("\\", "/").endswith("bin/ffmpeg.exe"):
                            zip_ref.extract(member, path=base_path)
                            src = os.path.join(base_path, member)
                            os.replace(src, get_path(ExecutableName.FFMPEG))
                            break

                os.remove(file_path)

                # Cleanup temp ffmpeg folders
                for item in os.listdir(base_path):
                    folder_path = os.path.join(base_path, item)
                    if os.path.isdir(folder_path) and item.startswith("ffmpeg"):
                        shutil.rmtree(folder_path)

                messagebox.showinfo("Done", "ffmpeg.exe has been extracted.")
            except (FileNotFoundError, zipfile.BadZipFile) as e:
                messagebox.showerror("Error", f"Failed to extract ffmpeg: {e}")
            except OSError as e:
                messagebox.showerror("Error", f"OS error during extraction: {e}")
            else:
                self.url_entry.config(state="normal")

        def check_queue_periodically() -> None:
            try:
                result: DownloadResult = self.download_queue.get_nowait()
            except queue.Empty:
                self.after(100, check_queue_periodically)
                return

            if result.success and result.file_path:
                file_path = result.file_path
                file_name = os.path.basename(file_path)

                messagebox.showinfo("Success", f"File downloaded: {file_path}")
                result.top_window.destroy()

                if file_name == "ffmpeg.zip":
                    thread = threading.Thread(
                        target=extract_ffmpeg, args=(file_path,), daemon=True
                    )
                    thread.start()
                elif file_name == ExecutableName.YT_DLP:
                    self.url_entry.config(state="normal")

            elif result.error_message:
                messagebox.showerror(
                    "Error", f"Failed to download file: {result.error_message}"
                )
                result.top_window.destroy()

        check_queue_periodically()

    def _get_executable(
        self, executable: ExecutableName, show_error: bool = True
    ) -> str | None:
        path = get_path(executable)
        if not os.path.exists(path):
            if show_error:
                messagebox.showerror("Error", f"File '{executable}' not found!")
            return None
        return path

    def _build_download_sections(self, start_time: str, end_time: str) -> list[str]:
        if not start_time and not end_time:
            return []

        sections: list[str] = []

        start_seconds = parse_time(start_time) if start_time else None
        end_seconds = parse_time(end_time) if end_time else None

        if (start_time and start_seconds is None) or (end_time and end_seconds is None):
            raise ValueError("Invalid time format. Use seconds, HH:MM or HH:MM:SS")

        if (
            start_seconds is not None
            and end_seconds is not None
            and end_seconds <= start_seconds
        ):
            raise ValueError("End time cannot be earlier than start time")

        sections.append("--download-sections")

        if start_time and end_time:
            sections.append(f"*{start_time}-{end_time}")
        elif start_time:
            sections.append(f"*{start_time}-")
        elif end_time:
            sections.append(f"*-{end_time}")

        sections.append("--force-keyframes-at-cuts")

        return sections

    def _build_youtube_command(
        self,
        *,
        yt_dlp_path: str,
        ffmpeg_path: str,
        output_template: str,
        url: str,
    ) -> list[str]:
        start_time = self.start_time_entry.get()
        end_time = self.end_time_entry.get()
        authorize = self.auth_switch_value.get()
        resolution = self.resolution_combobox.get()
        crf_value = self.crf_combobox.get()
        output_format = f"bestvideo[height={resolution}]+bestaudio/best"

        cmd = [
            yt_dlp_path,
            "-f",
            output_format,
            "--merge-output-format",
            "mp4",
            "--ffmpeg-location",
            ffmpeg_path,
            "-o",
            output_template,
            url,
            "--postprocessor-args",
            f"-c:v libx264 -crf {crf_value}",
            "--force-overwrite",
        ]

        sections = self._build_download_sections(start_time, end_time)
        if sections:
            cmd.extend(sections)

        if authorize:
            cmd.extend(["--cookies-from-browser", "firefox"])

        return cmd

    def _build_vimeo_command(
        self, *, yt_dlp_path: str, url: str, output_template: str
    ) -> list[str]:
        match = re.match(r"^https://vimeo\.com/(\d+)", url)

        if not match:
            raise ValueError("Invalid Vimeo URL")

        video_id = match.group(1)
        vimeo_url = f"https://player.vimeo.com/video/{video_id}"

        return [
            yt_dlp_path,
            vimeo_url,
            "-o",
            output_template,
            "--referer",
            "https://www.patreon.com",
            "--force-overwrite",
        ]

    def _run_download_command(self, cmd: list[str]) -> None:
        command = DownloadVideoCommand(cmd=cmd)
        result_queue = ResultQueue[bool]()
        self.download_button.config(state="disabled")

        thread = threading.Thread(
            target=self.run_command_in_thread, args=(command, result_queue), daemon=True
        )
        thread.start()

        def check_queue_periodically() -> None:
            try:
                success = result_queue.get_next()
            except queue.Empty:
                self.after(100, check_queue_periodically)
            else:
                if success:
                    self.after(
                        0,
                        lambda: messagebox.showinfo("Success", "Download successful!"),
                    )
            finally:
                self.download_button.config(state="normal")

        check_queue_periodically()

    def download_video(self) -> None:
        selected_platform = self.platform_cbx.get()
        url = self.url_entry.get()
        output_dir = filedialog.askdirectory(title="Select output folder")

        if not url or not output_dir:
            messagebox.showerror(
                "Error", "Please provide the video URL and select a folder!"
            )
            return

        yt_dlp_path = self._get_executable(ExecutableName.YT_DLP)
        ffmpeg_path = self._get_executable(ExecutableName.FFMPEG)

        if not yt_dlp_path or not ffmpeg_path:
            return

        output_template = os.path.join(output_dir, "%(title)s.%(ext)s")

        try:
            if selected_platform == SupportedPlatform.YT:
                cmd = self._build_youtube_command(
                    yt_dlp_path=yt_dlp_path,
                    ffmpeg_path=ffmpeg_path,
                    output_template=output_template,
                    url=url,
                )
            elif selected_platform == SupportedPlatform.VIMEO:
                cmd = self._build_vimeo_command(
                    yt_dlp_path=yt_dlp_path, url=url, output_template=output_template
                )
            else:
                messagebox.showerror(
                    "Error", f"Unsupported platform: {selected_platform}"
                )
                return
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        self._run_download_command(cmd)

    def on_platform_change(self, event: Event[ttk.Combobox]) -> None:
        event.widget.selection_clear()
        selected_platform = self.platform_cbx.get()

        if selected_platform == SupportedPlatform.VIMEO:
            self.resolution_label.pack_forget()
            self.resolution_combobox.pack_forget()
            self.start_label.pack_forget()
            self.start_time_entry.pack_forget()
            self.end_label.pack_forget()
            self.end_time_entry.pack_forget()
            self.crf_label.pack_forget()
            self.crf_combobox.pack_forget()
            self.auth_switch.pack_forget()

            self.url_entry.delete(0, tk.END)
            self.resolution_combobox["values"] = []
            self.resolution_combobox.set("")

        elif selected_platform == SupportedPlatform.YT:
            self.resolution_label.pack(pady=5, before=self.download_button)
            self.resolution_combobox.pack(pady=5, before=self.download_button)
            self.start_label.pack(pady=5, before=self.download_button)
            self.start_time_entry.pack(pady=5, before=self.download_button)
            self.end_label.pack(pady=5, before=self.download_button)
            self.end_time_entry.pack(pady=5, before=self.download_button)
            self.crf_label.pack(pady=5, before=self.download_button)
            self.crf_combobox.pack(pady=5, before=self.download_button)
            self.auth_switch.pack(pady=5, before=self.download_button)

        self.download_button.config(state="disabled")
        self.update_idletasks()

    def on_resolution_change(self, event: Event[ttk.Combobox]) -> None:
        event.widget.selection_clear()

    def on_crf_change(self, event: Event[ttk.Combobox]) -> None:
        event.widget.selection_clear()


if __name__ == "__main__":
    app = VideoDownloader()
    app.mainloop()
