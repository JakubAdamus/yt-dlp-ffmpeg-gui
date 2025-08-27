import os
import sys
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


class YTDLPStatus:
    _updated = False  

    @staticmethod
    def is_updated() -> bool:
        return YTDLPStatus._updated

    @staticmethod
    def update(status: bool):
        YTDLPStatus._updated = status
        
download_queue = queue.Queue()

class Operation(Enum):
    VIDEO_DOWNLOAD = 1
    UPDATE_YTDP = 2
    GET_YTDLP_VERSION = 3

def download_file(url: str, dest: str):
    top = tk.Toplevel(root)
    top.title("Pobieranie")
    tk.Label(top, text=f"Pobieranie {os.path.basename(dest)}").pack(pady=10)
    progress = ttk.Progressbar(top, length=300, mode="determinate", maximum=100)
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
                            root.after(0, progress.config, {"value": percent})

           
            download_queue.put({"success": True, "file_path": dest, "top_window": top})
        except Exception as e:
            download_queue.put({"success": False, "error_message": str(e), "top_window": top})
    
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    
    check_queue_periodically()

def check_queue_periodically():
    try:
        
        result = download_queue.get(block=False) 
        top_window = result.get("top_window")
        
        if result["success"]:
            file_path = result['file_path']
            messagebox.showinfo("Sukces", f"Pobrano plik: {file_path}")
            top_window.destroy()
            
            file_name = os.path.basename(file_path)
            if file_name == "yt-dlp.exe":
                YTDLPStatus.update(True)
            elif file_name == "ffmpeg.zip":
                def extract_ffmpeg():
                    try:
                        base_path = os.path.dirname(os.path.abspath(sys.executable))
                        with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        
                            for member in zip_ref.namelist():
                                if member.replace("\\", "/").endswith("bin/ffmpeg.exe"):
                                    zip_ref.extract(member, path=base_path)
                                    src = os.path.join(base_path, member)
                                    os.replace(src, get_path("ffmpeg.exe"))
                                    break
                                
                        os.remove(file_path)
                        
                        for item in os.listdir(base_path):
                            folder_path = os.path.join(base_path, item)
                            if os.path.isdir(folder_path) and item.startswith("ffmpeg"):
                                shutil.rmtree(folder_path)

                        messagebox.showinfo("Gotowe", "ffmpeg.exe został pobrany")
                
                    except Exception as e:
                        messagebox.showerror("Błąd", f"Nie udało się wyciągnąć ffmpeg: {e}")

                thread = threading.Thread(target=extract_ffmpeg, daemon=True)
                thread.start()
            
        else:
            messagebox.showerror("Błąd", f"Nie udało się pobrać pliku: {result['error_message']}")
            top_window.destroy()
            
    except queue.Empty:
        root.after(100, check_queue_periodically)

def show_message(result_queue: queue.Queue):
    if result_queue.empty():
        return
    
    status, title, message = result_queue.get()
    if status:
        messagebox.showinfo(title, message)
    else:
        messagebox.showerror(title, message)


def get_path(executable: str) -> str:
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.join(exe_dir, executable)

def check_dependencies():
    yt_dlp_path = get_path("yt-dlp.exe")
    
    if not os.path.exists(yt_dlp_path):
        messagebox.showinfo("Instalacja", "Pobieram yt-dlp.exe...")
        download_file(
            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
            yt_dlp_path
        )
    else:
        command = [get_path("yt-dlp.exe"), "-U"]
        result_queue = queue.Queue()
        thread = threading.Thread(target=run_command, args=(command, Operation.UPDATE_YTDP, result_queue,), daemon=True)
        thread.start()
        thread.join()
        show_message(result_queue)
            
    ffmpeg_path = get_path("ffmpeg.exe")
    if not os.path.exists(ffmpeg_path):
        base_path = os.path.dirname(os.path.abspath(sys.executable))
        archive_path = os.path.join(base_path, "ffmpeg.zip")
        
        messagebox.showinfo("Instalacja", "Pobieram ffmpeg.exe...")
        download_file(
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            archive_path
        ) 
        
         
def run_command(command, operation: Operation, result_queue: queue):
    try:
        kwargs = {"check": True}
        
        if sys.platform == "win32" and operation in (Operation.UPDATE_YTDP, Operation.GET_YTDLP_VERSION):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = creationflags
            
        if operation == Operation.GET_YTDLP_VERSION:
            kwargs["capture_output"] = True
            kwargs["text"] = True
           
        if operation == Operation.UPDATE_YTDP:
            old_version = get_ytdlp_version()
            
        result = subprocess.run(command, **kwargs)
        
        if operation == Operation.VIDEO_DOWNLOAD:
            result_queue.put([True,"Sukces", "Pobieranie zakończone sukcesem!"])
            
        elif operation == Operation.GET_YTDLP_VERSION:
            result_queue.put(result.stdout.strip())
            
        elif operation == Operation.UPDATE_YTDP:
            YTDLPStatus.update(True)
            new_version = get_ytdlp_version()
            
            if(new_version != old_version):
                result_queue.put([True, "Aktualizacja zakończona",
                                f"yt-dlp został zaktualizowany.\nWersja: {new_version}"])
        
    except subprocess.CalledProcessError:
        result_queue.put(False, "Błąd", "Wystąpił problem podczas pobierania.")

