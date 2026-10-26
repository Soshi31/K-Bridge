"""
K-Bridge - Kindle Colorsoft & KOReader Management Suite.
Commercial-grade, modern desktop application for batch PDF/EPUB conversion,
screensaver/wallpaper management, and bookshelf ornament transfers via KOReader Wi-Fi.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from tkinter import filedialog, messagebox
from typing import Dict, List, Optional

import customtkinter as ctk

from app.batch_runner import BatchRunner, QueueItem
from app.config import AppConfig, detect_tesseract_path, get_user_data_dir, load_config, save_config
from app.epub_builder import EPUBBuilder
from app.metadata_fetcher import MetadataFetcher
from app.sftp_client import KindleSFTPClient

STATUS_COLORS = {
    # English status keys
    "Pending": ("#555555", "#777777"),
    "Rendering/OCR...": ("#D97706", "#F59E0B"),
    "EPUB Generated": ("#16A34A", "#22C55E"),
    "EPUB Loaded": ("#2563EB", "#3B82F6"),
    "Uploaded": ("#0891B2", "#06B6D4"),
    "Sent to E-Reader": ("#0891B2", "#06B6D4"),
    "Sent to Kindle": ("#0891B2", "#06B6D4"),
    "Uploading...": ("#D97706", "#F59E0B"),
    "Error": ("#DC2626", "#EF4444"),
    "Stopped": ("#4B5563", "#6B7280"),
    # Legacy Romanian status aliases
    "În așteptare": ("#555555", "#777777"),
    "Randare/OCR...": ("#D97706", "#F59E0B"),
    "Generat EPUB": ("#16A34A", "#22C55E"),
    "EPUB Încărcat": ("#2563EB", "#3B82F6"),
    "Trimis SSH": ("#0891B2", "#06B6D4"),
    "Eroare": ("#DC2626", "#EF4444"),
    "Oprit": ("#4B5563", "#6B7280"),
}


def format_file_size(num_bytes: int) -> str:
    """Formats file size into human-readable string (KB, MB)."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


@dataclass
class MediaItem:
    item_id: str
    file_path: str
    file_name: str
    file_size_str: str
    status: str = "Pending"


class MetadataEditDialog(ctk.CTkToplevel):
    """Modal dialog for viewing and manually editing book metadata."""

    def __init__(self, parent, item: QueueItem, on_save_callback: Optional[callable] = None):
        super().__init__(parent)
        self.item = item
        self.on_save_callback = on_save_callback

        self.title("Edit Book Metadata")
        self.geometry("490x470")
        self.resizable(False, False)
        self.grab_set()

        lbl_head = ctk.CTkLabel(
            self,
            text="Book Metadata (Title, Author, Genre, Series)",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        lbl_head.pack(pady=(16, 10))

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=24, pady=5)

        ctk.CTkLabel(frame, text="Book Title:").pack(anchor="w", pady=(2, 0))
        self.ent_title = ctk.CTkEntry(frame)
        self.ent_title.pack(fill="x", pady=(0, 6))
        self.ent_title.insert(0, item.title if item.title else item.file_name)

        ctk.CTkLabel(frame, text="Author:").pack(anchor="w", pady=(2, 0))
        self.ent_author = ctk.CTkEntry(frame)
        self.ent_author.pack(fill="x", pady=(0, 6))
        self.ent_author.insert(0, item.author if item.author else "Unknown Author")

        ctk.CTkLabel(frame, text="Genre / Subject:").pack(anchor="w", pady=(2, 0))
        self.ent_genre = ctk.CTkEntry(frame)
        self.ent_genre.pack(fill="x", pady=(0, 6))
        self.ent_genre.insert(0, item.genre if item.genre else "Fiction")

        ctk.CTkLabel(frame, text="Series / Volume # (e.g. Dune #1, Harry Potter #2):").pack(anchor="w", pady=(2, 0))
        self.ent_series = ctk.CTkEntry(frame)
        self.ent_series.pack(fill="x", pady=(0, 6))
        self.ent_series.insert(0, item.series if item.series else "")

        ctk.CTkLabel(frame, text="Send Format:").pack(anchor="w", pady=(2, 0))
        self.seg_item_format = ctk.CTkSegmentedButton(frame, values=["EPUB (Convert)", "PDF (Direct + Meta)"])
        self.seg_item_format.pack(fill="x", pady=(0, 10))
        self.seg_item_format.set("PDF (Direct + Meta)" if getattr(item, "target_format", "EPUB") == "PDF" else "EPUB (Convert)")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(8, 16))

        btn_fetch = ctk.CTkButton(
            btn_row,
            text="🌐 Fetch Online",
            width=120,
            fg_color="#37474F",
            hover_color="#455A64",
            command=self._fetch_online,
        )
        btn_fetch.pack(side="left")

        btn_save = ctk.CTkButton(
            btn_row,
            text="Save Changes",
            width=110,
            fg_color="#2E7D32",
            hover_color="#1B5E20",
            command=self._save,
        )
        btn_save.pack(side="right")

    def _fetch_online(self):
        meta = MetadataFetcher.fetch_book_metadata(
            pdf_filename=self.item.file_name,
            pdf_internal_title=self.ent_title.get().strip(),
            pdf_internal_author=self.ent_author.get().strip(),
        )
        if meta:
            if meta.get("title"):
                self.ent_title.delete(0, "end")
                self.ent_title.insert(0, meta["title"])
            if meta.get("author"):
                self.ent_author.delete(0, "end")
                self.ent_author.insert(0, meta["author"])
            if meta.get("genre"):
                self.ent_genre.delete(0, "end")
                self.ent_genre.insert(0, meta["genre"])
            if meta.get("series"):
                self.ent_series.delete(0, "end")
                self.ent_series.insert(0, meta["series"])

    def _save(self):
        self.item.title = self.ent_title.get().strip()
        self.item.author = self.ent_author.get().strip()
        self.item.genre = self.ent_genre.get().strip()
        self.item.series = self.ent_series.get().strip()
        self.item.target_format = "PDF" if "PDF" in self.seg_item_format.get() else "EPUB"

        if self.item.file_type == "epub" or self.item.pdf_path.lower().endswith(".epub"):
            EPUBBuilder.update_epub_metadata(
                epub_path=self.item.pdf_path,
                title=self.item.title,
                author=self.item.author,
                genre=self.item.genre,
                series=self.item.series,
            )

        if self.on_save_callback:
            self.on_save_callback(self.item)
        self.destroy()


