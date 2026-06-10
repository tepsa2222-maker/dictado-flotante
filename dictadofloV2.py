#!/usr/bin/env python3
"""
DICTADO FLO V2 — GTK4/Adwaita, Fedora/GNOME Wayland
application_id: com.artar.dictadoflov2
Fixes:
  - stop_async() no bloquea GTK
  - debounce en selección de historial
  - límite de 20 archivos en historial
  - lectura de archivos en hilo separado
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

import threading
import subprocess
import os
import pathlib
import datetime
import wave
import tempfile
import pyaudio

CONFIG_FILE = pathlib.Path.home() / ".config" / "dictadoflov1" / "config.env"
SAVE_DIR    = pathlib.Path.home() / "Dictados"
SAMPLE_RATE = 16000
CHUNK       = 1024
CHANNELS    = 1
FORMAT      = pyaudio.paInt16

SAVE_DIR.mkdir(exist_ok=True)

def load_api_key():
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            if line.startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("GROQ_API_KEY", "")

CSS = b"""
.dictado-text { font-size: 12pt; padding: 8px; }
.status-ready      { color: #2563eb; }
.status-recording  { color: #dc2626; }
.status-processing { color: #7c3aed; }
.status-error      { color: #dc2626; }
.history-row       { padding: 4px 8px; }
"""

# ─────────────────────────────────────────────
# GRABACIÓN
# ─────────────────────────────────────────────
class Recorder:
    def __init__(self):
        self._frames  = []
        self._running = False
        self._thread  = None
        self._lock    = threading.Lock()

    def start(self):
        with self._lock:
            self._frames  = []
            self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop_async(self, callback):
        def _stop():
            with self._lock:
                self._running = False
            if self._thread:
                self._thread.join(timeout=5)
            frames = list(self._frames)
            GLib.idle_add(callback, frames)
        threading.Thread(target=_stop, daemon=True).start()

    def _run(self):
        pa = pyaudio.PyAudio()
        try:
            stream = pa.open(
                format=FORMAT, channels=CHANNELS,
                rate=SAMPLE_RATE, input=True,
                frames_per_buffer=CHUNK,
            )
            while True:
                with self._lock:
                    if not self._running:
                        break
                self._frames.append(stream.read(CHUNK, exception_on_overflow=False))
            stream.stop_stream()
            stream.close()
        finally:
            pa.terminate()

# ─────────────────────────────────────────────
# TRANSCRIPCIÓN
# ─────────────────────────────────────────────
def save_wav(frames, path: str):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))

def transcribe_groq(wav_path: str, api_key: str) -> str:
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        with open(wav_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=("audio.wav", f, "audio/wav"),
                language="es",
                response_format="text",
            )
        return result.strip() if isinstance(result, str) else result.text.strip()
    except Exception as e:
        return f"[Error: {e}]"

# ─────────────────────────────────────────────
# VENTANA
# ─────────────────────────────────────────────
class DictadoFloV2Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Dictado Flo V2")
        self.set_default_size(340, 520)
        self.set_resizable(True)

        self.api_key        = load_api_key()
        self.recorder       = Recorder()
        self.recording      = False
        self._history_files = []
        self._preview_timer = None
        self._pending_row   = None

        self._apply_css()
        self._build_ui()
        self._load_history()

        if self.api_key:
            self._set_status("listo", "status-ready")
        else:
            self._set_status("sin API key", "status-error")

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        header = Adw.HeaderBar()
        self.lbl_status = Gtk.Label(label="iniciando...")
        self.lbl_status.add_css_class("caption")
        header.pack_end(self.lbl_status)

        box_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box_main.append(header)

        lbl_current = Gtk.Label(label="DICTADO ACTUAL")
        lbl_current.add_css_class("caption")
        lbl_current.set_margin_start(12)
        lbl_current.set_margin_top(8)
        lbl_current.set_halign(Gtk.Align.START)
        box_main.append(lbl_current)

        frame_txt = Gtk.Frame()
        frame_txt.set_margin_start(12)
        frame_txt.set_margin_end(12)
        frame_txt.set_margin_top(4)
        frame_txt.set_margin_bottom(4)
        scroll_txt = Gtk.ScrolledWindow()
        scroll_txt.set_min_content_height(160)
        scroll_txt.set_vexpand(True)
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.textview.add_css_class("dictado-text")
        self.textbuffer = self.textview.get_buffer()
        scroll_txt.set_child(self.textview)
        frame_txt.set_child(scroll_txt)
        box_main.append(frame_txt)

        box_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box_btns.set_margin_start(12)
        box_btns.set_margin_end(12)
        box_btns.set_margin_top(8)
        box_btns.set_margin_bottom(4)

        self.btn_rec = Gtk.Button(label="Grabar")
        self.btn_rec.add_css_class("suggested-action")
        self.btn_rec.set_hexpand(True)
        self.btn_rec.connect("clicked", self.toggle_recording)
        box_btns.append(self.btn_rec)

        btn_copy = Gtk.Button(label="Copiar")
        btn_copy.connect("clicked", self.copy_current)
        box_btns.append(btn_copy)

        btn_clear = Gtk.Button(label="Limpiar")
        btn_clear.connect("clicked", self.clear_current)
        box_btns.append(btn_clear)

        box_main.append(box_btns)
        box_main.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        lbl_hist = Gtk.Label(label="DICTADOS GUARDADOS")
        lbl_hist.add_css_class("caption")
        lbl_hist.set_margin_start(12)
        lbl_hist.set_margin_top(8)
        lbl_hist.set_halign(Gtk.Align.START)
        box_main.append(lbl_hist)

        frame_hist = Gtk.Frame()
        frame_hist.set_margin_start(12)
        frame_hist.set_margin_end(12)
        frame_hist.set_margin_top(4)
        frame_hist.set_margin_bottom(4)
        scroll_hist = Gtk.ScrolledWindow()
        scroll_hist.set_min_content_height(140)
        scroll_hist.set_vexpand(True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self.on_history_activated)
        self.listbox.connect("row-selected",  self.on_history_selected)
        scroll_hist.set_child(self.listbox)
        frame_hist.set_child(scroll_hist)
        box_main.append(frame_hist)

        box_hist_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box_hist_btns.set_margin_start(12)
        box_hist_btns.set_margin_end(12)
        box_hist_btns.set_margin_bottom(12)
        lbl_hint = Gtk.Label(label="clic = ver  |  doble clic = copiar")
        lbl_hint.add_css_class("caption")
        lbl_hint.set_hexpand(True)
        lbl_hint.set_halign(Gtk.Align.START)
        box_hist_btns.append(lbl_hint)
        btn_del = Gtk.Button(label="Borrar")
        btn_del.add_css_class("destructive-action")
        btn_del.connect("clicked", self.delete_selected)
        box_hist_btns.append(btn_del)
        box_main.append(box_hist_btns)

        self.set_content(box_main)

    # ── Grabación ─────────────────────────────
    def toggle_recording(self, btn):
        if not self.recording:
            self._start_rec()
        else:
            self._stop_rec()

    def _start_rec(self):
        if not self.api_key:
            self._set_status("sin API key", "status-error")
            return
        self.recording = True
        self.btn_rec.set_label("Detener")
        self.btn_rec.remove_css_class("suggested-action")
        self.btn_rec.add_css_class("destructive-action")
        self._set_status("grabando...", "status-recording")
        self.recorder.start()

    def _stop_rec(self):
        self.recording = False
        self.btn_rec.set_sensitive(False)
        self._set_status("deteniendo...", "status-processing")
        self.recorder.stop_async(self._on_frames_ready)

    def _on_frames_ready(self, frames):
        self._set_status("transcribiendo...", "status-processing")
        threading.Thread(target=self._transcribe, args=(frames,), daemon=True).start()
        return False

    def _transcribe(self, frames):
        tmp = tempfile.mktemp(suffix=".wav")
        try:
            save_wav(frames, tmp)
            text = transcribe_groq(tmp, self.api_key)
        except Exception as e:
            text = f"[Error: {e}]"
        finally:
            try: os.unlink(tmp)
            except: pass
        GLib.idle_add(self._on_transcribed, text)

    def _on_transcribed(self, text):
        start   = self.textbuffer.get_start_iter()
        end     = self.textbuffer.get_end_iter()
        current = self.textbuffer.get_text(start, end, False).strip()
        new_text = (current + " " + text).strip() if current else text
        self.textbuffer.set_text(new_text)
        self._auto_save(new_text)
        self.btn_rec.set_label("Grabar")
        self.btn_rec.remove_css_class("destructive-action")
        self.btn_rec.add_css_class("suggested-action")
        self.btn_rec.set_sensitive(True)
        self._set_status("listo", "status-ready")
        return False

    # ── Clipboard ─────────────────────────────
    def _clipboard(self, text):
        try:
            subprocess.run(["wl-copy"], input=text.encode(), check=True)
        except Exception:
            self.get_display().get_clipboard().set(text)

    # ── Auto guardado ─────────────────────────
    def _auto_save(self, text):
        if not text.strip(): return
        now      = datetime.datetime.now()
        stamp    = now.strftime("%Y-%m-%d_%H-%M-%S")
        filename = SAVE_DIR / f"dictado_{stamp}.txt"
        filename.write_text(text.strip(), encoding="utf-8")
        self._add_history_row(now.strftime("%H:%M"), text.strip(), str(filename), prepend=True)

    def _add_history_row(self, ts, text, filepath, prepend=False):
        preview = text[:60].replace("\n", " ")
        label   = Gtk.Label(label=f"{ts}  {preview}{'...' if len(text)>60 else ''}")
        label.set_halign(Gtk.Align.START)
        label.add_css_class("history-row")
        row = Gtk.ListBoxRow()
        row.set_child(label)
        row._filepath = filepath
        if prepend:
            self.listbox.prepend(row)
            self._history_files.insert(0, filepath)
        else:
            self.listbox.append(row)
            self._history_files.append(filepath)

    def _load_history(self):
        self._history_files = []
        # Límite 20 archivos para evitar sobrecarga
        files = sorted(SAVE_DIR.glob("dictado_*.txt"), reverse=True)[:20]
        for f in files:
            text = f.read_text(encoding="utf-8").strip()
            ts   = f.stem.replace("dictado_", "")[:16].replace("_", " ")
            self._add_history_row(ts, text, str(f))

    # ── Historial con debounce ─────────────────
    def on_history_selected(self, listbox, row):
        if not row: return
        # Cancelar preview pendiente
        if self._preview_timer is not None:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = None
        self._pending_row = row
        # Esperar 200ms antes de leer el archivo
        self._preview_timer = GLib.timeout_add(200, self._do_preview)

    def _do_preview(self):
        self._preview_timer = None
        row = self._pending_row
        if not row: return False
        path = pathlib.Path(row._filepath)
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8").strip()
                self.textbuffer.set_text(text)
            except Exception:
                pass
        return False

    def on_history_activated(self, listbox, row):
        path = pathlib.Path(row._filepath)
        if path.exists():
            try:
                self._clipboard(path.read_text(encoding="utf-8").strip())
                self._set_status("copiado ✓", "status-ready")
            except Exception:
                self._set_status("error al copiar", "status-error")

    def delete_selected(self, btn):
        row = self.listbox.get_selected_row()
        if not row: return
        path = pathlib.Path(row._filepath)
        if path.exists(): path.unlink()
        self.listbox.remove(row)
        if row._filepath in self._history_files:
            self._history_files.remove(row._filepath)
        self.textbuffer.set_text("")

    def copy_current(self, btn):
        start = self.textbuffer.get_start_iter()
        end   = self.textbuffer.get_end_iter()
        text  = self.textbuffer.get_text(start, end, False).strip()
        if not text:
            self._set_status("nada que copiar", "status-error")
            return
        self._clipboard(text)
        self._set_status("copiado ✓", "status-ready")

    def clear_current(self, btn):
        self.textbuffer.set_text("")
        self._set_status("listo", "status-ready")

    def _set_status(self, msg, css_class=""):
        self.lbl_status.set_label(msg)
        for c in ["status-ready","status-recording","status-processing","status-error"]:
            self.lbl_status.remove_css_class(c)
        if css_class:
            self.lbl_status.add_css_class(css_class)

# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────
class DictadoFloV2App(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.artar.dictadoflov2",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = self.get_active_window()
        if not win:
            win = DictadoFloV2Window(app)
        win.present()

def main():
    app = DictadoFloV2App()
    app.run()

if __name__ == "__main__":
    main()
