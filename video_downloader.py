import json
import os
import sys
import platform
import ctypes as ct
import re
import subprocess
import threading
import tkinter as tk
import requests
import zipfile
import shutil
import queue
from tkinter import filedialog, messagebox
from tkinter import ttk
from enum import Enum

class Operation(Enum):
    VIDEO_DOWNLOAD = 1
    UPDATE_YTDP = 2
    GET_YTDLP_VERSION = 3
    GET_VIDEO_INFO = 4


class MessageQueue(queue.Queue):
    def put(self, status: bool, title: str, message: str, block=True, timeout=None):
        super().put((status, title, message), block=block, timeout=timeout)

    def show_next(self):
        if self.empty():
            raise queue.Empty
        
        status, title, message = self.get()
        
        if status:
            if title and message:   
                messagebox.showinfo(title, message)
            
        else:
            messagebox.showerror(title, message)
            
            
def dark_title_bar(window):
        
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

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        set_window_attribute = ct.windll.dwmapi.DwmSetWindowAttribute
        get_parent = ct.windll.user32.GetParent
        hwnd = get_parent(window.winfo_id())

        value = ct.c_int(2)  
        set_window_attribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ct.byref(value),
            ct.sizeof(value)
        )

def validate_regex(value, pattern):
    value = value.strip()
    return re.fullmatch(pattern, value) is not None