class QueueItemWidget(ctk.CTkFrame):
    """Visual card for each book in the conversion queue."""

    def __init__(
        self,
        master,
        item: QueueItem,
        on_delete: Optional[callable] = None,
        on_upload: Optional[callable] = None,
        on_edit: Optional[callable] = None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.item = item
        self.on_delete = on_delete
        self.on_upload = on_upload
        self.on_edit = on_edit

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_columnconfigure(2, weight=0)
        self.grid_columnconfigure(3, weight=0)
        self.grid_columnconfigure(4, weight=0)
        self.grid_columnconfigure(5, weight=0)

        # Title
        display_title = item.title if item.title else item.file_name
        self.lbl_title = ctk.CTkLabel(
            self,
            text=display_title,
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.lbl_title.grid(row=0, column=0, padx=(10, 5), pady=(4, 0), sticky="w")

        # Subtitle: Author • Genre • Series
        self.lbl_sub = ctk.CTkLabel(
            self,
            text=self._build_sub_text(),
            font=ctk.CTkFont(size=10),
            text_color="#AAAAAA",
            anchor="w",
        )
        self.lbl_sub.grid(row=1, column=0, padx=(10, 5), pady=(0, 4), sticky="w")

        # Format Toggle Button (EPUB vs PDF)
        is_pdf = getattr(item, "target_format", "EPUB") == "PDF"
        self.btn_fmt = ctk.CTkButton(
            self,
            text="📄 PDF" if is_pdf else "📖 EPUB",
            width=68,
            height=24,
            fg_color="#00695C" if is_pdf else "#1E3A8A",
            hover_color="#004D40" if is_pdf else "#172554",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._toggle_format_clicked,
        )
        self.btn_fmt.grid(row=0, column=1, rowspan=2, padx=(3, 2), pady=4)

        # Edit button
        self.btn_edit = ctk.CTkButton(
            self,
            text="✏️",
            width=28,
            height=24,
            fg_color="transparent",
            hover_color="#334455",
            font=ctk.CTkFont(size=11),
            command=self._edit_clicked,
        )
        self.btn_edit.grid(row=0, column=2, rowspan=2, padx=(2, 2), pady=4)

        # Single Upload Button
        self.btn_send = ctk.CTkButton(
            self,
            text="📤 Send",
            width=65,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#00838F",
            hover_color="#006064",
            command=self._upload_clicked,
        )
        self.btn_send.grid(row=0, column=3, rowspan=2, padx=(2, 4), pady=4)

        # Status badge
        display_status = self._translate_status(item.status)
        self.lbl_status = ctk.CTkLabel(
            self,
            text=display_status,
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=5,
            fg_color="#333333",
            text_color="#FFFFFF",
            padx=8,
            pady=2,
        )
        self.lbl_status.grid(row=0, column=4, rowspan=2, padx=(2, 6), pady=4, sticky="e")
        self.update_status(item.status)

        # Remove button
        self.btn_remove = ctk.CTkButton(
            self,
            text="✕",
            width=24,
            height=24,
            fg_color="transparent",
            hover_color="#552222",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._delete_clicked,
        )
        self.btn_remove.grid(row=0, column=5, rowspan=2, padx=(0, 6), pady=4)

    def _translate_status(self, st: str) -> str:
        translations = {
            "În așteptare": "Pending",
            "Randare/OCR...": "Rendering/OCR...",
            "Generat EPUB": "EPUB Ready",
            "EPUB Încărcat": "EPUB Ready",
            "EPUB Pregătit": "EPUB Ready",
            "Trimis SSH": "Sent to E-Reader",
            "Sent to Kindle": "Sent to E-Reader",
            "Eroare": "Error",
            "Oprit": "Stopped",
        }
        return translations.get(st, st)

    def _build_sub_text(self) -> str:
        parts = []
        if self.item.author:
            parts.append(f"👤 {self.item.author}")
        if self.item.series:
            parts.append(f"📚 {self.item.series}")
        if self.item.genre:
            parts.append(f"🏷️ {self.item.genre}")
        if getattr(self.item, "target_format", "EPUB") == "PDF":
            parts.append("📄 Direct PDF")
        elif self.item.file_type == "epub" or self.item.pdf_path.lower().endswith(".epub"):
            parts.append("📖 EPUB")
        if not parts:
            parts.append(str(Path(self.item.pdf_path).parent))
        elif self.item.title and self.item.title != self.item.file_name:
            parts.append(f"({self.item.file_name})")
        return " • ".join(parts)

    def _toggle_format_clicked(self):
        new_fmt = "PDF" if getattr(self.item, "target_format", "EPUB") != "PDF" else "EPUB"
        self.item.target_format = new_fmt
        self.update_format_button()
        self.update_view()

    def update_format_button(self):
        is_pdf = getattr(self.item, "target_format", "EPUB") == "PDF"
        self.btn_fmt.configure(
            text="📄 PDF" if is_pdf else "📖 EPUB",
            fg_color="#00695C" if is_pdf else "#1E3A8A",
            hover_color="#004D40" if is_pdf else "#172554",
        )

    def update_view(self):
        display_title = self.item.title if self.item.title else self.item.file_name
        self.lbl_title.configure(text=display_title)
        self.lbl_sub.configure(text=self._build_sub_text())
        self.update_format_button()

    def _delete_clicked(self):
        if self.on_delete:
            self.on_delete(self.item.item_id)

    def _upload_clicked(self):
        if self.on_upload:
            self.on_upload(self.item)

    def _edit_clicked(self):
        if self.on_edit:
            self.on_edit(self.item)

    def update_status(self, new_status: str, extra_msg: str = ""):
        self.item.status = new_status
        display = self._translate_status(new_status)
        self.lbl_status.configure(text=display)
        colors = STATUS_COLORS.get(new_status, STATUS_COLORS.get(display, ("#444444", "#888888")))
        self.lbl_status.configure(fg_color=colors[0])
        if extra_msg:
            self.lbl_sub.configure(text=extra_msg)


class MediaItemWidget(ctk.CTkFrame):
    """Visual card for Wallpaper and Ornament images."""

    def __init__(
        self,
        master,
        item: MediaItem,
        icon_emoji: str = "🖼️",
        on_delete: Optional[callable] = None,
        on_upload: Optional[callable] = None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.item = item
        self.icon_emoji = icon_emoji
        self.on_delete = on_delete
        self.on_upload = on_upload

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_columnconfigure(2, weight=0)
        self.grid_columnconfigure(3, weight=0)

        # Title
        self.lbl_title = ctk.CTkLabel(
            self,
            text=f"{self.icon_emoji}  {item.file_name}",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.lbl_title.grid(row=0, column=0, padx=(10, 5), pady=(4, 0), sticky="w")

        # Subtitle: File size & extension
        ext = Path(item.file_path).suffix.upper().replace(".", "")
        self.lbl_sub = ctk.CTkLabel(
            self,
            text=f"{ext} Image • {item.file_size_str}",
            font=ctk.CTkFont(size=10),
            text_color="#AAAAAA",
            anchor="w",
        )
        self.lbl_sub.grid(row=1, column=0, padx=(10, 5), pady=(0, 4), sticky="w")

        # Single Upload Button
        self.btn_send = ctk.CTkButton(
            self,
            text="📤 Send",
            width=65,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#00838F",
            hover_color="#006064",
            command=self._upload_clicked,
        )
        self.btn_send.grid(row=0, column=1, rowspan=2, padx=(2, 4), pady=4)

        # Status badge
        self.lbl_status = ctk.CTkLabel(
            self,
            text=item.status,
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=5,
            fg_color="#333333",
            text_color="#FFFFFF",
            padx=8,
            pady=2,
        )
        self.lbl_status.grid(row=0, column=2, rowspan=2, padx=(2, 6), pady=4, sticky="e")
        self.update_status(item.status)

        # Remove button
        self.btn_remove = ctk.CTkButton(
            self,
            text="✕",
            width=24,
            height=24,
            fg_color="transparent",
            hover_color="#552222",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._delete_clicked,
        )
        self.btn_remove.grid(row=0, column=3, rowspan=2, padx=(0, 6), pady=4)

    def _delete_clicked(self):
        if self.on_delete:
            self.on_delete(self.item.item_id)

    def _upload_clicked(self):
        if self.on_upload:
            self.on_upload(self.item)

    def update_status(self, new_status: str):
        self.item.status = new_status
        self.lbl_status.configure(text=new_status)
        colors = STATUS_COLORS.get(new_status, ("#444444", "#888888"))
        self.lbl_status.configure(fg_color=colors[0])


class MainWindow(ctk.CTk):
    """Main application window for K-Bridge."""

    def __init__(self):
        super().__init__()

        # Load configuration
        self.config = load_config()

        ctk.set_appearance_mode(self.config.appearance_mode)
        ctk.set_default_color_theme(self.config.color_theme)

        self.title("K-Bridge - E-Reader & KOReader Manager")
        self.geometry("1180x790")
        self.minsize(1020, 700)

        # Window icon and taskbar identity
        base_dir = Path(__file__).resolve().parent.parent.parent
        icon_ico = base_dir / "K-Bridge.ico"
        if not icon_ico.is_file() and getattr(sys, "frozen", False):
            icon_ico = Path(sys._MEIPASS) / "K-Bridge.ico"
        if icon_ico.is_file():
            try:
                self.iconbitmap(str(icon_ico))
            except Exception:
                pass

        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kbridge.kindle.manager.1.0")
        except Exception:
            pass

        # Data states
        self.queue_items: List[QueueItem] = []
        self.queue_widgets: Dict[str, QueueItemWidget] = {}
        self.runner: Optional[BatchRunner] = None

        self.wallpaper_items: List[MediaItem] = []
        self.wallpaper_widgets: Dict[str, MediaItemWidget] = {}

        self.ornament_items: List[MediaItem] = []
        self.ornament_widgets: Dict[str, MediaItemWidget] = {}

        self.is_uploading_wallpapers = False
        self.is_uploading_ornaments = False

        # Build UI layout
        self._build_layout()
        self._load_config_into_ui()
        self._load_queue_history()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        try:
            self._save_settings_from_ui()
            self._save_queue_history()
        except Exception:
            pass
        self.destroy()

    # --- History Persistence ---

    def _get_history_file_path(self) -> Path:
        data_dir = get_user_data_dir()
        p1 = data_dir / "session_queue.json"
        p2 = data_dir / "queue_history.json"
        if not p1.is_file() and p2.is_file():
            return p2
        return p1

    def _save_queue_history(self):
        try:
            data = []
            for it in self.queue_items:
                data.append(
                    {
                        "item_id": it.item_id,
                        "pdf_path": it.pdf_path,
                        "file_name": it.file_name,
                        "title": it.title,
                        "author": it.author,
                        "genre": it.genre,
                        "series": it.series,
                        "file_type": it.file_type,
                        "status": it.status,
                        "output_epub_path": it.output_epub_path,
                        "total_pages": it.total_pages,
                        "target_format": getattr(it, "target_format", "EPUB"),
                    }
                )
            with open(self._get_history_file_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Warning] Could not save session history: {e}")

    def _load_queue_history(self):
        hist_file = self._get_history_file_path()
        if not hist_file.is_file():
            return
        try:
            with open(hist_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                entries = data.get("books", [])
            elif isinstance(data, list):
                entries = data
            else:
                return

            loaded_count = 0
            for entry in entries:
                pdf_p = entry.get("pdf_path", "")
                if not pdf_p or not os.path.isfile(pdf_p):
                    continue

                is_epub = pdf_p.lower().endswith(".epub") or entry.get("file_type") == "epub"
                out_epub = entry.get("output_epub_path", "")
                if is_epub:
                    out_epub = pdf_p
                elif not out_epub or not os.path.isfile(out_epub):
                    expected_epub = Path(self.config.output_dir) / f"{Path(pdf_p).stem}.epub"
                    if expected_epub.is_file():
                        out_epub = str(expected_epub)

                status = entry.get("status", "Pending")
                if status in ["În așteptare", "Randare/OCR..."]:
                    status = "Pending"
                elif status in ["Generat EPUB", "EPUB Încărcat", "EPUB Pregătit"]:
                    status = "EPUB Ready"
                elif status == "Trimis SSH":
                    status = "Sent to E-Reader"

                item_id = entry.get("item_id", f"item_{len(self.queue_items)}_{Path(pdf_p).stem}")
                loaded_title = entry.get("title", "")
                loaded_author = entry.get("author", "")
                loaded_genre = entry.get("genre", "")
                if not loaded_genre or loaded_genre == "Fiction":
                    loaded_genre = MetadataFetcher.classify_genre(loaded_title, loaded_author)

                target_fmt = entry.get("target_format", getattr(self.config, "target_format", "EPUB"))
                item = QueueItem(
                    item_id=item_id,
                    pdf_path=pdf_p,
                    file_name=entry.get("file_name", Path(pdf_p).name),
                    title=loaded_title,
                    author=loaded_author,
                    genre=loaded_genre,
                    series=entry.get("series", ""),
                    file_type="epub" if is_epub else "pdf",
                    total_pages=entry.get("total_pages", 0),
                    status=status,
                    output_epub_path=out_epub,
                    target_format=target_fmt,
                )
                self.queue_items.append(item)

                widget = QueueItemWidget(
                    self.scroll_books_queue,
                    item=item,
                    on_delete=self._remove_queue_item,
                    on_upload=self._upload_single_book,
                    on_edit=self._open_metadata_dialog,
                )
                widget.pack(fill="x", pady=3, padx=4)
                self.queue_widgets[item_id] = widget
                loaded_count += 1

            if loaded_count > 0 and self.lbl_empty_books:
                self.lbl_empty_books.pack_forget()

            self._update_batch_progress_ui(0, len(self.queue_items))
            self.set_status(f"Restored {loaded_count} books from previous session.")
        except Exception as e:
            print(f"[Warning] Error loading history: {e}")

    # --- UI Construction ---

    def _build_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 1. TOP HEADER
        header = ctk.CTkFrame(self, height=52, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=16, pady=6, sticky="w")

        lbl_app_title = ctk.CTkLabel(
            title_box,
            text="⚡ K-Bridge",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        lbl_app_title.pack(side="left")

        lbl_sub_header = ctk.CTkLabel(
            title_box,
            text="  E-Reader & KOReader Manager",
            font=ctk.CTkFont(size=12),
            text_color="#888888",
        )
        lbl_sub_header.pack(side="left", padx=(4, 0))

        theme_btn = ctk.CTkSegmentedButton(
            header,
            values=["Dark", "Light"],
            command=self._change_theme,
        )
        theme_btn.set(self.config.appearance_mode if self.config.appearance_mode in ["Dark", "Light"] else "Dark")
        theme_btn.grid(row=0, column=1, padx=16, pady=8, sticky="e")

        # 2. TAB NAVIGATION
        self.tabview = ctk.CTkTabview(self, corner_radius=10)
        self.tabview.grid(row=1, column=0, padx=14, pady=(6, 6), sticky="nsew")

        tab_books = self.tabview.add("📚 Books")
        tab_wallpapers = self.tabview.add("🖼️ Wallpapers")
        tab_ornaments = self.tabview.add("✨ Ornaments")

        # Build individual tabs
        self._build_books_tab(tab_books)
        self._build_wallpapers_tab(tab_wallpapers)
        self._build_ornaments_tab(tab_ornaments)

        # 3. BOTTOM SLEEK STATUS BAR
        status_bar = ctk.CTkFrame(self, height=32, corner_radius=0)
        status_bar.grid(row=2, column=0, sticky="ew")
        status_bar.grid_columnconfigure(0, weight=1)

        self.lbl_status_bar = ctk.CTkLabel(
            status_bar,
            text="Ready",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.lbl_status_bar.grid(row=0, column=0, padx=16, pady=4, sticky="w")

        self.lbl_conn_pill = ctk.CTkLabel(
            status_bar,
            text=f"● E-Reader: {self.config.kindle_ip}:{self.config.kindle_port}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#22C55E",
            anchor="e",
        )
        self.lbl_conn_pill.grid(row=0, column=1, padx=16, pady=4, sticky="e")

    # =========================================================================
    # TAB 1: BOOKS
    # =========================================================================

    def _build_books_tab(self, parent):
        parent.grid_columnconfigure(0, weight=6)
        parent.grid_columnconfigure(1, weight=5)
        parent.grid_rowconfigure(0, weight=1)

        # Left Column: Books Queue
        left_col = ctk.CTkFrame(parent)
        left_col.grid(row=0, column=0, padx=(0, 8), pady=4, sticky="nsew")
        left_col.grid_columnconfigure(0, weight=1)
        left_col.grid_rowconfigure(1, weight=1)

        # Action Buttons
        tb_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        tb_frame.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")

        btn_add = ctk.CTkButton(
            tb_frame,
            text="+ Add Books (PDF / EPUB)",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._add_book_files_dialog,
        )
        btn_add.pack(side="left", padx=(0, 6))

        btn_folder = ctk.CTkButton(
            tb_frame,
            text="+ Folder",
            width=80,
            fg_color="#37474F",
            hover_color="#455A64",
            command=self._add_book_folder_dialog,
        )
        btn_folder.pack(side="left", padx=4)

        btn_clear = ctk.CTkButton(
            tb_frame,
            text="Clear",
            width=70,
            fg_color="transparent",
            border_width=1,
            hover_color="#552222",
            command=self._clear_books_queue,
        )
        btn_clear.pack(side="right")

        # Scrollable Books Queue
        self.scroll_books_queue = ctk.CTkScrollableFrame(
            left_col,
            label_text="Books in Queue",
            label_font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.scroll_books_queue.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="nsew")
        self.scroll_books_queue.grid_columnconfigure(0, weight=1)

        self.lbl_empty_books = ctk.CTkLabel(
            self.scroll_books_queue,
            text="No books added.\nUse the buttons above to load PDF or EPUB files.",
            text_color="#777777",
            font=ctk.CTkFont(size=12),
        )
        self.lbl_empty_books.pack(pady=50)

        # Bottom Conversion CTA Bar
        cta_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        cta_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
        cta_frame.grid_columnconfigure(0, weight=3)
        cta_frame.grid_columnconfigure(1, weight=2)
        cta_frame.grid_columnconfigure(2, weight=0)

        self.btn_start_conversion = ctk.CTkButton(
            cta_frame,
            text="🚀 START CONVERSION",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            fg_color="#1B5E20",
            hover_color="#2E7D32",
            command=self._start_conversion,
        )
        self.btn_start_conversion.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.btn_direct_upload = ctk.CTkButton(
            cta_frame,
            text="📤 Direct Send (PDF/EPUB)",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=38,
            fg_color="#00695C",
            hover_color="#004D40",
            command=self._upload_all_books_direct,
        )
        self.btn_direct_upload.grid(row=0, column=1, padx=(2, 4), sticky="ew")

        self.btn_stop_conversion = ctk.CTkButton(
            cta_frame,
            text="⏹ Stop",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=38,
            width=70,
            fg_color="#7F1D1D",
            hover_color="#991B1B",
            state="disabled",
            command=self._stop_conversion,
        )
        self.btn_stop_conversion.grid(row=0, column=2, sticky="e")

        # Right Column: Kindle Connection, Settings & Progress
        right_col = ctk.CTkFrame(parent, fg_color="transparent")
        right_col.grid(row=0, column=1, padx=(8, 0), pady=4, sticky="nsew")
        right_col.grid_columnconfigure(0, weight=1)

        # Card 1: Kindle Connection
        conn_card = ctk.CTkFrame(right_col)
        conn_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        conn_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            conn_card,
            text="📱 E-Reader KOReader Connection (Wi-Fi)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 6), sticky="w")

        ctk.CTkLabel(conn_card, text="E-Reader IP:").grid(row=1, column=0, padx=(12, 4), pady=3, sticky="w")
        self.entry_ip_books = ctk.CTkEntry(conn_card, placeholder_text="e.g. 192.168.43.150")
        self.entry_ip_books.grid(row=1, column=1, padx=(4, 12), pady=3, sticky="ew")
        self.entry_ip_books.bind("<KeyRelease>", lambda e: self._on_ip_entry_changed(self.entry_ip_books.get()))

        ctk.CTkLabel(conn_card, text="Books Folder:").grid(row=2, column=0, padx=(12, 4), pady=3, sticky="w")
        self.entry_books_dir = ctk.CTkEntry(conn_card, placeholder_text="/mnt/us/Carti/Biblioteca")
        self.entry_books_dir.grid(row=2, column=1, padx=(4, 12), pady=3, sticky="ew")
        self.entry_books_dir.bind("<KeyRelease>", lambda e: self._on_books_dir_changed())

        self.chk_autosend_books = ctk.CTkCheckBox(
            conn_card,
            text="Automatically upload to E-Reader after conversion",
            font=ctk.CTkFont(size=11),
            command=self._on_autosend_toggled,
        )
        self.chk_autosend_books.grid(row=3, column=0, columnspan=2, padx=12, pady=(6, 10), sticky="w")

        # Card 2: Book Output Format & Metadata
        options_card = ctk.CTkFrame(right_col)
        options_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        options_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            options_card,
            text="⚙️ Book Output Format & Metadata",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(8, 6), sticky="w")

        ctk.CTkLabel(options_card, text="Default Send Format:").grid(row=1, column=0, padx=(12, 4), pady=3, sticky="w")
        self.seg_format = ctk.CTkSegmentedButton(
            options_card,
            values=["EPUB (Convert)", "PDF (Direct + Meta)"],
            command=self._on_format_changed,
        )
        self.seg_format.grid(row=1, column=1, padx=(4, 12), pady=3, sticky="ew")
        self.seg_format.set("PDF (Direct + Meta)" if getattr(self.config, "target_format", "EPUB") == "PDF" else "EPUB (Convert)")

        ctk.CTkLabel(options_card, text="OCR Language:").grid(row=2, column=0, padx=(12, 4), pady=3, sticky="w")
        self.cmb_lang = ctk.CTkComboBox(
            options_card,
            values=["ron+eng", "eng", "ron"],
            state="readonly",
            command=lambda v: self._save_settings_from_ui(),
        )
        self.cmb_lang.grid(row=2, column=1, padx=(4, 12), pady=3, sticky="ew")

        ctk.CTkLabel(options_card, text="Render DPI:").grid(row=3, column=0, padx=(12, 4), pady=3, sticky="w")
        self.cmb_dpi = ctk.CTkComboBox(
            options_card,
            values=["300", "200", "150"],
            state="readonly",
            command=lambda v: self._save_settings_from_ui(),
        )
        self.cmb_dpi.grid(row=3, column=1, padx=(4, 12), pady=3, sticky="ew")

        self.chk_metadata = ctk.CTkCheckBox(
            options_card,
            text="Fetch online book metadata (OpenLibrary / Google Books)",
            font=ctk.CTkFont(size=11),
            command=self._save_settings_from_ui,
        )
        self.chk_metadata.grid(row=4, column=0, columnspan=2, padx=12, pady=(6, 4), sticky="w")

        btn_open_out = ctk.CTkButton(
            options_card,
            text="📁 Open Output Folder",
            height=28,
            fg_color="#37474F",
            hover_color="#455A64",
            command=self._open_output_dir,
        )
        btn_open_out.grid(row=5, column=0, columnspan=2, padx=12, pady=(6, 10), sticky="ew")

        # Card 3: Conversion Progress
        prog_card = ctk.CTkFrame(right_col)
        prog_card.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        prog_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            prog_card,
            text="📊 Conversion Progress",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        self.lbl_batch_prog = ctk.CTkLabel(prog_card, text="Batch Progress: 0 / 0 books (0%)", font=ctk.CTkFont(size=11))
        self.lbl_batch_prog.grid(row=1, column=0, padx=12, pady=(2, 2), sticky="w")
        self.bar_batch_prog = ctk.CTkProgressBar(prog_card)
        self.bar_batch_prog.grid(row=2, column=0, padx=12, pady=(0, 6), sticky="ew")
        self.bar_batch_prog.set(0)

        self.lbl_book_prog = ctk.CTkLabel(prog_card, text="Current Book: Idle", font=ctk.CTkFont(size=11))
        self.lbl_book_prog.grid(row=3, column=0, padx=12, pady=(2, 2), sticky="w")
        self.bar_book_prog = ctk.CTkProgressBar(prog_card)
        self.bar_book_prog.grid(row=4, column=0, padx=12, pady=(0, 10), sticky="ew")
        self.bar_book_prog.set(0)

    # =========================================================================
    # TAB 2: WALLPAPERS (SCREENSAVERS)
    # =========================================================================

    def _build_wallpapers_tab(self, parent):
        parent.grid_columnconfigure(0, weight=6)
        parent.grid_columnconfigure(1, weight=5)
        parent.grid_rowconfigure(0, weight=1)

        # Left Column: Wallpapers Queue
        left_col = ctk.CTkFrame(parent)
        left_col.grid(row=0, column=0, padx=(0, 8), pady=4, sticky="nsew")
        left_col.grid_columnconfigure(0, weight=1)
        left_col.grid_rowconfigure(1, weight=1)

        tb_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        tb_frame.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")

        btn_add = ctk.CTkButton(
            tb_frame,
            text="+ Add Wallpapers (PNG / JPG / WEBP)",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._add_wallpaper_files_dialog,
        )
        btn_add.pack(side="left", padx=(0, 6))

        btn_folder = ctk.CTkButton(
            tb_frame,
            text="+ Folder",
            width=80,
            fg_color="#37474F",
            hover_color="#455A64",
            command=self._add_wallpaper_folder_dialog,
        )
        btn_folder.pack(side="left", padx=4)

        btn_clear = ctk.CTkButton(
            tb_frame,
            text="Clear",
            width=70,
            fg_color="transparent",
            border_width=1,
            hover_color="#552222",
            command=self._clear_wallpaper_queue,
        )
        btn_clear.pack(side="right")

        self.scroll_wallpapers = ctk.CTkScrollableFrame(
            left_col,
            label_text="Screensavers & Wallpapers Queue",
            label_font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.scroll_wallpapers.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="nsew")
        self.scroll_wallpapers.grid_columnconfigure(0, weight=1)

        self.lbl_empty_wallpapers = ctk.CTkLabel(
            self.scroll_wallpapers,
            text="No wallpapers added.\nAdd PNG, JPG, or WEBP images to upload to your Kindle.",
            text_color="#777777",
            font=ctk.CTkFont(size=12),
        )
        self.lbl_empty_wallpapers.pack(pady=50)

        # Bottom Upload CTA
        cta_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        cta_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
        cta_frame.grid_columnconfigure(0, weight=1)

        self.btn_upload_all_wallpapers = ctk.CTkButton(
            cta_frame,
            text="🚀 UPLOAD ALL TO E-READER",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=38,
            fg_color="#0284C7",
            hover_color="#0369A1",
            command=self._upload_all_wallpapers,
        )
        self.btn_upload_all_wallpapers.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        # Right Column: Settings & Guide
        right_col = ctk.CTkFrame(parent, fg_color="transparent")
        right_col.grid(row=0, column=1, padx=(8, 0), pady=4, sticky="nsew")
        right_col.grid_columnconfigure(0, weight=1)

        # Connection Card
        conn_card = ctk.CTkFrame(right_col)
        conn_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        conn_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            conn_card,
            text="📱 E-Reader KOReader Connection (Wi-Fi)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 6), sticky="w")

        ctk.CTkLabel(conn_card, text="E-Reader IP:").grid(row=1, column=0, padx=(12, 4), pady=3, sticky="w")
        self.entry_ip_wallpapers = ctk.CTkEntry(conn_card, placeholder_text="e.g. 192.168.43.150")
        self.entry_ip_wallpapers.grid(row=1, column=1, padx=(4, 12), pady=3, sticky="ew")
        self.entry_ip_wallpapers.bind("<KeyRelease>", lambda e: self._on_ip_entry_changed(self.entry_ip_wallpapers.get()))

        ctk.CTkLabel(conn_card, text="Screensavers Folder:").grid(row=2, column=0, padx=(12, 4), pady=3, sticky="w")
        self.entry_wallpapers_dir = ctk.CTkEntry(conn_card, placeholder_text="/mnt/us/koreader/screensavers")
        self.entry_wallpapers_dir.grid(row=2, column=1, padx=(4, 12), pady=3, sticky="ew")
        self.entry_wallpapers_dir.bind("<KeyRelease>", lambda e: self._on_wallpapers_dir_changed())

        # Guide Card
        guide_card = ctk.CTkFrame(right_col)
        guide_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        guide_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            guide_card,
            text="ℹ️ KOReader Screensaver Guide",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        guide_text = (
            "• Images sent here are used by KOReader as sleep screensavers.\n"
            "• Recommended resolution for Kindle Colorsoft: 1264 × 1680 px.\n"
            "• Supported formats: PNG, JPG, JPEG, WEBP.\n"
            "• Direct transfer over Wi-Fi — no file conversion required."
        )
        ctk.CTkLabel(
            guide_card,
            text=guide_text,
            font=ctk.CTkFont(size=11),
            text_color="#AAAAAA",
            justify="left",
        ).grid(row=1, column=0, padx=12, pady=(0, 10), sticky="w")

        # Upload Progress Card
        prog_card = ctk.CTkFrame(right_col)
        prog_card.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        prog_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            prog_card,
            text="📊 Upload Progress",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        self.lbl_wallpapers_prog = ctk.CTkLabel(prog_card, text="Uploaded: 0 / 0 images", font=ctk.CTkFont(size=11))
        self.lbl_wallpapers_prog.grid(row=1, column=0, padx=12, pady=(2, 2), sticky="w")
        self.bar_wallpapers_prog = ctk.CTkProgressBar(prog_card)
        self.bar_wallpapers_prog.grid(row=2, column=0, padx=12, pady=(0, 10), sticky="ew")
        self.bar_wallpapers_prog.set(0)

    # =========================================================================
    # TAB 3: ORNAMENTS (BOOKSHELF PLUGIN)
    # =========================================================================

    def _build_ornaments_tab(self, parent):
        parent.grid_columnconfigure(0, weight=6)
        parent.grid_columnconfigure(1, weight=5)
        parent.grid_rowconfigure(0, weight=1)

        # Left Column: Ornaments Queue
        left_col = ctk.CTkFrame(parent)
        left_col.grid(row=0, column=0, padx=(0, 8), pady=4, sticky="nsew")
        left_col.grid_columnconfigure(0, weight=1)
        left_col.grid_rowconfigure(1, weight=1)

        tb_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        tb_frame.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")

        btn_add = ctk.CTkButton(
            tb_frame,
            text="+ Add Ornaments (PNG / JPG / SVG)",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._add_ornament_files_dialog,
        )
        btn_add.pack(side="left", padx=(0, 6))

        btn_folder = ctk.CTkButton(
            tb_frame,
            text="+ Folder",
            width=80,
            fg_color="#37474F",
            hover_color="#455A64",
            command=self._add_ornament_folder_dialog,
        )
        btn_folder.pack(side="left", padx=4)

        btn_clear = ctk.CTkButton(
            tb_frame,
            text="Clear",
            width=70,
            fg_color="transparent",
            border_width=1,
            hover_color="#552222",
            command=self._clear_ornament_queue,
        )
        btn_clear.pack(side="right")

        self.scroll_ornaments = ctk.CTkScrollableFrame(
            left_col,
            label_text="Bookshelf Ornaments Queue",
            label_font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.scroll_ornaments.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="nsew")
        self.scroll_ornaments.grid_columnconfigure(0, weight=1)

        self.lbl_empty_ornaments = ctk.CTkLabel(
            self.scroll_ornaments,
            text="No ornaments added.\nAdd transparent PNG icons for KOReader Bookshelf plugin.",
            text_color="#777777",
            font=ctk.CTkFont(size=12),
        )
        self.lbl_empty_ornaments.pack(pady=50)

        # Bottom Upload CTA
        cta_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        cta_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
        cta_frame.grid_columnconfigure(0, weight=1)

        self.btn_upload_all_ornaments = ctk.CTkButton(
            cta_frame,
            text="🚀 UPLOAD ALL TO E-READER",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=38,
            fg_color="#7E22CE",
            hover_color="#6B21A8",
            command=self._upload_all_ornaments,
        )
        self.btn_upload_all_ornaments.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        # Right Column: Settings & Guide
        right_col = ctk.CTkFrame(parent, fg_color="transparent")
        right_col.grid(row=0, column=1, padx=(8, 0), pady=4, sticky="nsew")
        right_col.grid_columnconfigure(0, weight=1)

        # Connection Card
        conn_card = ctk.CTkFrame(right_col)
        conn_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        conn_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            conn_card,
            text="📱 E-Reader KOReader Connection (Wi-Fi)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 6), sticky="w")

        ctk.CTkLabel(conn_card, text="E-Reader IP:").grid(row=1, column=0, padx=(12, 4), pady=3, sticky="w")
        self.entry_ip_ornaments = ctk.CTkEntry(conn_card, placeholder_text="e.g. 192.168.43.150")
        self.entry_ip_ornaments.grid(row=1, column=1, padx=(4, 12), pady=3, sticky="ew")
        self.entry_ip_ornaments.bind("<KeyRelease>", lambda e: self._on_ip_entry_changed(self.entry_ip_ornaments.get()))

        ctk.CTkLabel(conn_card, text="Ornaments Folder:").grid(row=2, column=0, padx=(12, 4), pady=3, sticky="w")
        self.entry_ornaments_dir = ctk.CTkEntry(conn_card, placeholder_text="/mnt/us/koreader/settings/bookshelf/ornaments")
        self.entry_ornaments_dir.grid(row=2, column=1, padx=(4, 12), pady=3, sticky="ew")
        self.entry_ornaments_dir.bind("<KeyRelease>", lambda e: self._on_ornaments_dir_changed())

        # Guide Card
        guide_card = ctk.CTkFrame(right_col)
        guide_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        guide_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            guide_card,
            text="✨ KOReader Bookshelf Ornaments Guide",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        guide_text = (
            "• Universal folder for the KOReader Bookshelf plugin.\n"
            "• Target: /mnt/us/koreader/settings/bookshelf/ornaments\n"
            "• Used as shelf dividers, decorative bookends, and category icons.\n"
            "• Transparent PNG images recommended for best display on e-ink."
        )
        ctk.CTkLabel(
            guide_card,
            text=guide_text,
            font=ctk.CTkFont(size=11),
            text_color="#AAAAAA",
            justify="left",
        ).grid(row=1, column=0, padx=12, pady=(0, 10), sticky="w")

        # Upload Progress Card
        prog_card = ctk.CTkFrame(right_col)
        prog_card.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        prog_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            prog_card,
            text="📊 Upload Progress",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        self.lbl_ornaments_prog = ctk.CTkLabel(prog_card, text="Uploaded: 0 / 0 ornaments", font=ctk.CTkFont(size=11))
        self.lbl_ornaments_prog.grid(row=1, column=0, padx=12, pady=(2, 2), sticky="w")
        self.bar_ornaments_prog = ctk.CTkProgressBar(prog_card)
        self.bar_ornaments_prog.grid(row=2, column=0, padx=12, pady=(0, 10), sticky="ew")
        self.bar_ornaments_prog.set(0)

    # =========================================================================
    # CONFIG SYNC & STATUS
    # =========================================================================

    def _load_config_into_ui(self):
        """Populates UI controls with stored config values."""
        ip = self.config.kindle_ip
        self.entry_ip_books.delete(0, "end")
        self.entry_ip_books.insert(0, ip)
        self.entry_ip_wallpapers.delete(0, "end")
        self.entry_ip_wallpapers.insert(0, ip)
        self.entry_ip_ornaments.delete(0, "end")
        self.entry_ip_ornaments.insert(0, ip)

        self.entry_books_dir.delete(0, "end")
        self.entry_books_dir.insert(0, self.config.remote_dir)

        self.entry_wallpapers_dir.delete(0, "end")
        self.entry_wallpapers_dir.insert(0, getattr(self.config, "remote_screensavers_dir", "/mnt/us/koreader/screensavers"))

        self.entry_ornaments_dir.delete(0, "end")
        self.entry_ornaments_dir.insert(0, getattr(self.config, "remote_ornaments_dir", "/mnt/us/koreader/settings/bookshelf/ornaments"))

        if self.config.auto_send:
            self.chk_autosend_books.select()
        else:
            self.chk_autosend_books.deselect()

        if self.config.fetch_metadata:
            self.chk_metadata.select()
        else:
            self.chk_metadata.deselect()

        self.cmb_lang.set(self.config.ocr_lang)
        self.cmb_dpi.set(str(self.config.dpi))

        self.lbl_conn_pill.configure(text=f"● E-Reader: {self.config.kindle_ip}:{self.config.kindle_port}")

    def _on_ip_entry_changed(self, new_ip: str):
        cleaned_ip = new_ip.strip()
        self.config.kindle_ip = cleaned_ip
        # Keep all three entry inputs in sync
        for entry in [self.entry_ip_books, self.entry_ip_wallpapers, self.entry_ip_ornaments]:
            if entry.get().strip() != cleaned_ip:
                entry.delete(0, "end")
                entry.insert(0, cleaned_ip)
        self.lbl_conn_pill.configure(text=f"● E-Reader: {cleaned_ip}:{self.config.kindle_port}")
        save_config(self.config)

    def _on_books_dir_changed(self):
        val = self.entry_books_dir.get().strip()
        if val and val != self.config.remote_dir:
            self.config.remote_dir = val
            save_config(self.config)

    def _on_wallpapers_dir_changed(self):
        val = self.entry_wallpapers_dir.get().strip()
        if val:
            self.config.remote_screensavers_dir = val
            save_config(self.config)

    def _on_ornaments_dir_changed(self):
        val = self.entry_ornaments_dir.get().strip()
        if val:
            self.config.remote_ornaments_dir = val
            save_config(self.config)

    def _on_autosend_toggled(self):
        self.config.auto_send = bool(self.chk_autosend_books.get())
        save_config(self.config)

    def _save_settings_from_ui(self):
        self.config.kindle_ip = self.entry_ip_books.get().strip()
        self.config.remote_dir = self.entry_books_dir.get().strip() or "/mnt/us/Carti/Biblioteca"
        self.config.remote_screensavers_dir = self.entry_wallpapers_dir.get().strip() or "/mnt/us/koreader/screensavers"
        self.config.remote_ornaments_dir = self.entry_ornaments_dir.get().strip() or "/mnt/us/koreader/settings/bookshelf/ornaments"
        self.config.auto_send = bool(self.chk_autosend_books.get())
        self.config.fetch_metadata = bool(self.chk_metadata.get())
        if hasattr(self, "seg_format"):
            self.config.target_format = "PDF" if "PDF" in self.seg_format.get() else "EPUB"
        self.config.ocr_lang = self.cmb_lang.get().strip()
        try:
            self.config.dpi = int(self.cmb_dpi.get().strip())
        except ValueError:
            self.config.dpi = 300
        save_config(self.config)

    def _on_format_changed(self, value: str):
        is_pdf = "PDF" in value
        self.config.target_format = "PDF" if is_pdf else "EPUB"
        save_config(self.config)

        for it in self.queue_items:
            it.target_format = self.config.target_format
            w = self.queue_widgets.get(it.item_id)
            if w:
                w.update_format_button()
                w.update_view()
        self._save_queue_history()

        if is_pdf:
            self.btn_start_conversion.configure(text="📤 SEND ALL AS PDF (WITH META)", fg_color="#00695C", hover_color="#004D40")
            self.set_status("Target format set to PDF (Direct + Embedded Metadata).")
        else:
            self.btn_start_conversion.configure(text="🚀 START CONVERSION (EPUB)", fg_color="#1B5E20", hover_color="#2E7D32")
            self.set_status("Target format set to EPUB (Reflowable Conversion).")

    def set_status(self, text: str):
        """Sets the bottom status bar message."""
        self.lbl_status_bar.configure(text=text)

    def _change_theme(self, mode: str):
        ctk.set_appearance_mode(mode)
        self.config.appearance_mode = mode
        save_config(self.config)

    # =========================================================================
    # BOOKS TAB LOGIC
    # =========================================================================

    def _add_book_files_dialog(self):
        paths = filedialog.askopenfilenames(
            title="Select PDF or EPUB Books",
            filetypes=[("E-books (PDF & EPUB)", "*.pdf;*.epub"), ("PDF Documents", "*.pdf"), ("EPUB E-books", "*.epub"), ("All Files", "*.*")],
        )
        if paths:
            self._add_books_to_queue(list(paths))

    def _add_book_folder_dialog(self):
        folder = filedialog.askdirectory(title="Select Folder Containing Books")
        if folder:
            p_folder = Path(folder)
            found = []
            try:
                for item in p_folder.iterdir():
                    if item.is_file() and item.suffix.lower() in [".pdf", ".epub"]:
                        found.append(str(item.resolve()))
            except Exception:
                pass
            if not found:
                for root, _, files in os.walk(folder):
                    for file in files:
                        if Path(file).suffix.lower() in [".pdf", ".epub"]:
                            found.append(os.path.join(root, file))
            if found:
                self._add_books_to_queue(found)
            else:
                messagebox.showinfo("No Books Found", "No PDF or EPUB files were found in the selected folder.")

    def _add_books_to_queue(self, file_paths: List[str]):
        added_count = 0
        existing_paths = {it.pdf_path for it in self.queue_items}
        new_items = []

        for p in file_paths:
            path_obj = Path(p)
            abs_p = str(path_obj.resolve())
            if not path_obj.is_file() or abs_p in existing_paths or p in existing_paths:
                continue

            is_epub = path_obj.suffix.lower() == ".epub"
            out_epub_path = abs_p if is_epub else ""
            status = "EPUB Ready" if is_epub else "Pending"
            item_id = f"book_{int(time.time() * 1000)}_{len(self.queue_items)}"

            # Metadata parsing from filename (intelligent author & title split)
            init_author, init_title = MetadataFetcher.split_filename_author_title(path_obj.name)
            initial_title = init_title or MetadataFetcher.clean_web_junk(path_obj.stem)
            initial_author = init_author or ""
            initial_genre = MetadataFetcher.classify_genre(initial_title, initial_author)
            initial_series = ""

            # Read internal PDF/EPUB metadata
            if not is_epub:
                try:
                    import fitz
                    with fitz.open(p) as doc:
                        pdf_meta = doc.metadata or {}
                        int_t = (pdf_meta.get("title") or "").strip()
                        int_a = (pdf_meta.get("author") or "").strip()
                        if int_t and len(int_t) >= 2 and not int_t.lower().endswith(".pdf") and (not initial_title or initial_title == path_obj.stem):
                            initial_title = int_t
                        if int_a and len(int_a) >= 2 and not initial_author:
                            initial_author = int_a
                except Exception:
                    pass

            item = QueueItem(
                item_id=item_id,
                pdf_path=abs_p,
                file_name=path_obj.name,
                title=initial_title,
                author=initial_author,
                genre=initial_genre if initial_genre else "Fiction",
                series=initial_series,
                file_type="epub" if is_epub else "pdf",
                total_pages=0,
                status=status,
                output_epub_path=out_epub_path,
                target_format=getattr(self.config, "target_format", "EPUB"),
            )

            widget = QueueItemWidget(
                self.scroll_books_queue,
                item=item,
                on_delete=self._remove_queue_item,
                on_upload=self._upload_single_book,
                on_edit=self._open_metadata_dialog,
            )
            widget.pack(fill="x", pady=3, padx=4)

            self.queue_items.append(item)
            self.queue_widgets[item_id] = widget
            new_items.append(item)
            existing_paths.add(abs_p)
            added_count += 1

        if added_count > 0 and self.lbl_empty_books:
            self.lbl_empty_books.pack_forget()

        self._update_batch_progress_ui(0, len(self.queue_items))
        self._save_queue_history()
        if added_count > 0:
            self.set_status(f"Added {added_count} book(s) to queue.")
        elif file_paths:
            self.set_status("All selected books are already in the list.")
            messagebox.showinfo("Already in Queue", f"All {len(file_paths)} selected book(s) are already in the list.")

        if new_items and getattr(self.config, "fetch_metadata", True):
            threading.Thread(target=self._fetch_metadata_async, args=(new_items,), daemon=True).start()

    def _fetch_metadata_async(self, items: List[QueueItem]):
        """Fetches metadata online (Google Books, OpenLibrary) in background."""
        for item in items:
            if not item.genre or item.genre == "Fiction":
                item.genre = MetadataFetcher.classify_genre(item.title, item.author)
            if not item.title or not item.author or item.author in ["Unknown Author", "Autor Necunoscut"] or not item.series:
                meta = MetadataFetcher.fetch_book_metadata(item.file_name)
                if meta:
                    if meta.get("title") and (not item.title or item.title == item.file_name):
                        item.title = meta["title"]
                    if meta.get("author") and meta["author"] not in ["Unknown Author", "Autor Necunoscut"]:
                        item.author = meta["author"]
                    if meta.get("genre"):
                        item.genre = meta["genre"]
                    if meta.get("series"):
                        item.series = meta["series"]

            def update_ui(i=item):
                w = self.queue_widgets.get(i.item_id)
                if w:
                    w.update_view()

            self.after(0, update_ui)

        self.after(0, self._save_queue_history)

    def _remove_queue_item(self, item_id: str):
        widget = self.queue_widgets.pop(item_id, None)
        if widget:
            widget.destroy()

        self.queue_items = [it for it in self.queue_items if it.item_id != item_id]

        if not self.queue_items:
            self.lbl_empty_books.pack(pady=50)

        self._update_batch_progress_ui(0, len(self.queue_items))
        self._save_queue_history()
        self.set_status("Book removed from queue.")

    def _clear_books_queue(self):
        if self.runner and self.runner.is_running:
            messagebox.showwarning("Busy", "Cannot clear queue while conversion is in progress.")
            return

        for w in self.queue_widgets.values():
            w.destroy()
        self.queue_widgets.clear()
        self.queue_items.clear()
        self.lbl_empty_books.pack(pady=50)

        self._update_batch_progress_ui(0, 0)
        self.bar_book_prog.set(0)
        self.lbl_book_prog.configure(text="Current Book: Idle")
        self._save_queue_history()
        self.set_status("Books queue cleared.")

    def _open_metadata_dialog(self, item: QueueItem):
        def on_saved(updated_item: QueueItem):
            widget = self.queue_widgets.get(updated_item.item_id)
            if widget:
                widget.update_view()
            self._save_queue_history()
            self.set_status(f"Updated metadata for '{updated_item.title}'")

        MetadataEditDialog(self, item, on_save_callback=on_saved)

    def _prepare_pdf_with_metadata(self, item: QueueItem) -> str:
        """
        Embeds metadata (Title, Author, Genre/Subject, Series/Keywords) into a copy of the PDF
        in the output directory without modifying or damaging the original source file.
        Returns the path to the updated PDF file ready for E-Reader transfer.
        """
        import fitz
        import shutil

        out_dir = Path(self.config.output_dir) if self.config.output_dir else (Path.home() / "Documents" / "K-Bridge Books")
        out_dir.mkdir(parents=True, exist_ok=True)

        clean_t = "".join(c for c in (item.title or Path(item.pdf_path).stem) if c.isalnum() or c in (" ", "-", "_", ".")).strip()
        clean_a = "".join(c for c in (item.author or "") if c.isalnum() or c in (" ", "-", "_", ".")).strip()
        if clean_a and clean_t and clean_a.lower() not in clean_t.lower():
            out_filename = f"{clean_a} - {clean_t}.pdf"
        elif clean_t:
            out_filename = f"{clean_t}.pdf"
        else:
            out_filename = Path(item.pdf_path).name

        target_path = out_dir / out_filename
        shutil.copy2(item.pdf_path, target_path)

        try:
            doc = fitz.open(str(target_path))
            meta = doc.metadata or {}
            if item.title:
                meta["title"] = item.title
            if item.author:
                meta["author"] = item.author
            if item.genre:
                meta["subject"] = item.genre
            if item.series:
                meta["keywords"] = item.series
            doc.set_metadata(meta)
            doc.save(str(target_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            doc.close()
        except Exception as e:
            print(f"[Warning] PDF metadata injection error: {e}")

        return str(target_path)

    def _upload_single_book(self, item: QueueItem):
        self._save_settings_from_ui()
        ip = self.config.kindle_ip
        if not ip:
            messagebox.showwarning("E-Reader IP Missing", "Please enter your E-Reader's IP address.")
            return

        remote_dir = self.config.remote_dir
        book_name = item.title if item.title else item.file_name

        # Check format preference:
        is_pdf_mode = getattr(item, "target_format", "EPUB") == "PDF"

        if is_pdf_mode:
            if not item.pdf_path or not os.path.isfile(item.pdf_path):
                messagebox.showerror("File Missing", f"Could not find PDF file for '{book_name}' on disk.")
                return
            self.set_status(f"Embedding metadata into PDF '{book_name}'...")
            try:
                target_file = self._prepare_pdf_with_metadata(item)
            except Exception as e:
                messagebox.showerror("Metadata Injection Failed", f"Could not embed metadata into PDF:\n{e}")
                return
            fmt_label = "PDF"
        else:
            # EPUB Mode
            if item.output_epub_path and os.path.isfile(item.output_epub_path):
                target_file = item.output_epub_path
                fmt_label = "EPUB"
            elif item.file_type == "epub" or item.pdf_path.lower().endswith(".epub"):
                target_file = item.pdf_path
                fmt_label = "EPUB"
            else:
                ans = messagebox.askyesno(
                    "EPUB Not Converted",
                    f"The book '{item.file_name}' has not been converted to EPUB yet.\n\n"
                    "Would you like to embed Title, Author & Genre metadata and transfer it directly as PDF instead?",
                )
                if not ans:
                    return
                self.set_status(f"Embedding metadata into PDF '{book_name}'...")
                target_file = self._prepare_pdf_with_metadata(item)
                fmt_label = "PDF"

        self.set_status(f"Uploading {fmt_label} '{book_name}' to E-Reader ({ip})...")

        def run_upload():
            client = KindleSFTPClient(
                host=ip,
                port=self.config.kindle_port,
                username=self.config.kindle_user,
                password=self.config.kindle_password,
                remote_dir=remote_dir,
            )
            success, msg = client.upload_file(target_file, target_remote_dir=remote_dir)

            def finish():
                if success:
                    item.status = "Sent to E-Reader"
                    self._update_book_item_status_ui(item)
                    self._save_queue_history()
                    self.set_status(f"Uploaded {fmt_label}: '{book_name}'")
                    messagebox.showinfo("Upload Successful", f"Transferred to E-Reader in:\n{remote_dir}\n\nFile: {os.path.basename(target_file)}")
                else:
                    self.set_status(f"Upload failed: {msg}")
                    messagebox.showerror("SSH Transfer Failed", msg)

            self.after(0, finish)

        threading.Thread(target=run_upload, daemon=True).start()

    def _upload_all_books_direct(self):
        if not self.queue_items:
            messagebox.showwarning("Empty Queue", "Please add at least one book before sending.")
            return

        self._save_settings_from_ui()
        ip = self.config.kindle_ip
        if not ip:
            messagebox.showwarning("E-Reader IP Missing", "Please enter your E-Reader's IP address.")
            return

        remote_dir = self.config.remote_dir
        ans = messagebox.askyesno(
            "Direct Transfer (With Metadata)",
            f"Transfer all {len(self.queue_items)} book(s) directly to your E-Reader at {ip}?\n\n"
            "Each book will have its Title, Author and Genre embedded into its metadata before uploading.",
        )
        if not ans:
            return

        if hasattr(self, "btn_direct_upload"):
            self.btn_direct_upload.configure(state="disabled")
        self.btn_start_conversion.configure(state="disabled")
        self.set_status(f"Starting direct transfer of {len(self.queue_items)} books...")

        def run_batch_upload():
            client = KindleSFTPClient(
                host=ip,
                port=self.config.kindle_port,
                username=self.config.kindle_user,
                password=self.config.kindle_password,
                remote_dir=remote_dir,
            )
            success_count = 0
            fail_count = 0
            total = len(self.queue_items)

            for idx, it in enumerate(self.queue_items):
                target_file = ""
                b_name = it.title if it.title else it.file_name

                # Check format
                is_pdf_mode = getattr(it, "target_format", "EPUB") == "PDF" or not it.output_epub_path

                if is_pdf_mode:
                    if it.file_type == "epub" or it.pdf_path.lower().endswith(".epub"):
                        target_file = it.pdf_path
                    else:
                        self.after(0, lambda n=b_name: self.set_status(f"Embedding metadata into PDF '{n}'..."))
                        try:
                            target_file = self._prepare_pdf_with_metadata(it)
                        except Exception:
                            target_file = it.pdf_path
                else:
                    target_file = it.output_epub_path

                if not target_file or not os.path.isfile(target_file):
                    fail_count += 1
                    continue

                self.after(0, lambda n=b_name, i=idx: self.set_status(f"Uploading [{i+1}/{total}]: '{n}'..."))
                ok, msg = client.upload_file(target_file, target_remote_dir=remote_dir)
                if ok:
                    success_count += 1
                    it.status = "Sent to E-Reader"
                    self.after(0, lambda item=it: self._update_book_item_status_ui(item))
                else:
                    fail_count += 1

                self.after(0, lambda i=idx: self._update_batch_progress_ui(i + 1, total))

            def finish():
                if hasattr(self, "btn_direct_upload"):
                    self.btn_direct_upload.configure(state="normal")
                self.btn_start_conversion.configure(state="normal")
                self._save_queue_history()
                msg = f"Direct Transfer Finished!\n\nSuccessful: {success_count}\nFailed: {fail_count}"
                self.set_status(f"Direct transfer complete. Sent: {success_count}, Failed: {fail_count}")
                if fail_count == 0 and success_count > 0:
                    messagebox.showinfo("Transfer Completed", msg)
                else:
                    messagebox.showwarning("Transfer Completed", msg)

            self.after(0, finish)

        threading.Thread(target=run_batch_upload, daemon=True).start()

    def _start_conversion(self):
        if not self.queue_items:
            messagebox.showwarning("Empty Queue", "Please add at least one book before starting.")
            return

        self._save_settings_from_ui()

        # If global format is set to PDF, perform direct transfer with metadata
        if getattr(self.config, "target_format", "EPUB") == "PDF":
            self._upload_all_books_direct()
            return

        self.btn_start_conversion.configure(state="disabled")
        self.btn_stop_conversion.configure(state="normal")
        self.set_status("Starting batch conversion...")

        self.runner = BatchRunner(
            config=self.config,
            on_batch_progress=lambda comp, tot: self.after(0, lambda: self._update_batch_progress_ui(comp, tot)),
            on_book_progress=lambda comp, tot: self.after(0, lambda: self._update_book_progress_ui(comp, tot)),
            on_item_status=lambda item: self.after(0, lambda: self._update_book_item_status_ui(item)),
            on_log=lambda msg, lvl: self.after(0, lambda: self.set_status(msg)),
            on_finished=lambda s, f: self.after(0, lambda: self._conversion_finished(s, f)),
        )
        self.runner.start(self.queue_items)

    def _stop_conversion(self):
        if self.runner and self.runner.is_running:
            self.runner.stop()
            self.btn_stop_conversion.configure(state="disabled")
            self.set_status("Stopping conversion...")

    def _conversion_finished(self, success_count: int, fail_count: int):
        self.btn_start_conversion.configure(state="normal")
        self.btn_stop_conversion.configure(state="disabled")
        self.lbl_book_prog.configure(text="Current Book: Completed")
        self.bar_book_prog.set(1.0)
        self._save_queue_history()

        msg = f"Batch conversion finished!\n\nSuccessful: {success_count}\nFailed: {fail_count}"
        self.set_status(f"Conversion complete. Successful: {success_count}, Failed: {fail_count}")
        if fail_count == 0 and success_count > 0:
            messagebox.showinfo("Completed", msg)
        else:
            messagebox.showwarning("Completed with Warnings", msg)

    def _update_batch_progress_ui(self, completed: int, total: int):
        if total == 0:
            self.bar_batch_prog.set(0)
            self.lbl_batch_prog.configure(text="Batch Progress: 0 / 0 books (0%)")
            return
        ratio = min(1.0, max(0.0, completed / total))
        self.bar_batch_prog.set(ratio)
        self.lbl_batch_prog.configure(text=f"Batch Progress: {completed} / {total} books ({int(ratio * 100)}%)")

    def _update_book_progress_ui(self, current_page: int, total_pages: int):
        if total_pages == 0:
            self.bar_book_prog.set(0)
            self.lbl_book_prog.configure(text="Current Book: Processing...")
            return
        ratio = min(1.0, max(0.0, current_page / total_pages))
        self.bar_book_prog.set(ratio)
        self.lbl_book_prog.configure(text=f"Current Book: Page {current_page} / {total_pages} ({int(ratio * 100)}%)")

    def _update_book_item_status_ui(self, item: QueueItem):
        widget = self.queue_widgets.get(item.item_id)
        if widget:
            widget.update_status(item.status)

    def _open_output_dir(self):
        out_dir = self.config.output_dir
        path = Path(out_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])

    # =========================================================================
    # TAB 2: WALLPAPERS (SCREENSAVERS) LOGIC
    # =========================================================================

    def _add_wallpaper_files_dialog(self):
        paths = filedialog.askopenfilenames(
            title="Select Wallpaper Images",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("PNG Images", "*.png"), ("JPG Images", "*.jpg;*.jpeg"), ("All Files", "*.*")],
        )
        if paths:
            self._add_wallpapers_to_queue(list(paths))

    def _add_wallpaper_folder_dialog(self):
        folder = filedialog.askdirectory(title="Select Folder with Wallpapers")
        if folder:
            p_folder = Path(folder)
            img_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
            found = []
            try:
                for item in p_folder.iterdir():
                    if item.is_file() and item.suffix.lower() in img_exts:
                        found.append(str(item.resolve()))
            except Exception:
                pass
            if not found:
                for root, _, files in os.walk(folder):
                    for file in files:
                        if Path(file).suffix.lower() in img_exts:
                            found.append(os.path.join(root, file))
            if found:
                self._add_wallpapers_to_queue(found)
            else:
                messagebox.showinfo("No Images Found", "No supported image files found in the selected folder.")

    def _add_wallpapers_to_queue(self, file_paths: List[str]):
        added = 0
        existing = {it.file_path for it in self.wallpaper_items}
        for p in file_paths:
            path_obj = Path(p)
            if not path_obj.is_file() or p in existing:
                continue
            size_str = format_file_size(path_obj.stat().st_size)
            item_id = f"wall_{int(time.time() * 1000)}_{len(self.wallpaper_items)}"
            item = MediaItem(
                item_id=item_id,
                file_path=p,
                file_name=path_obj.name,
                file_size_str=size_str,
                status="Pending",
            )
            widget = MediaItemWidget(
                self.scroll_wallpapers,
                item=item,
                icon_emoji="🖼️",
                on_delete=self._remove_wallpaper_item,
                on_upload=self._upload_single_wallpaper,
            )
            widget.pack(fill="x", pady=3, padx=4)
            self.wallpaper_items.append(item)
            self.wallpaper_widgets[item_id] = widget
            existing.add(p)
            added += 1

        if added > 0 and self.lbl_empty_wallpapers:
            self.lbl_empty_wallpapers.pack_forget()

        self._update_wallpapers_progress_ui(0, len(self.wallpaper_items))
        self.set_status(f"Added {added} wallpaper(s) to queue.")

    def _remove_wallpaper_item(self, item_id: str):
        widget = self.wallpaper_widgets.pop(item_id, None)
        if widget:
            widget.destroy()
        self.wallpaper_items = [it for it in self.wallpaper_items if it.item_id != item_id]
        if not self.wallpaper_items:
            self.lbl_empty_wallpapers.pack(pady=50)
        self._update_wallpapers_progress_ui(0, len(self.wallpaper_items))
        self.set_status("Wallpaper removed from queue.")

    def _clear_wallpaper_queue(self):
        if self.is_uploading_wallpapers:
            messagebox.showwarning("Busy", "Cannot clear queue while upload is active.")
            return
        for w in self.wallpaper_widgets.values():
            w.destroy()
        self.wallpaper_widgets.clear()
        self.wallpaper_items.clear()
        self.lbl_empty_wallpapers.pack(pady=50)
        self._update_wallpapers_progress_ui(0, 0)
        self.set_status("Wallpaper queue cleared.")

    def _upload_single_wallpaper(self, item: MediaItem):
        self._save_settings_from_ui()
        ip = self.config.kindle_ip
        if not ip:
            messagebox.showwarning("E-Reader IP Missing", "Please enter your E-Reader's IP address.")
            return

        target_dir = self.config.remote_screensavers_dir or "/mnt/us/koreader/screensavers"
        self.set_status(f"Uploading wallpaper '{item.file_name}' to E-Reader...")
        widget = self.wallpaper_widgets.get(item.item_id)
        if widget:
            widget.update_status("Uploading...")

        def run_upload():
            client = KindleSFTPClient(
                host=ip,
                port=self.config.kindle_port,
                username=self.config.kindle_user,
                password=self.config.kindle_password,
                remote_dir=target_dir,
            )
            success, msg = client.upload_file(item.file_path, target_remote_dir=target_dir)

            def finish():
                if success:
                    item.status = "Uploaded"
                    if widget:
                        widget.update_status("Uploaded")
                    self.set_status(f"Uploaded: '{item.file_name}'")
                    messagebox.showinfo("Wallpaper Uploaded", f"Wallpaper sent to:\n{target_dir}/{item.file_name}")
                else:
                    item.status = "Failed"
                    if widget:
                        widget.update_status("Failed")
                    self.set_status(f"Upload failed: {msg}")
                    messagebox.showerror("Upload Failed", msg)

            self.after(0, finish)

        threading.Thread(target=run_upload, daemon=True).start()

    def _upload_all_wallpapers(self):
        if not self.wallpaper_items:
            messagebox.showwarning("Empty Queue", "Please add wallpaper images first.")
            return

        self._save_settings_from_ui()
        ip = self.config.kindle_ip
        if not ip:
            messagebox.showwarning("E-Reader IP Missing", "Please enter your E-Reader's IP address.")
            return

        target_dir = self.config.remote_screensavers_dir or "/mnt/us/koreader/screensavers"
        self.is_uploading_wallpapers = True
        self.btn_upload_all_wallpapers.configure(state="disabled")
        self.set_status(f"Uploading {len(self.wallpaper_items)} wallpapers to E-Reader...")

        def run_batch():
            client = KindleSFTPClient(
                host=ip,
                port=self.config.kindle_port,
                username=self.config.kindle_user,
                password=self.config.kindle_password,
                remote_dir=target_dir,
            )
            success_count = 0
            fail_count = 0

            for idx, it in enumerate(self.wallpaper_items):
                w = self.wallpaper_widgets.get(it.item_id)
                if w:
                    self.after(0, lambda w=w: w.update_status("Uploading..."))

                ok, msg = client.upload_file(it.file_path, target_remote_dir=target_dir)
                if ok:
                    success_count += 1
                    status_str = "Uploaded"
                else:
                    fail_count += 1
                    status_str = "Failed"

                it.status = status_str
                if w:
                    self.after(0, lambda w=w, s=status_str: w.update_status(s))
                self.after(0, lambda i=idx+1, t=len(self.wallpaper_items): self._update_wallpapers_progress_ui(i, t))

            def finish():
                self.is_uploading_wallpapers = False
                self.btn_upload_all_wallpapers.configure(state="normal")
                self.set_status(f"Wallpaper transfer complete. Uploaded: {success_count}, Failed: {fail_count}")
                messagebox.showinfo("Transfer Finished", f"Uploaded {success_count} wallpapers to:\n{target_dir}")

            self.after(0, finish)

        threading.Thread(target=run_batch, daemon=True).start()

    def _update_wallpapers_progress_ui(self, done: int, total: int):
        if total == 0:
            self.bar_wallpapers_prog.set(0)
            self.lbl_wallpapers_prog.configure(text="Uploaded: 0 / 0 images")
            return
        ratio = min(1.0, max(0.0, done / total))
        self.bar_wallpapers_prog.set(ratio)
        self.lbl_wallpapers_prog.configure(text=f"Uploaded: {done} / {total} images ({int(ratio * 100)}%)")

    # =========================================================================
    # TAB 3: ORNAMENTS (BOOKSHELF PLUGIN) LOGIC
    # =========================================================================

    def _add_ornament_files_dialog(self):
        paths = filedialog.askopenfilenames(
            title="Select Bookshelf Ornament Icons",
            filetypes=[("Icons (PNG / JPG / SVG)", "*.png;*.jpg;*.jpeg;*.svg"), ("PNG Icons", "*.png"), ("All Files", "*.*")],
        )
        if paths:
            self._add_ornaments_to_queue(list(paths))

    def _add_ornament_folder_dialog(self):
        folder = filedialog.askdirectory(title="Select Folder with Ornaments")
        if folder:
            p_folder = Path(folder)
            orn_exts = {".png", ".jpg", ".jpeg", ".svg"}
            found = []
            try:
                for item in p_folder.iterdir():
                    if item.is_file() and item.suffix.lower() in orn_exts:
                        found.append(str(item.resolve()))
            except Exception:
                pass
            if not found:
                for root, _, files in os.walk(folder):
                    for file in files:
                        if Path(file).suffix.lower() in orn_exts:
                            found.append(os.path.join(root, file))
            if found:
                self._add_ornaments_to_queue(found)
            else:
                messagebox.showinfo("No Icons Found", "No supported image icons found in the selected folder.")

    def _add_ornaments_to_queue(self, file_paths: List[str]):
        added = 0
        existing = {it.file_path for it in self.ornament_items}
        for p in file_paths:
            path_obj = Path(p)
            if not path_obj.is_file() or p in existing:
                continue
            size_str = format_file_size(path_obj.stat().st_size)
            item_id = f"orn_{int(time.time() * 1000)}_{len(self.ornament_items)}"
            item = MediaItem(
                item_id=item_id,
                file_path=p,
                file_name=path_obj.name,
                file_size_str=size_str,
                status="Pending",
            )
            widget = MediaItemWidget(
                self.scroll_ornaments,
                item=item,
                icon_emoji="✨",
                on_delete=self._remove_ornament_item,
                on_upload=self._upload_single_ornament,
            )
            widget.pack(fill="x", pady=3, padx=4)
            self.ornament_items.append(item)
            self.ornament_widgets[item_id] = widget
            existing.add(p)
            added += 1

        if added > 0 and self.lbl_empty_ornaments:
            self.lbl_empty_ornaments.pack_forget()

        self._update_ornaments_progress_ui(0, len(self.ornament_items))
        self.set_status(f"Added {added} ornament(s) to queue.")

    def _remove_ornament_item(self, item_id: str):
        widget = self.ornament_widgets.pop(item_id, None)
        if widget:
            widget.destroy()
        self.ornament_items = [it for it in self.ornament_items if it.item_id != item_id]
        if not self.ornament_items:
            self.lbl_empty_ornaments.pack(pady=50)
        self._update_ornaments_progress_ui(0, len(self.ornament_items))
        self.set_status("Ornament removed from queue.")

    def _clear_ornament_queue(self):
        if self.is_uploading_ornaments:
            messagebox.showwarning("Busy", "Cannot clear queue while upload is active.")
            return
        for w in self.ornament_widgets.values():
            w.destroy()
        self.ornament_widgets.clear()
        self.ornament_items.clear()
        self.lbl_empty_ornaments.pack(pady=50)
        self._update_ornaments_progress_ui(0, 0)
        self.set_status("Ornaments queue cleared.")

    def _upload_single_ornament(self, item: MediaItem):
        self._save_settings_from_ui()
        ip = self.config.kindle_ip
        if not ip:
            messagebox.showwarning("E-Reader IP Missing", "Please enter your E-Reader's IP address.")
            return

        target_dir = self.config.remote_ornaments_dir or "/mnt/us/koreader/settings/bookshelf/ornaments"
        self.set_status(f"Uploading ornament '{item.file_name}' to E-Reader...")
        widget = self.ornament_widgets.get(item.item_id)
        if widget:
            widget.update_status("Uploading...")

        def run_upload():
            client = KindleSFTPClient(
                host=ip,
                port=self.config.kindle_port,
                username=self.config.kindle_user,
                password=self.config.kindle_password,
                remote_dir=target_dir,
            )
            success, msg = client.upload_file(item.file_path, target_remote_dir=target_dir)

            def finish():
                if success:
                    item.status = "Uploaded"
                    if widget:
                        widget.update_status("Uploaded")
                    self.set_status(f"Uploaded: '{item.file_name}'")
                    messagebox.showinfo("Ornament Uploaded", f"Ornament sent to:\n{target_dir}/{item.file_name}")
                else:
                    item.status = "Failed"
                    if widget:
                        widget.update_status("Failed")
                    self.set_status(f"Upload failed: {msg}")
                    messagebox.showerror("Upload Failed", msg)

            self.after(0, finish)

        threading.Thread(target=run_upload, daemon=True).start()

    def _upload_all_ornaments(self):
        if not self.ornament_items:
            messagebox.showwarning("Empty Queue", "Please add ornament icons first.")
            return

        self._save_settings_from_ui()
        ip = self.config.kindle_ip
        if not ip:
            messagebox.showwarning("E-Reader IP Missing", "Please enter your E-Reader's IP address.")
            return

        target_dir = self.config.remote_ornaments_dir or "/mnt/us/koreader/settings/bookshelf/ornaments"
        self.is_uploading_ornaments = True
        self.btn_upload_all_ornaments.configure(state="disabled")
        self.set_status(f"Uploading {len(self.ornament_items)} ornaments to E-Reader...")

        def run_batch():
            client = KindleSFTPClient(
                host=ip,
                port=self.config.kindle_port,
                username=self.config.kindle_user,
                password=self.config.kindle_password,
                remote_dir=target_dir,
            )
            success_count = 0
            fail_count = 0

            for idx, it in enumerate(self.ornament_items):
                w = self.ornament_widgets.get(it.item_id)
                if w:
                    self.after(0, lambda w=w: w.update_status("Uploading..."))

                ok, msg = client.upload_file(it.file_path, target_remote_dir=target_dir)
                if ok:
                    success_count += 1
                    status_str = "Uploaded"
                else:
                    fail_count += 1
                    status_str = "Failed"

                it.status = status_str
                if w:
                    self.after(0, lambda w=w, s=status_str: w.update_status(s))
                self.after(0, lambda i=idx+1, t=len(self.ornament_items): self._update_ornaments_progress_ui(i, t))

            def finish():
                self.is_uploading_ornaments = False
                self.btn_upload_all_ornaments.configure(state="normal")
                self.set_status(f"Ornaments upload complete. Uploaded: {success_count}, Failed: {fail_count}")
                messagebox.showinfo("Transfer Finished", f"Uploaded {success_count} ornaments to:\n{target_dir}")

            self.after(0, finish)

        threading.Thread(target=run_batch, daemon=True).start()

    def _update_ornaments_progress_ui(self, done: int, total: int):
        if total == 0:
            self.bar_ornaments_prog.set(0)
            self.lbl_ornaments_prog.configure(text="Uploaded: 0 / 0 ornaments")
            return
        ratio = min(1.0, max(0.0, done / total))
        self.bar_ornaments_prog.set(ratio)
        self.lbl_ornaments_prog.configure(text=f"Uploaded: {done} / {total} ornaments ({int(ratio * 100)}%)")
