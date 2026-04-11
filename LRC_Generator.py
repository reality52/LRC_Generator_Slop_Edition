import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import whisper
import os
import threading
import sys
import shutil
import subprocess
import torch

# Расширенный импорт для поддержки форматов
try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.wave import WAVE
    from mutagen.easymp4 import EasyMP4
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

WHISPER_LANGS = ["auto", "en", "ru", "zh", "de", "es", "ko", "fr", "ja"]

class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text: return
        x, y, _cx, cy = self.widget.bbox("insert")
        x = x + self.widget.winfo_rootx() + 25
        y = y + cy + self.widget.winfo_rooty() + 25
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                         font=("tahoma", "8", "normal"), padx=5, pady=2)
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        tw = self.tip_window
        self.tip_window = None
        if tw: tw.destroy()

class LRCGeneratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Lyrics Generator Slop Edition")
        self.root.geometry("900x550") # Увеличил высоту для галочки
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        if PYGAME_AVAILABLE:
            pygame.mixer.init()
        
        self.model = None
        self.processing = False
        self.abort_flag = False
        self.current_process = None 
        self.last_separated_path = ""
        
        # Директория для Demucs в папке пользователя (не требует прав админа)
        self.demucs_out_dir = os.path.join(os.path.expanduser("~"), ".lrc_generator_separated")
        
        self.audio_path = tk.StringVar()
        self.model_size = tk.StringVar(value="small")
        self.selected_lang = tk.StringVar(value="auto")
        self.use_demucs = tk.BooleanVar(value=True)
        self.include_meta = tk.BooleanVar(value=True)
        
        self.track_title = tk.StringVar()
        self.artist = tk.StringVar()
        self.album = tk.StringVar() 
        self.created_by = tk.StringVar(value="Reality52")
        self.offset = tk.IntVar(value=0)
        
        self.progress_var = tk.DoubleVar()
        self.segments_data = []
        self.is_playing = False
        self.is_paused = False
        self.audio_length_sec = 0.0
        self.play_offset_sec = 0.0
        self.is_seeking = False
        self.success_flag = False
        
        self.models_cache_path = self.get_whisper_cache_path()
        
        self.create_widgets()
        self.update_model_info()
    
    def on_closing(self):
        self.stop_processing_action(ask=False)
        if PYGAME_AVAILABLE:
            try: pygame.mixer.music.stop(); pygame.mixer.quit()
            except: pass
        if os.path.exists(self.demucs_out_dir):
            try: shutil.rmtree(self.demucs_out_dir, ignore_errors=True)
            except: pass
        self.root.destroy()

    def get_whisper_cache_path(self):
        base = os.environ.get("XDG_CACHE_HOME", os.path.join(os.environ.get("USERPROFILE" if sys.platform=="win32" else "HOME", ""), ".cache"))
        return os.path.join(base, "whisper")

    def open_folder(self, path):
        if path and os.path.exists(path): os.startfile(path)

    def clear_demucs_folder(self):
        if os.path.exists(self.demucs_out_dir):
            try:
                shutil.rmtree(self.demucs_out_dir, ignore_errors=True)
                self.last_separated_path = ""
                self.btn_open_folder.config(state=tk.DISABLED)
                messagebox.showinfo("Очистка", "Рабочая папка Demucs очищена.")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось очистить: {e}")
        else:
            messagebox.showinfo("Очистка", "Папка уже пуста.")

    def show_help(self):
        help_text = (
            "Инструкция по использованию:\n\n\n"
            "1. Выберите аудиофайл нажав '...'.\n\n"
            "2. Demucs.\n\n"
            "Шикарно отделяет вокал от фоновых инструментов и шумов для чистого распознавания речи.\n\n"
            "3. Выберите модель Whisper и язык распознавания.\n\n"
            "'Small' — в большинстве случаев достаточна.\n\n"
            "4. Нажмите 'СТАРТ' и ждите.\n\n" 
            "Если кэш моделей Whisper пуст, то придется подождать загрузки выбранной модели из Интернета.\n\n"
            "Кнопка 'СТОП' прервет процесс в любой момент.\n\n"
            "5. Проверьте, отредактируйте и сохраните результат через кнопку 'СОХРАНИТЬ ФАЙЛ'."
        )
        messagebox.showinfo("Справка", help_text)

    def create_widgets(self):
        header_frame = ttk.Frame(self.root)
        header_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(header_frame, text="?", width=3, command=self.show_help).pack(side=tk.RIGHT)

        main_frame = ttk.Frame(self.root, padding=5)
        main_frame.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main_frame, width=400)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        left_panel.pack_propagate(False)

        # 1. Файл
        f_box = ttk.LabelFrame(left_panel, text=" 1. Аудиофайл ", padding=5)
        f_box.pack(fill=tk.X, pady=2)
        ttk.Entry(f_box, textvariable=self.audio_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn_sel = ttk.Button(f_box, text="...", command=self.select_file, width=8)
        btn_sel.pack(side=tk.RIGHT, padx=2)
        Tooltip(btn_sel, "Выбрать аудиофайл")

        # 2. Demucs
        d_box = ttk.LabelFrame(left_panel, text=" 2. Demucs (Выделение голоса) ", padding=5)
        d_box.pack(fill=tk.X, pady=2)
        chk_d = ttk.Checkbutton(d_box, text="Очистить вокал (htdemucs_ft)", variable=self.use_demucs)
        chk_d.pack(side=tk.LEFT)
        self.btn_open_folder = ttk.Button(d_box, text="📂 Вокал", command=lambda: self.open_folder(self.last_separated_path), width=8, state=tk.DISABLED)
        self.btn_open_folder.pack(side=tk.RIGHT, padx=2)
        Tooltip(self.btn_open_folder, "Открыть рабочую папку Demucs, где находятся разделённый вокал и инструментал, доступные после обработки")
        self.btn_clear_demucs = ttk.Button(d_box, text="🗑 Очистить", command=self.clear_demucs_folder, width=12)
        self.btn_clear_demucs.pack(side=tk.RIGHT, padx=2)
        Tooltip(self.btn_clear_demucs, "Очистить рабочую папку Demucs, где находятся разделённый вокал и инструментал, если что-то идет не так или не хватает места")
        
        # 3. Whisper
        w_box = ttk.LabelFrame(left_panel, text=" 3. Whisper (Распознавание) ", padding=5)
        w_box.pack(fill=tk.X, pady=2)
        w_grid = ttk.Frame(w_box)
        w_grid.pack(fill=tk.X)
        ttk.Label(w_grid, text="Модель:").grid(row=0, column=0, sticky=tk.W)
        cb_model = ttk.Combobox(w_grid, textvariable=self.model_size, values=["tiny", "base", "small", "medium", "large"], width=7, state="readonly")
        cb_model.grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(w_grid, text="Язык:").grid(row=0, column=2, sticky=tk.W)
        cb_lang = ttk.Combobox(w_grid, textvariable=self.selected_lang, values=WHISPER_LANGS, width=5, state="readonly")
        cb_lang.grid(row=0, column=3, padx=5)

        w_cache_frame = ttk.Frame(w_box)
        w_cache_frame.pack(side=tk.TOP, anchor=tk.E, padx=5, pady=2)
        btn_open_cache = ttk.Button(w_cache_frame, text="📂 Кэш моделей", command=lambda: self.open_folder(self.models_cache_path), width=15)
        btn_open_cache.pack(side=tk.RIGHT, padx=2)
        Tooltip(btn_open_cache, "Открыть локальный кэш скачаных моделей Whisper")
        btn_clear_cache = ttk.Button(w_cache_frame, text="🗑 Очистить", command=self.delete_models, width=12)
        btn_clear_cache.pack(side=tk.RIGHT, padx=2)
        Tooltip(btn_clear_cache, "Очистить ВЕСЬ локальный кэш скачаных моделей Whisper, если что-то идет не так или не хватает места.")
        
        self.model_info_lbl = ttk.Label(w_box, text="Модели в наличии: ...", foreground="gray", font=("Arial", 8))
        self.model_info_lbl.pack(fill=tk.X)
        
        # 4. Метаданные
        m_box = ttk.LabelFrame(left_panel, text=" 4. Метаданные ", padding=5)
        m_box.pack(fill=tk.X, pady=2)
        m_grid = ttk.Frame(m_box)
        m_grid.pack(fill=tk.X)
        fields = [("Трек:", self.track_title), ("Артист:", self.artist), ("Альбом:", self.album), ("Создатель:", self.created_by)]
        for i, (lbl, var) in enumerate(fields):
            ttk.Label(m_grid, text=lbl).grid(row=i, column=0, sticky=tk.W)
            ttk.Entry(m_grid, textvariable=var).grid(row=i, column=1, sticky=tk.EW, padx=5, pady=1)
        m_grid.columnconfigure(1, weight=1)

        cuda_text = f"✅ CUDA: {torch.cuda.get_device_name(0)}" if torch.cuda.is_available() else "❌ CUDA: Не найдено (Работаем на CPU)"
        ttk.Label(m_box, text=cuda_text, font=("Arial", 8, "bold")).pack(anchor=tk.W, pady=2)
        
        # Возвращаем галочку для метаданных
        chk_m = ttk.Checkbutton(m_box, text="Добавлять теги в шапку файла", variable=self.include_meta)
        chk_m.pack(anchor=tk.W)
        Tooltip(chk_m, "Если включено, в начале файла будут строки типа [ar:Artist] [ti:Title]")

        # 5. Управление
        action_frame = ttk.Frame(left_panel)
        action_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=5)
        self.btn_stop = ttk.Button(action_frame, text="🛑 СТОП", command=self.stop_processing_action, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.RIGHT)
        self.btn_process = ttk.Button(action_frame, text="🚀 СТАРТ", command=self.start_processing)
        self.btn_process.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=2)

        self.status_label = ttk.Label(left_panel, text="Готов!", font=("Arial", 9, "bold"))
        self.status_label.pack(side=tk.BOTTOM)
        self.progress_bar = ttk.Progressbar(left_panel, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, side=tk.BOTTOM, pady=2)

        # Правая панель
        right_panel = ttk.Frame(main_frame)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.text_output = tk.Text(right_panel, font=("Consolas", 10), undo=True)
        self.text_output.pack(fill=tk.BOTH, expand=True)
        self.text_output.tag_configure("highlight", background="#CEE5FF")

        p_frame = ttk.Frame(right_panel, padding=(0, 5, 0, 0))
        p_frame.pack(fill=tk.X)
        self.btn_play = ttk.Button(p_frame, text="▶", width=5, command=self.play_audio, state=tk.DISABLED)
        self.btn_play.pack(side=tk.LEFT)
        self.btn_pause = ttk.Button(p_frame, text="⏸", width=5, command=self.pause_audio, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.LEFT, padx=2)
        self.btn_stop_music = ttk.Button(p_frame, text="⏹", width=5, command=self.stop_audio, state=tk.DISABLED)
        self.btn_stop_music.pack(side=tk.LEFT)
        
        self.seek_var = tk.DoubleVar()
        self.seekbar = ttk.Scale(p_frame, from_=0, to=100, variable=self.seek_var, orient=tk.HORIZONTAL)
        self.seekbar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.seekbar.bind("<ButtonPress-1>", lambda e: setattr(self, 'is_seeking', True))
        self.seekbar.bind("<ButtonRelease-1>", self.on_seek_end)
        self.lbl_time = ttk.Label(p_frame, text="00:00 / 00:00", font=("Consolas", 10))
        self.lbl_time.pack(side=tk.RIGHT)

        ttk.Button(right_panel, text="💾 СОХРАНИТЬ ФАЙЛ", command=self.save_file_dialog).pack(fill=tk.X, pady=(5, 0))

    def select_file(self):
        # Останавливаем воспроизведение старого файла
        if PYGAME_AVAILABLE:
            self.stop_audio()
            
        fn = filedialog.askopenfilename(filetypes=[("Аудио", "*.mp3 *.wav *.m4a *.flac *.ogg")])
        if fn:
            self.audio_path.set(fn)
            self.read_metadata(fn)

    def read_metadata(self, path):
        if not MUTAGEN_AVAILABLE: return
        try:
            ext = os.path.splitext(path)[1].lower()
            audio = None
            
            # Сброс старых данных перед чтением
            self.audio_length_sec = 0.0
            
            # Распознавание форматов
            if ext == '.mp3':
                audio = MP3(path)
                self.track_title.set(audio.get('TIT2', [''])[0])
                self.artist.set(audio.get('TPE1', [''])[0])
            elif ext in ['.m4a', '.mp4']:
                audio = EasyMP4(path)
                self.track_title.set(audio.get('title', [''])[0])
            elif ext == '.flac':
                audio = FLAC(path)
                self.track_title.set(audio.get('title', [os.path.basename(path)])[0])
                self.artist.set(audio.get('artist', [''])[0])
            elif ext == '.wav':
                audio = WAVE(path)
            elif ext == '.ogg':
                audio = OggVorbis(path)
            
            # ОБЩИЙ БЛОК: выполняется для любого успешно открытого файла
            if audio:
                self.audio_length_sec = audio.info.length
                # Обновляем ползунок и текст времени
                self.seekbar.config(to=self.audio_length_sec)
                self.seek_var.set(0) # Сброс ползунка в начало
                self.lbl_time.config(text=f"00:00 / {self.format_time_short(self.audio_length_sec)}")
                
                # Активация кнопок
                self.btn_play.config(state=tk.NORMAL)
                self.btn_stop_music.config(state=tk.NORMAL)
                self.btn_pause.config(state=tk.DISABLED) # Пауза пока не нужна
                
                # Очистка старого текста (опционально, для чистоты)
                self.text_output.delete(1.0, tk.END)
                self.segments_data = []
        except Exception as e: 
            print(f"Ошибка чтения метаданных: {e}")
            # 3. Обновление интерфейса, если файл успешно прочитан
            if audio:
                self.audio_length_sec = audio.info.length
                
                # Обновляем ползунок и текст времени
                self.seekbar.config(to=self.audio_length_sec)
                self.lbl_time.config(text=f"00:00 / {self.format_time_short(self.audio_length_sec)}")
                
                # Активируем кнопки управления плеером
                self.btn_play.config(state=tk.NORMAL)
                self.btn_stop_music.config(state=tk.NORMAL)
                self.btn_pause.config(state=tk.DISABLED)
                
                # Очищаем старые данные сегментов от предыдущего файла
                self.segments_data = []
                
        except Exception as e:
            # Если возникла ошибка (например, файл поврежден), выводим её в консоль для отладки
            print(f"Ошибка чтения метаданных: {e}")

    def start_processing(self):
        path = self.audio_path.get()
        if not path or not os.path.exists(path): return
        
        # Очистка временной папки перед стартом
        if os.path.exists(self.demucs_out_dir):
            try: shutil.rmtree(self.demucs_out_dir, ignore_errors=True)
            except: pass
            
        self.processing = True
        self.abort_flag = False
        self.success_flag = False
        self.btn_process.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.progress_bar.start(10)
        threading.Thread(target=self.worker, args=(path,), daemon=True).start()

    def worker(self, audio_path):
        target = audio_path
        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            # 1. Этап Demucs
            if self.use_demucs.get():
                if self.abort_flag: return
                self.root.after(0, lambda: self.status_label.config(text="Demucs: Выделение речи...", foreground="orange"))
                
                # Добавлен флаг -o для указания пути без прав администратора
                cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs_ft", "--shifts", "1", "-o", self.demucs_out_dir, audio_path]
                self.current_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                
                while self.current_process.poll() is None:
                    if self.abort_flag:
                        self.current_process.terminate()
                        return
                    self.root.update_idletasks()
                
                base = os.path.splitext(os.path.basename(audio_path))[0]
                self.last_separated_path = os.path.abspath(os.path.join(self.demucs_out_dir, "htdemucs_ft", base))
                v_file = os.path.join(self.last_separated_path, "vocals.wav")
                if os.path.exists(v_file): 
                    target = v_file
                    self.root.after(0, lambda: self.btn_open_folder.config(state=tk.NORMAL))

            # 2. Этап Whisper
            if self.abort_flag: return
            self.root.after(0, lambda: self.status_label.config(text=f"Whisper: Загрузка модели... Если в кэше пусто то это может занять немало времени...", foreground="blue"))
            
            if not self.model or getattr(self.model, 'mname', '') != self.model_size.get():
                self.model = whisper.load_model(self.model_size.get(), device=device)
                self.model.mname = self.model_size.get()

            if self.abort_flag: return
            self.root.after(0, lambda: self.status_label.config(text=f"Whisper: Распознавание...", foreground="blue"))
            
            res = self.model.transcribe(
                target, 
                language=None if self.selected_lang.get()=="auto" else self.selected_lang.get(),
                word_timestamps=True,
                verbose=None 
            )

            if self.abort_flag: return

            self.segments_data = []
            off = self.offset.get() / 1000.0
            for seg in res['segments']:
                start = (seg['words'][0]['start'] if 'words' in seg and seg['words'] else seg['start']) + off
                self.segments_data.append({'start': start, 'end': seg['end'] + off, 'text': seg['text'].strip()})
            
            self.root.after(0, self.finish_success)
        except Exception as e: 
            if not self.abort_flag: self.root.after(0, lambda: messagebox.showinfo("Ошибка", str(e)))
        finally:
            self.root.after(0, self.reset_ui)

    def stop_processing_action(self, ask=True):
        if ask and not messagebox.askyesno("СТОП!", "Прервать выполнение?"):
            return
        
        self.abort_flag = True
        if self.current_process:
            try: self.current_process.terminate()
            except: pass
            
        self.status_label.config(text="Прервано пользователем!", foreground="red")
        self.reset_ui()

    def finish_success(self):
        self.success_flag = True 
        self.text_output.delete(1.0, tk.END)
        lrc_lines = [f"[{self.format_timestamp_lrc(s['start'])}] {s['text']}" for s in self.segments_data]
        self.text_output.insert(tk.END, "\n".join(lrc_lines))
        messagebox.showinfo("Успех", "Текст готов!")

    def reset_ui(self):
        self.processing = False
        self.progress_bar.stop()
        self.progress_var.set(0)
        self.btn_process.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        
        if self.abort_flag:
            self.status_label.config(text="Прервано пользователем!", foreground="red")
        elif getattr(self, 'success_flag', False):
            self.status_label.config(text="Успех! Обработка завершена.", foreground="green")
        else:
            self.status_label.config(text="Готов", foreground="black")
            
        self.update_model_info()

    def update_model_info(self):
        try: downloaded = [f.replace(".pt", "") for f in os.listdir(self.models_cache_path) if f.endswith(".pt")]
        except: downloaded = []
        self.model_info_lbl.config(text=f"В наличии: {', '.join(downloaded) if downloaded else 'пусто'}")

    def play_audio(self):
        if not PYGAME_AVAILABLE: return
        p = self.audio_path.get()
        if not os.path.exists(p): return
        
        if self.is_paused: 
            pygame.mixer.music.unpause()
        else: 
            pygame.mixer.music.load(p)
            pygame.mixer.music.play(start=self.play_offset_sec)
            
        self.is_playing = True
        self.is_paused = False
        self.btn_play.config(state=tk.DISABLED)
        self.btn_pause.config(state=tk.NORMAL)
        self.btn_stop_music.config(state=tk.NORMAL)
        self.update_player_ui()

    def pause_audio(self): 
        pygame.mixer.music.pause()
        self.is_paused = True
        self.btn_play.config(state=tk.NORMAL)
        self.btn_pause.config(state=tk.DISABLED)
        
    def stop_audio(self):
        if PYGAME_AVAILABLE: 
            pygame.mixer.music.stop()
            self.is_playing = False
            self.is_paused = False
            self.play_offset_sec = 0.0
            self.seek_var.set(0)
            self.btn_play.config(state=tk.NORMAL if self.audio_length_sec > 0 else tk.DISABLED)
            self.btn_pause.config(state=tk.DISABLED)
            self.btn_stop_music.config(state=tk.DISABLED)
            self.lbl_time.config(text=f"00:00 / {self.format_time_short(self.audio_length_sec)}")
            try: self.text_output.tag_remove("highlight", "1.0", tk.END)
            except: pass

    def update_player_ui(self):
        if not self.is_playing or self.is_paused: return
        pos = pygame.mixer.music.get_pos()
        if pos == -1: return
        
        cur = self.play_offset_sec + (pos / 1000.0)
        if not self.is_seeking: 
            self.seek_var.set(cur)
            
        self.lbl_time.config(text=f"{self.format_time_short(cur)} / {self.format_time_short(self.audio_length_sec)}")
        
        try: self.text_output.tag_remove("highlight", "1.0", tk.END)
        except: pass
        
        for i, s in enumerate(self.segments_data):
            if s['start'] <= cur <= s['end']:
                self.text_output.tag_add("highlight", f"{i+1}.0", f"{i+1}.end")
                self.text_output.see(f"{i+1}.0")
                
        self.root.after(100, self.update_player_ui)

    def on_seek_end(self, event):
        self.is_seeking = False
        self.play_offset_sec = self.seek_var.get()
        if self.is_playing: 
            pygame.mixer.music.stop()
            pygame.mixer.music.play(start=self.play_offset_sec)
        else:
            self.lbl_time.config(text=f"{self.format_time_short(self.play_offset_sec)} / {self.format_time_short(self.audio_length_sec)}")

    def format_timestamp_lrc(self, sec):
        """Форматирует секунды в стандарт LRC: [mm:ss.xx]"""
        minutes = int(max(0, sec) // 60)
        seconds = sec % 60
        # LRC обычно использует 2 знака после запятой для сотых долей секунды
        return f"{minutes:02d}:{seconds:05.2f}"
    
    def format_time_short(self, sec): return f"{int(max(0,sec)//60):02d}:{int(max(0,sec)%60):02d}"

    def save_file_dialog(self):
        fpath = filedialog.asksaveasfilename(
            defaultextension=".lrc",
            filetypes=[("LRC Lyrics", "*.lrc"), ("SubRip Subtitles", "*.srt"), ("Plain Text", "*.txt")]
        )
        if not fpath:
            return

        try:
            content = ""
            # Если выбран LRC, добавляем расширенные метаданные
            if fpath.lower().endswith(".lrc"):
                meta = []
                if self.include_meta.get():
                    if self.track_title.get(): meta.append(f"[ti:{self.track_title.get()}]")
                    if self.artist.get(): meta.append(f"[ar:{self.artist.get()}]")
                    if self.album.get(): meta.append(f"[al:{self.album.get()}]")
                    meta.append(f"[by:{self.created_by.get()}]")
                    meta.append(f"[offset:{self.offset.get()}]")
                    content = "\n".join(meta) + "\n\n"
            
            # Получаем текст из редактора
            main_text = self.text_output.get(1.0, tk.END).strip()
            content += main_text

            with open(fpath, 'w', encoding='utf-8-sig') as f: # utf-8-sig для лучшей совместимости с Windows
                f.write(content)
            
            messagebox.showinfo("Успех", f"Файл успешно сохранен:\n{os.path.basename(fpath)}")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", f"Не удалось сохранить файл: {e}")

    def delete_models(self):
        if messagebox.askyesno("?", "Удалить кэш моделей whisper? В случае повторного использования выбранную модель придется выкачивать из Интернета."):
            shutil.rmtree(self.models_cache_path, ignore_errors=True); self.update_model_info()

if __name__ == "__main__":
    root = tk.Tk(); app = LRCGeneratorApp(root); root.mainloop()