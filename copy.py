import os
import shutil
import threading
import queue
import psutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import time

# -------- Globals --------
SOURCE_DIR = ""
DEST_DIR = ""
ESTIMATED_AVG_FILE_SIZE = 500 * 1024 * 1024  # 500 MB average for thread estimation

file_queue = queue.Queue()
progress_queue = queue.Queue()
total_files = 0
copied_files = 0
pause_event = threading.Event()
cancel_event = threading.Event()

thread_status = {}
thread_status_lock = threading.Lock()

# -------- Helpers --------
def get_available_memory():
    return psutil.virtual_memory().available

def estimate_thread_count():
    available_mem = get_available_memory()
    return max(1, min(8, available_mem // ESTIMATED_AVG_FILE_SIZE))

def populate_file_queue():
    global total_files
    for root, dirs, files in os.walk(SOURCE_DIR):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, SOURCE_DIR)
            dest_path = os.path.join(DEST_DIR, rel_path)
            if os.path.exists(dest_path):
                try:
                    if os.path.getsize(full_path) == os.path.getsize(dest_path):
                        continue  # Skip if same size exists
                except:
                    continue
            file_queue.put(full_path)
    total_files = file_queue.qsize()

# -------- Copy Worker --------
def copy_worker():
    global copied_files
    buffer_size = 4 * 1024 * 1024
    thread_name = threading.current_thread().name

    while not cancel_event.is_set():
        pause_event.wait()

        try:
            src_file = file_queue.get_nowait()
        except queue.Empty:
            break

        rel_path = os.path.relpath(src_file, SOURCE_DIR)
        dest_file = os.path.join(DEST_DIR, rel_path)
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)

        try:
            total_size = os.path.getsize(src_file)
            copied_size = 0

            with open(src_file, 'rb') as src, open(dest_file, 'wb') as dst:
                while not cancel_event.is_set():
                    pause_event.wait()
                    chunk = src.read(buffer_size)
                    if not chunk:
                        break
                    dst.write(chunk)
                    copied_size += len(chunk)
                    percent = (copied_size / total_size) * 100 if total_size else 100
                    with thread_status_lock:
                        thread_status[thread_name] = f"{os.path.basename(src_file)} – {percent:.1f}%"

            shutil.copystat(src_file, dest_file)

        except Exception:
            with thread_status_lock:
                thread_status[thread_name] = f"Error: {os.path.basename(src_file)}"
        finally:
            copied_files += 1
            progress_queue.put(copied_files)
            with thread_status_lock:
                thread_status[thread_name] = "Idle"
            file_queue.task_done()

# -------- UI Update --------
def update_ui():
    try:
        while not progress_queue.empty():
            current = progress_queue.get_nowait()
            percent = (current / total_files) * 100 if total_files else 0
            percent_label.config(text=f"{current}/{total_files} files copied ({percent:.1f}%)")
            progress_bar['value'] = percent
            root.title(f"File Copier – {percent:.1f}% Complete")

        with thread_status_lock:
            lines = [f"{k}: {v}" for k, v in sorted(thread_status.items())]
        status_label.config(text="\n".join(lines[:10]))

    except Exception as e:
        print("UI update error:", e)
    finally:
        if copied_files < total_files and not cancel_event.is_set():
            root.after(200, update_ui)

# -------- Finalize UI --------
def finalize_ui():
    percent_label.config(text=f"{copied_files}/{total_files} files copied (100%)")
    progress_bar['value'] = 100
    root.title("File Copier – 100% Complete")
    status_label.config(text="Copy completed!")
    start_button.config(state=tk.NORMAL)
    pause_button.config(state=tk.DISABLED)
    resume_button.config(state=tk.DISABLED)
    cancel_button.config(state=tk.DISABLED)
    messagebox.showinfo("Done", "All files copied successfully!")

# -------- Button Actions --------
def start_copy():
    global copied_files, SOURCE_DIR, DEST_DIR
    copied_files = 0

    if not SOURCE_DIR or not DEST_DIR or not os.path.exists(SOURCE_DIR) or not os.path.exists(DEST_DIR):
        messagebox.showerror("Error", "Please select valid source and destination folders.")
        return

    start_button.config(state=tk.DISABLED)
    pause_button.config(state=tk.NORMAL)
    resume_button.config(state=tk.DISABLED)
    cancel_button.config(state=tk.NORMAL)

    cancel_event.clear()
    pause_event.set()

    populate_file_queue()
    if total_files == 0:
        messagebox.showinfo("Info", "No files to copy.")
        return

    status_label.config(text=f"Starting... {total_files} files found")
    num_threads = estimate_thread_count()

    threads = []
    for i in range(num_threads):
        name = f"Worker-{i+1}"
        with thread_status_lock:
            thread_status[name] = "Waiting..."
        t = threading.Thread(target=copy_worker, daemon=True, name=name)
        t.start()
        threads.append(t)

    def wait_for_completion():
        file_queue.join()
        root.after(0, finalize_ui)

    threading.Thread(target=wait_for_completion, daemon=True).start()
    root.after(100, update_ui)