class VideoDownloader(tk.Tk):
    def __init__(self):
        super().__init__()
        self.download_queue = queue.Queue()
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
            background=[
                ("active", self.active_bg),
                ("disabled", self.disabled_bg)   
            ],
            foreground=[
                ("active", self.text_fg),
                ("disabled", self.disabled_fg)  
            ],
        )

        self.style.configure(
            "TCombobox",
            fieldbackground=self.widget_bg,
            background=self.widget_bg,
            foreground=self.text_fg,
            arrowcolor=self.text_fg
        )
        
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.widget_bg), ("active", self.active_bg)],
            foreground=[("readonly", self.text_fg), ("active", self.text_fg)],
            background=[("readonly", self.widget_bg), ("active", self.active_bg)]
        )

        entry_opts = {
            "bg": self.widget_bg, "fg": self.text_fg,
            "insertbackground": self.text_fg,  
            "highlightthickness": 0,
            "relief": "flat",
            "disabledbackground": self.disabled_bg,  
            "disabledforeground": self.disabled_fg,  
        }

        ttk.Label(self, text="Select platform:").pack(pady=5)
        plaftorm_list = ["YT", "Vimeo"]
        self.platform_cbx = ttk.Combobox(self, values=plaftorm_list, state="readonly", width=10)
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
        self.crf_combobox = ttk.Combobox(self, values=[i for i in range(18, 29)], state="readonly", width=10)
        self.crf_combobox.set("23")
        self.crf_combobox.pack(pady=5)
        self.crf_combobox.bind("<<ComboboxSelected>>", self.on_crf_change)

        self.auth_switch_value = tk.BooleanVar(value=False)
        self.auth_switch = tk.Checkbutton(
            self, text="Authorize YT", variable=self.auth_switch_value,
            onvalue=True, offvalue=False, 
            bg=self.dark_bg, fg=self.text_fg, selectcolor=self.dark_bg,
            activebackground=self.dark_bg, activeforeground=self.text_fg
        )
        self.auth_switch.pack(pady=5)

        self.download_button = ttk.Button(
            self, 
            text="Download Video", 
            command=self.download_video, 
            width=20,
            state="disabled"
        )
        self.download_button.pack(side="bottom", pady=20)

        self.after(100, self.check_dependencies)
        
    def validate_url(self, url):
        if not url:
            return False
        
        url_regex = r"^https?://[^\s/$.?#].[^\s]*$"
        return validate_regex(url, url_regex)

    def update_resolutions(self, event):
        url = self.url_entry.get()
        platform = self.platform_cbx.get()
       
        if not self.validate_url(url):
            self.download_button.config(state="disabled")
            return
        else:
            self.download_button.config(state="normal")
        
        if platform == "YT":
            result_queue = queue.Queue()
            
            command = [self.get_path("yt-dlp.exe"), "-j", url]
            
            self.download_button.config(state="disabled")
            thread = threading.Thread(
                target=self.run_command, 
                args=(command, Operation.GET_VIDEO_INFO, result_queue), 
                daemon=True
            )
            thread.start()
            
            def check_queue_periodically():
                try:
                    info: dict = result_queue.get_nowait()
                    resolutions = set()
                    if 'formats' in info:
                        for f in info['formats']:
                            height = f.get('height')
                            if height is not None and height >= 144:
                                resolutions.add(f['height'])
                     
                    if resolutions:
                        res_list = [str(r) for r in sorted(resolutions)]
                        self.resolution_combobox['values'] = res_list
                    
                        self.resolution_combobox.set(res_list[-1])
                        self.download_button.config(state="normal")
                    else:
                        messagebox.showerror("No resolution available", "Failed to retrieve video information.")
                        self.resolution_combobox['values'] = []
                        
                except queue.Empty:
                    self.after(100, check_queue_periodically)
                    
            check_queue_periodically()    
      
    def get_path(self, executable: str) -> str:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        
        return os.path.join(exe_dir, executable)


    def run_command(self, command, operation: Operation, result_queue: queue.Queue):
        try:
            kwargs = {"check": True}
            
            if sys.platform == "win32" and operation in (
                Operation.UPDATE_YTDP, 
                Operation.GET_YTDLP_VERSION, 
                Operation.GET_VIDEO_INFO
            ):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creationflags = subprocess.CREATE_NO_WINDOW
                kwargs["startupinfo"] = startupinfo
                kwargs["creationflags"] = creationflags

            if operation in (Operation.GET_YTDLP_VERSION, Operation.GET_VIDEO_INFO):
                kwargs["capture_output"] = True
                kwargs["text"] = True

            if operation == Operation.UPDATE_YTDP:
                old_version = self.get_ytdlp_version()

            result = subprocess.run(command, **kwargs)

            if operation == Operation.VIDEO_DOWNLOAD:
                result_queue.put(True, "Success", "Download successful!")
                
            elif operation == Operation.GET_YTDLP_VERSION:
                result_queue.put(result.stdout.strip())
                
            elif operation == Operation.GET_VIDEO_INFO:
                result_queue.put(json.loads(result.stdout))
                
            elif operation == Operation.UPDATE_YTDP:
                new_version = self.get_ytdlp_version()
                
                if new_version != old_version:
                    result_queue.put(
                        True, 
                        "Update completed", 
                        f"yt-dlp has been updated.\nVersion: {new_version}"
                    )
                else:
                    result_queue.put(True, "", "")
                    
        except subprocess.CalledProcessError:
            result_queue.put(False, "Error", "Download failed.")


    def get_ytdlp_version(self):
        result_queue = queue.Queue()
        command = [self.get_path("yt-dlp.exe"), "--version"]
        
        thread = threading.Thread(
            target=self.run_command, 
            args=(command, Operation.GET_YTDLP_VERSION, result_queue), 
            daemon=True
        )
        thread.start()
        thread.join()
        
        return result_queue.get()
    
    def check_dependencies(self):
        yt_dlp_path = self.get_path("yt-dlp.exe")
        
        if not os.path.exists(yt_dlp_path):
            messagebox.showinfo("Installation", "Downloading yt-dlp.exe...")
            self.download_file(
                "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe", 
                yt_dlp_path
            )
            
        else:
            command = [yt_dlp_path, "-U"]
            messageQueue = MessageQueue()
            self.url_entry.config(state="disabled")
            
            thread = threading.Thread(
                target=self.run_command, 
                args=(command, Operation.UPDATE_YTDP, messageQueue), 
                daemon=True
            )
            thread.start()
            
            def check_queue_periodically():
                try:
                    messageQueue.show_next()
                    self.url_entry.config(state="normal")
                except queue.Empty:
                    self.after(100, check_queue_periodically)
            
            check_queue_periodically()

        ffmpeg_path = self.get_path("ffmpeg.exe")
        
        if not os.path.exists(ffmpeg_path):
            base_path = os.path.dirname(os.path.abspath(sys.executable))
            archive_path = os.path.join(base_path, "ffmpeg.zip")
            
            messagebox.showinfo("Installation", "Downloading ffmpeg.exe...")
            self.download_file(
                "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", 
                archive_path
            )


    def download_file(self, url: str, dest: str):
        top = tk.Toplevel(self)
        top.title("Downloading")
        top.configure(bg=self.dark_bg)
        self.after(10, lambda: dark_title_bar(top))
        green = "#00aa00"  

        tk.Label(top, text=f"Downloading {os.path.basename(dest)}",fg=self.text_fg, bg=self.dark_bg).pack(pady=10)
        
        self.style.configure("Dark.Horizontal.TProgressbar",
                troughcolor=self.widget_bg,  
                background=green,    
                bordercolor=self.widget_bg,
                lightcolor=green,
                darkcolor=green)

        progress = ttk.Progressbar(top, length=300, mode="determinate", maximum=100, style="Dark.Horizontal.TProgressbar")
        progress.pack(pady=10)

        def worker():
            try:
                response = requests.get(url, stream=True)
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
                                
                self.download_queue.put({"success": True, "file_path": dest, "top_window": top})
                
            except Exception as e:
                self.download_queue.put({"success": False, "error_message": str(e), "top_window": top})

        self.url_entry.config(state="disabled")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        
        def check_queue_periodically():
            try:
                result = self.download_queue.get_nowait()
                top_window = result.get("top_window")
                
                if result["success"]:
                    file_path = result['file_path']
                    messagebox.showinfo("Success", f"File downloaded: {file_path}")
                    top_window.destroy()
                    
                    file_name = os.path.basename(file_path)
                    
                    if file_name == "ffmpeg.zip":
                        def extract_ffmpeg():
                            try:
                                base_path = os.path.dirname(os.path.abspath(sys.executable))
                                
                                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                                    for member in zip_ref.namelist():
                                        if member.replace("\\", "/").endswith("bin/ffmpeg.exe"):
                                            zip_ref.extract(member, path=base_path)
                                            src = os.path.join(base_path, member)
                                            os.replace(src, self.get_path("ffmpeg.exe"))
                                            break
                                        
                                os.remove(file_path)
                                
                                for item in os.listdir(base_path):
                                    folder_path = os.path.join(base_path, item)
                                    if os.path.isdir(folder_path) and item.startswith("ffmpeg"):
                                        shutil.rmtree(folder_path)

                                messagebox.showinfo("Done", "ffmpeg.exe has been extracted.")
                                self.url_entry.config(state="normal")
                        
                            except Exception as e:
                                messagebox.showerror("Error", f"Failed to extract ffmpeg: {e}")

                        thread = threading.Thread(target=extract_ffmpeg, daemon=True)
                        thread.start()
                    
                else:
                    messagebox.showerror("Error", f"Failed to download file: {result['error_message']}")
                    top_window.destroy()
                    
            except queue.Empty:
                self.after(100, check_queue_periodically)
        
        check_queue_periodically()

    def download_video(self):
        platform = self.platform_cbx.get()
        url = self.url_entry.get()
        start_time = self.start_time_entry.get()
        end_time = self.end_time_entry.get()
        authorize = self.auth_switch_value.get()
        resolution = self.resolution_combobox.get()
        output_dir = filedialog.askdirectory(title="Select output folder")

        if not url or not output_dir:
            messagebox.showerror("Error", "Please provide the video URL and select a destination folder!")
            return

        yt_dlp_path = self.get_path("yt-dlp.exe")
        ffmpeg_path = self.get_path("ffmpeg.exe")

        if not os.path.exists(yt_dlp_path):
            messagebox.showerror("Error", "File 'yt-dlp.exe' not found in the application folder!")
            return
        
        if not os.path.exists(ffmpeg_path):
            messagebox.showerror("Error", "File 'ffmpeg.exe' not found in the application folder!")
            return

        output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
        
        if platform == "YT":
            crf_value = self.crf_combobox.get()
            format_string = f"bestvideo[height={resolution}]+bestaudio/best"
            
            command = [
                yt_dlp_path, 
                "-f", format_string, 
                "--merge-output-format", "mp4", 
                "--ffmpeg-location", ffmpeg_path,
                "-o", output_template, url,
                "--postprocessor-args", f"-c:v libx264 -crf {crf_value}",
                "--force-overwrite"
            ]
            
            if start_time and end_time:
                command.extend(["--download-sections", f"*{start_time}-{end_time}", "--force-keyframes-at-cuts"])
                
            elif start_time:
                command.extend(["--download-sections", f"*{start_time}-", "--force-keyframes-at-cuts"])
                
            elif end_time:
                command.extend(["--download-sections", f"*-{end_time}", "--force-keyframes-at-cuts"])
                
            if authorize:
                command.extend(["--cookies-from-browser", "firefox"])
                
        elif platform == "Vimeo":
            match = re.match(r"^https://vimeo\.com/(\d+)", url)
            
            if not match:
                messagebox.showerror("Error", "Invalid Vimeo URL")
                return

            video_id = match.group(1)
            vimeo_url = f"https://player.vimeo.com/video/{video_id}"
            
            command = [
                yt_dlp_path, vimeo_url, 
                "-o", output_template, 
                "--referer", "https://www.patreon.com", 
                "--force-overwrite"
            ]

        messageQueue = MessageQueue()
        self.download_button.config(state="disabled")
        
        thread = threading.Thread(
            target=self.run_command, 
            args=(command, Operation.VIDEO_DOWNLOAD, messageQueue), 
            daemon=True
        )
        thread.start()
        
        def check_queue_periodically():
            try:
                messageQueue.show_next()
                self.download_button.config(state="normal")
            except queue.Empty:
                self.after(100, check_queue_periodically)
        
        check_queue_periodically()

    def on_platform_change(self, event):
        event.widget.selection_clear()
        selected_platform = self.platform_cbx.get()
        
        if selected_platform == "Vimeo":
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
            
        elif selected_platform == "YT":
            self.resolution_label.pack(pady=5, before=self.download_button)
            self.resolution_combobox.pack(pady=5, before=self.download_button)
            self.start_label.pack(pady=5, before=self.download_button)
            self.start_time_entry.pack(pady=5, before=self.download_button)
            self.end_label.pack(pady=5, before=self.download_button)
            self.end_time_entry.pack(pady=5, before=self.download_button)
            self.crf_label.pack(pady=5, before=self.download_button)
            self.crf_combobox.pack(pady=5, before=self.download_button)
            self.auth_switch.pack(pady=5, before=self.download_button)
            
        self.update_idletasks()
        
    def on_resolution_change(self, event):
        event.widget.selection_clear()
           
    def on_crf_change(self, event):
        event.widget.selection_clear()
    
        
if __name__ == "__main__":
    app = VideoDownloader()
    app.mainloop()
