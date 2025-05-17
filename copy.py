import os
import shutil
import threading
import queue
import psutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import time

# -------- Configuration --------
ESTIMATED_AVG_FILE_SIZE = 500 * 1024 * 1024  # 500 MB average for better thread count estimation

# -------- Globals --------
file_queue = queue.Queue()
progress_queue = queue.Queue()
total_files = 0
copied_files = 0
total_bytes_copied = 0
pause_event = threading.Event()
cancel_event = threading.Event()
start_time = None

# Track thread-specific status messages
thread_status = {}
thread_status_lock = threading.Lock()

# -------- Helpers --------
def get_available_memory():
    return psutil.virtual_memory().available

def estimate_thread_count():
    available_mem = get_available_memory()
    return max(1, min(8, available_mem // ESTIMATED_AVG_FILE_SIZE))  # Limit to 8 threads for large files

def populate_file_queue(source_dir):
    global total_files
    for root_dir, dirs, files in os.walk(source_dir):
        for file in files:
            full_path = os.path.join(root_dir, file)
            file_queue.put(full_path)
    total_files = file_queue.qsize()

# -------- Copy Worker --------
def copy_worker(source_dir, dest_dir):
    global copied_files, total_bytes_copied
    buffer_size = 4 * 1024 * 1024  # 4 MB buffer for faster large file transfer
    thread_name = threading.current_thread().name

    while not cancel_event.is_set():
        pause_event.wait()

        try:
            src_file = file_queue.get_nowait()
        except queue.Empty:
            break

        rel_path = os.path.relpath(src_file, source_dir)
        dest_file = os.path.join(dest_dir, rel_path)
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

                    with thread_status_lock:
                        thread_status[thread_name] = f"{os.path.basename(src_file)} – {copied_size // (1024 * 1024)}MB/{total_size // (1024 * 1024)}MB"

            shutil.copystat(src_file, dest_file)
            total_bytes_copied += copied_size

        except Exception as e:
            with thread_status_lock:
                thread_status[thread_name] = f"Error: {os.path.basename(src_file)}"
        finally:
            copied_files += 1
            progress_queue.put(copied_files)
            with thread_status_lock:
                thread_status[thread_name] = "Idle"
            file_queue.task_done()

# -------- UI Update --------
def format_time(seconds):
    mins, secs = divmod(int(seconds), 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs:02}:{mins:02}:{secs:02}"

def update_ui():
    try:
        while not progress_queue.empty():
            current = progress_queue.get_nowait()
            percent = (current / total_files) * 100 if total_files else 0
            percent_label.config(
                text=f"{current}/{total_files} files copied ({percent:.1f}%)"
            )
            progress_bar['value'] = percent

        elapsed = time.time() - start_time if start_time else 0
        elapsed_label.config(text=f"Elapsed Time: {format_time(elapsed)}")

        if copied_files > 0 and start_time:
            estimated_total = (elapsed / copied_files) * total_files
            remaining = estimated_total - elapsed
            remaining_label.config(text=f"Estimated Remaining: {format_time(remaining)}")
            speed = total_bytes_copied / elapsed if elapsed else 0
            speed_label.config(text=f"Speed: {speed / (1024 * 1024):.2f} MB/s")
        else:
            remaining_label.config(text="Estimated Remaining: --:--:--")
            speed_label.config(text="Speed: -- MB/s")

        with thread_status_lock:
            status_lines = [f"{k}: {v}" for k, v in sorted(thread_status.items())]
        status_label.config(text="\n".join(status_lines[:10]))

    except Exception as e:
        print("UI update error:", e)
    finally:
        if copied_files < total_files and not cancel_event.is_set():
            root.after(200, update_ui)

# -------- Finalize UI --------
def finalize_ui():
    percent_label.config(
        text=f"{copied_files}/{total_files} files copied (100%)"
    )
    progress_bar['value'] = 100
    elapsed = time.time() - start_time if start_time else 0
    elapsed_label.config(text=f"Elapsed Time: {format_time(elapsed)}")
    remaining_label.config(text="Estimated Remaining: 00:00:00")
    speed_label.config(text="Speed: -- MB/s")
    root.title("File Copier – 100% Complete")
    status_label.config(text="Copy completed!")
    start_button.config(state=tk.NORMAL)
    pause_button.config(state=tk.DISABLED)
    resume_button.config(state=tk.DISABLED)
    cancel_button.config(state=tk.DISABLED)
    messagebox.showinfo("Done", "All files copied successfully!")

# -------- Button Actions --------
def browse_source():
    directory = filedialog.askdirectory(title="Select Source Directory")
    if directory:
        source_dir_var.set(directory)

def browse_dest():
    directory = filedialog.askdirectory(title="Select Destination Directory")
    if directory:
        dest_dir_var.set(directory)

def start_copy():
    global copied_files, start_time, total_bytes_copied, total_files
    copied_files = 0
    total_bytes_copied = 0
    start_time = time.time()

    source_dir = source_dir_var.get()
    dest_dir = dest_dir_var.get()

    if not os.path.exists(source_dir) or not os.path.exists(dest_dir):
        messagebox.showerror("Error", "Please select valid source and destination directories.")
        return

    start_button.config(state=tk.DISABLED)
    pause_button.config(state=tk.NORMAL)
    resume_button.config(state=tk.DISABLED)
    cancel_button.config(state=tk.NORMAL)

    cancel_event.clear()
    pause_event.set()

    populate_file_queue(source_dir)
    if total_files == 0:
        messagebox.showinfo("Info", "No files to copy.")
        start_button.config(state=tk.NORMAL)
        pause_button.config(state=tk.DISABLED)
        resume_button.config(state=tk.DISABLED)
        cancel_button.config(state=tk.DISABLED)
        return

    status_label.config(text=f"Starting... {total_files} files found")
    num_threads = estimate_thread_count()

    threads = []
    for i in range(num_threads):
        name = f"Worker-{i+1}"
        with thread_status_lock:
            thread_status[name] = "Waiting..."
        t = threading.Thread(target=copy_worker, args=(source_dir, dest_dir), daemon=True, name=name)
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
    elapsed_label.config(text="Elapsed Time: 00:00:00")
    remaining_label.config(text="Estimated Remaining: --:--:--")
    speed_label.config(text="Speed: -- MB/s")
    start_button.config(state=tk.NORMAL)
    pause_button.config(state=tk.DISABLED)
    resume_button.config(state=tk.DISABLED)
    cancel_button.config(state=tk.DISABLED)
    root.title("File Copier – Cancelled")

# -------- GUI Setup --------
root = tk.Tk()
root.title("File Copier")
root.geometry("600x500")
root.resizable(False, False)

source_dir_var = tk.StringVar()
dest_dir_var = tk.StringVar()

frame = ttk.Frame(root, padding=10)
frame.pack(fill=tk.BOTH, expand=True)

source_frame = ttk.Frame(frame)
source_frame.pack(fill=tk.X, pady=5)
ttk.Label(source_frame, text="Source Directory:").pack(side=tk.LEFT)
ttk.Entry(source_frame, textvariable=source_dir_var, width=40).pack(side=tk.LEFT, padx=5)
ttk.Button(source_frame, text="Browse", command=browse_source).pack(side=tk.LEFT)

dest_frame = ttk.Frame(frame)
dest_frame.pack(fill=tk.X, pady=5)
ttk.Label(dest_frame, text="Destination Directory:").pack(side=tk.LEFT)
ttk.Entry(dest_frame, textvariable=dest_dir_var, width=40).pack(side=tk.LEFT, padx=5)
ttk.Button(dest_frame, text="Browse", command=browse_dest).pack(side=tk.LEFT)

progress_bar = ttk.Progressbar(frame, length=500)
progress_bar.pack(pady=10)

percent_label = ttk.Label(frame, text="0/0 files copied (0%)", font=("Segoe UI", 10))
percent_label.pack()

elapsed_label = ttk.Label(frame, text="Elapsed Time: 00:00:00", font=("Segoe UI", 10))
elapsed_label.pack()

remaining_label = ttk.Label(frame, text="Estimated Remaining: --:--:--", font=("Segoe UI", 10))
remaining_label.pack()

speed_label = ttk.Label(frame, text="Speed: -- MB/s", font=("Segoe UI", 10))
speed_label.pack()

status_label = ttk.Label(frame, text="Status: Idle", font=("Segoe UI", 10), anchor="w", justify="left")
status_label.pack(fill=tk.X, pady=5)

button_frame = ttk.Frame(frame)
button_frame.pack(pady=10)

start_button = ttk.Button(button_frame, text="Start Copy", command=start_copy)
start_button.pack(side=tk.LEFT, padx=5)

pause_button = ttk.Button(button_frame, text="Pause", command=pause_copy, state=tk.DISABLED)
pause_button.pack(side=tk.LEFT, padx=5)

resume_button = ttk.Button(button_frame, text="Resume", command=resume_copy, state=tk.DISABLED)
resume_button.pack(side=tk.LEFT, padx=5)

cancel_button = ttk.Button(button_frame, text="Cancel", command=cancel_copy, state=tk.DISABLED)
cancel_button.pack(side=tk.LEFT, padx=5)

root.mainloop()