def pause_copy():
    pause_event.clear()
    status_label.config(text="Paused...")
    pause_button.config(state=tk.DISABLED)
    resume_button.config(state=tk.NORMAL)

def resume_copy():
    pause_event.set()
    status_label.config(text="Resuming...")
    pause_button.config(state=tk.NORMAL)
    resume_button.config(state=tk.DISABLED)

def cancel_copy():
    cancel_event.set()
    pause_event.set()
    with file_queue.mutex:
        file_queue.queue.clear()
    status_label.config(text="Cancelling...")
    progress_bar['value'] = 0
    percent_label.config(text="0/0 files copied (0%)")
    start_button.config(state=tk.NORMAL)
    pause_button.config(state=tk.DISABLED)
    resume_button.config(state=tk.DISABLED)
    cancel_button.config(state=tk.DISABLED)
    root.title("File Copier – Cancelled")

def select_source():
    global SOURCE_DIR
    folder = filedialog.askdirectory()
    if folder:
        SOURCE_DIR = folder
        source_label.config(text=f"Source: {SOURCE_DIR}")

def select_dest():
    global DEST_DIR
    folder = filedialog.askdirectory()
    if folder:
        DEST_DIR = folder
        dest_label.config(text=f"Destination: {DEST_DIR}")

# -------- Monitor Destination --------
def monitor_destination():
    while True:
        if not cancel_event.is_set():
            if DEST_DIR and os.path.exists(DEST_DIR):
                if not pause_event.is_set() and not start_button['state'] == tk.NORMAL:
                    pause_event.set()
                    root.after(0, lambda: status_label.config(text="Resumed after network reconnect."))
            else:
                if pause_event.is_set():
                    pause_event.clear()
                    root.after(0, lambda: status_label.config(text="Network disconnected. Waiting to reconnect..."))
        time.sleep(3)

# -------- GUI Setup --------
root = tk.Tk()
root.title("File Copier")
root.geometry("650x450")
root.resizable(False, False)

frame = ttk.Frame(root, padding=10)
frame.pack(fill=tk.BOTH, expand=True)

ttk.Label(frame, text="Parallel File Copier", font=("Segoe UI", 14)).pack(pady=5)

source_btn = ttk.Button(frame, text="Select Source Folder", command=select_source)
source_btn.pack(pady=2)

source_label = ttk.Label(frame, text="Source: Not selected", font=("Segoe UI", 9), foreground="gray")
source_label.pack()

dest_btn = ttk.Button(frame, text="Select Destination Folder", command=select_dest)
dest_btn.pack(pady=2)

dest_label = ttk.Label(frame, text="Destination: Not selected", font=("Segoe UI", 9), foreground="gray")
dest_label.pack()

progress_bar = ttk.Progressbar(frame, orient="horizontal", length=550, mode="determinate")
progress_bar.pack(pady=10)

percent_label = ttk.Label(frame, text="0/0 files copied (0%)", font=("Segoe UI", 10))
percent_label.pack()

status_label = ttk.Label(frame, text="Status: Waiting", font=("Segoe UI", 10), justify="left")
status_label.pack(pady=5)

button_frame = ttk.Frame(frame)
button_frame.pack(pady=15, fill=tk.X)

buttons = {
    "Start": (start_copy, "start_button"),
    "Pause": (pause_copy, "pause_button"),
    "Resume": (resume_copy, "resume_button"),
    "Cancel": (cancel_copy, "cancel_button"),
}

button_widgets = {}
for i, (label, (command, varname)) in enumerate(buttons.items()):
    btn = ttk.Button(button_frame, text=label, command=command)
    btn.grid(row=0, column=i, padx=10, ipadx=10, sticky="ew")
    button_widgets[varname] = btn

start_button = button_widgets["start_button"]
pause_button = button_widgets["pause_button"]
resume_button = button_widgets["resume_button"]
cancel_button = button_widgets["cancel_button"]

pause_button.config(state=tk.DISABLED)
resume_button.config(state=tk.DISABLED)
cancel_button.config(state=tk.DISABLED)

for i in range(len(buttons)):
    button_frame.columnconfigure(i, weight=1)

# Start monitor thread
monitor_thread = threading.Thread(target=monitor_destination, daemon=True)
monitor_thread.start()

root.mainloop()
