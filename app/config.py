"""
Modul de configurare pentru KindleBatchConverter.
Gestionează salvarea și încărcarea setărilor din config.json,
precum și detecția automată a executabilului Tesseract pe diferite sisteme de operare.
"""

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class AppConfig:
    # SSH / KOReader settings
    kindle_ip: str = "192.168.1.50"
    kindle_port: int = 2222
    kindle_user: str = "root"
    kindle_password: str = ""
    remote_dir: str = "/mnt/us/Carti/Biblioteca"
    remote_screensavers_dir: str = "/mnt/us/koreader/screensavers"
    remote_ornaments_dir: str = "/mnt/us/koreader/settings/bookshelf/ornaments"
    auto_send: bool = True

    # OCR & PDF settings
    tesseract_cmd: str = ""
    ocr_lang: str = "ron+eng"
    dpi: int = 300
    max_workers: int = field(default_factory=lambda: max(2, min(8, (os.cpu_count() or 4))))
    fetch_metadata: bool = True  # Automatically fetch Title, Author, Genre online
    target_format: str = "EPUB"  # "EPUB" or "PDF"

    # Output directory
    output_dir: str = ""
    last_browse_dir: str = ""

    # Appearance
    appearance_mode: str = "Dark"  # "Dark", "Light", "System"
    color_theme: str = "blue"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


def detect_tesseract_path() -> Optional[str]:
    """
    Detects the Tesseract OCR binary path:
    1. Checks for bundled Tesseract inside application package (zero setup for end users)
    2. Checks standard Windows, macOS, and Linux locations
    3. Checks system PATH
    """
    # 1. Bundled Tesseract in app directory
    app_base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    bundled_candidates = [
        app_base / "tesseract" / "tesseract.exe",
        app_base / "bin" / "tesseract" / "tesseract.exe",
        app_base / "tesseract.exe",
    ]
    for b in bundled_candidates:
        if b.is_file():
            # Configure TESSDATA_PREFIX to bundled tessdata
            tessdata_dir = b.parent / "tessdata"
            if not tessdata_dir.is_dir():
                tessdata_dir = app_base / "tessdata"
            if tessdata_dir.is_dir():
                os.environ["TESSDATA_PREFIX"] = str(tessdata_dir.resolve())
            return str(b.resolve())

    # 2. Standard Windows locations
    if sys.platform.startswith("win"):
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
            r"C:\Tesseract-OCR\tesseract.exe",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p

    # 3. Standard PATH
    which_path = shutil.which("tesseract")
    if which_path and os.path.isfile(which_path):
        return which_path

    # 4. macOS locations
    elif sys.platform == "darwin":
        candidates = [
            "/opt/homebrew/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/opt/local/bin/tesseract",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p

    # 5. Linux locations
    elif sys.platform.startswith("linux"):
        candidates = [
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p

    return None


CONFIG_FILE_NAME = "config.json"


def get_user_data_dir() -> Path:
    """
    Returnează folderul unde se salvează datele utilizatorului (config.json, sesiuni).
    Dacă aplicația se află într-un folder cu drepturi de scriere (folder portabil sau dev),
    folosim acel folder. Dacă e instalată în Program Files unde scrierea e restricționată,
    folosim %APPDATA%/K-Bridge.
    """
    if getattr(sys, "frozen", False):
        app_base = Path(sys.executable).parent
    else:
        app_base = Path(__file__).resolve().parent.parent

    # Testăm dacă avem drept de scriere în folderul aplicației
    test_file = app_base / ".perm_check.tmp"
    try:
        with open(test_file, "w") as f:
            f.write("1")
        test_file.unlink(missing_ok=True)
        return app_base
    except Exception:
        appdata = os.environ.get("APPDATA")
        if appdata:
            data_dir = Path(appdata) / "K-Bridge"
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir
        return app_base


def get_config_file_path() -> Path:
    """Returnează calea către fișierul config.json."""
    data_dir = get_user_data_dir()
    direct_path = data_dir / CONFIG_FILE_NAME
    if direct_path.is_file():
        return direct_path

    # Dacă nu există în data_dir, verificăm dacă există în app_base
    if getattr(sys, "frozen", False):
        app_base = Path(sys.executable).parent
    else:
        app_base = Path(__file__).resolve().parent.parent
    base_path = app_base / CONFIG_FILE_NAME
    if base_path.is_file():
        return base_path

    return direct_path


def load_config() -> AppConfig:
    """Încarcă configurația din config.json sau returnează setările implicite."""
    data_dir = get_user_data_dir()
    config_path = get_config_file_path()
    config = AppConfig()

    # Dacă Tesseract nu este setat explicit, încercăm autodetecția
    detected_tesseract = detect_tesseract_path()
    if detected_tesseract:
        config.tesseract_cmd = detected_tesseract

    # Setăm folderul de output implicit
    docs_dir = Path.home() / "Documents" / "K-Bridge Books"
    default_output = str(docs_dir.resolve()) if docs_dir.parent.is_dir() else str((data_dir / "output_epubs").resolve())
    config.output_dir = default_output

    if config_path.is_file():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = AppConfig.from_dict(data)
                # Dacă tesseract_cmd a fost salvat gol, încercăm să folosim ce am detectat
                if not config.tesseract_cmd and detected_tesseract:
                    config.tesseract_cmd = detected_tesseract
                if not config.output_dir:
                    config.output_dir = default_output
        except Exception as e:
            print(f"[Avertisment] Nu s-a putut încărca {CONFIG_FILE_NAME}: {e}")

    return config


def save_config(config: AppConfig) -> bool:
    """Salvează configurația în config.json."""
    data_dir = get_user_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / CONFIG_FILE_NAME
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[Eroare] Salvare eșuată pentru {CONFIG_FILE_NAME}: {e}")
        return False