def get_ytdlp_version():
    result_queue = queue.Queue()
    command = [get_path("yt-dlp.exe"), "--version"]
    thread = threading.Thread(target=run_command, args=(command, Operation.GET_YTDLP_VERSION, result_queue,), daemon=True)
    thread.start()
    thread.join()
    
    return result_queue.get()

def init():
    if not YTDLPStatus.is_updated(): 
        check_dependencies()
        
        
def download_video():
    platform = platform_cbx.get()
    url = url_entry.get()
    start_time = start_time_entry.get()
    end_time = end_time_entry.get()
    authorize = auth_switch_value.get()
    resolution = resolution_combobox.get()
    output_dir = filedialog.askdirectory(title="Wybierz folder zapisu")
    
    if not url or not output_dir:
        messagebox.showerror("Błąd", "Podaj URL i wybierz folder docelowy!")
        return
    
    yt_dlp_path = get_path("yt-dlp.exe")
    ffmpeg_path = get_path("ffmpeg.exe")

    if not os.path.exists(yt_dlp_path):
        messagebox.showerror("Błąd", "Nie znaleziono pliku 'yt-dlp.exe' w folderze aplikacji!")
        return
    if not os.path.exists(ffmpeg_path):
        messagebox.showerror("Błąd", "Nie znaleziono pliku 'ffmpeg.exe' w folderze aplikacji!")
        return

    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
    
    if platform == "YT":
        crf_value = crf_combobox.get()
        format_string = f"bestvideo[height={resolution}]+bestaudio/best"

        command = [
            yt_dlp_path, "-f", format_string, "--merge-output-format", "mp4", "--ffmpeg-location", ffmpeg_path,
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
        vimeo_url = "https://player.vimeo.com/video/" + url.split("https://vimeo.com/")[1].split("?")[0]
        
        command = [
            yt_dlp_path, vimeo_url, 
            "-o", output_template,
            "--referer", "https://www.patreon.com", 
            "--force-overwrite" 
        ]
    
    result_queue = queue.Queue()
    thread = threading.Thread(target=run_command, args=(command, Operation.VIDEO_DOWNLOAD, result_queue,), daemon=True)
    thread.start()
    thread.join()
    show_message(result_queue)
    
    

def on_platform_change(event):
    selected_platform = platform_cbx.get()
    
    if selected_platform == "Vimeo":
        start_label.pack_forget()
        start_time_entry.pack_forget()
        end_label.pack_forget()
        end_time_entry.pack_forget()
        crf_label.pack_forget()
        crf_combobox.pack_forget()
        resolution_label.pack_forget()
        resolution_combobox.pack_forget()
        auth_switch.pack_forget()
        
        start_time_entry.config(state="disabled")
        end_time_entry.config(state="disabled")
        auth_switch.config(state="disabled")
        crf_combobox.config(state="disabled")
        resolution_combobox.config(state="disabled")
        
    elif selected_platform == "YT":
        start_label.pack(pady=5)
        start_time_entry.pack(pady=5)
        end_label.pack(pady=5)
        end_time_entry.pack(pady=5)
        crf_label.pack(pady=5)
        crf_combobox.pack(pady=5)
        resolution_label.pack(pady=5)
        resolution_combobox.pack(pady=5)
        auth_switch.pack(pady=5)
        
        start_time_entry.config(state="normal")
        end_time_entry.config(state="normal")
        auth_switch.config(state="normal")
        crf_combobox.config(state="normal")
        resolution_combobox.config(state="normal")
    
    root.update_idletasks()
        
root = tk.Tk()
root.title("Video Downloader")
root.geometry("400x475")

tk.Label(root, text="Wybierz platformę:").pack(pady=5)
plaftorm_list = ["YT", "Vimeo"]
platform_cbx = ttk.Combobox(root, values=plaftorm_list, state="readonly", width=10)
platform_cbx.set("YT")  
platform_cbx.pack(pady=5)
platform_cbx.bind("<<ComboboxSelected>>", on_platform_change)

tk.Label(root, text="Podaj URL filmu:").pack(pady=5)
url_entry = tk.Entry(root, width=50)
url_entry.pack(pady=5)

start_label = tk.Label(root, text="Początek (hh:mm:ss):")
start_label.pack(pady=5)
start_time_entry = tk.Entry(root, width=20)
start_time_entry.pack(pady=5)

end_label = tk.Label(root, text="Koniec (hh:mm:ss):")
end_label.pack(pady=5)
end_time_entry = tk.Entry(root, width=20)
end_time_entry.pack(pady=5)

resolution_label = tk.Label(root, text="Wybierz rozdzielczość:")
resolution_label.pack(pady=5)
resolution_combobox = ttk.Combobox(root, values=["144", "240", "360", "480", "720", "1080", "1440", "2160"], state="readonly", width=10)
resolution_combobox.set("1080")  
resolution_combobox.pack(pady=5)

crf_label = tk.Label(root, text="Wybierz wartość CRF:")
crf_label.pack(pady=5)
crf_combobox = ttk.Combobox(root, values=[str(i) for i in range(18, 29)], state="readonly", width=10)
crf_combobox.set("23")  
crf_combobox.pack(pady=5)

auth_switch_value = tk.BooleanVar(value=False)
auth_switch = tk.Checkbutton(root, text="Autoryzuj YT", variable=auth_switch_value, onvalue=True, offvalue=False)
auth_switch.pack(pady=5)

download_button = tk.Button(root, text="Pobierz", command=download_video, width=20)
download_button.pack(side="bottom", pady=20)

root.after(100, init)

root.mainloop()
