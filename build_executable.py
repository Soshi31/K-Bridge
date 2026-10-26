"""
Script pentru compilarea aplicatiei K-Bridge intr-un executabil Windows (.exe)
cu iconita personalizata folosind PyInstaller.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import customtkinter


def build_exe():
    print("=== Re-compilare K-Bridge.exe cu pictograma noua ===")

    ctk_path = Path(customtkinter.__file__).parent
    print(f"Cale CustomTkinter: {ctk_path}")

    base_dir = Path(__file__).parent.resolve()
    main_file = base_dir / "main.py"
    icon_file = base_dir / "K-Bridge.ico"
    png_icon = base_dir / "K-Bridge_icon.png"

    add_data_ctk = f"{ctk_path}{os.pathsep}customtkinter"
    add_data_ico = f"{icon_file}{os.pathsep}."
    add_data_png = f"{png_icon}{os.pathsep}."

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name",
        "K-Bridge",
        "--icon",
        str(icon_file),
        "--add-data",
        add_data_ctk,
        "--add-data",
        add_data_ico,
        "--add-data",
        add_data_png,
        "--exclude-module",
        "pymupdf.layout",
        "--exclude-module",
        "pymupdf_layout",
        "--exclude-module",
        "onnxruntime",
        "--hidden-import",
        "customtkinter",
        "--hidden-import",
        "pymupdf",
        "--hidden-import",
        "fitz",
        "--hidden-import",
        "pytesseract",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "ebooklib",
        "--hidden-import",
        "ebooklib.epub",
        "--hidden-import",
        "bs4",
        "--hidden-import",
        "paramiko",
        "--hidden-import",
        "cryptography",
        str(main_file),
    ]

    print("Rulare PyInstaller...")
    res = subprocess.run(cmd, cwd=str(base_dir))
    if res.returncode == 0:
        dist_dir = base_dir / "dist" / "K-Bridge"
        exe_file = dist_dir / "K-Bridge.exe"

        # Copiere config.json in folderul dist
        src_cfg = base_dir / "config.json"
        if src_cfg.is_file():
            shutil.copy2(src_cfg, dist_dir / "config.json")
            print("config.json a fost copiat in folderul de distributie.")

        # Copiere tessdata in folderul dist
        src_tess = base_dir / "tessdata"
        if src_tess.is_dir():
            shutil.copytree(src_tess, dist_dir / "tessdata", dirs_exist_ok=True)
            print("tessdata a fost copiat in folderul de distributie.")

        # Copiere tesseract binar integrat in folderul dist
        src_tesseract = base_dir / "tesseract"
        if src_tesseract.is_dir():
            shutil.copytree(src_tesseract, dist_dir / "tesseract", dirs_exist_ok=True)
            print("tesseract runtime complet a fost inclus in folderul de distributie.")

        # Copiere iconita aplicatie
        if icon_file.is_file():
            shutil.copy2(icon_file, dist_dir / "K-Bridge.ico")

        print(f"\n[SUCCES] Executabilul a fost creat cu succes in:\n{exe_file}")

        # Creare lansator .bat sigur (ocoleste blocajul Smart App Control prin pythonw semnat)
        pythonw_path = sys.executable.replace("python.exe", "pythonw.exe")
        root_bat = base_dir / "Lanseaza_K-Bridge.bat"
        with open(root_bat, "w", encoding="utf-8") as f:
            f.write(f'@echo off\ntitle K-Bridge\ncd /d "%~dp0"\nstart "" "{pythonw_path}" main.py\n')
        print(f"A fost creat lansatorul rapid: {root_bat.name}")

        # Actualizare si vechiul Lanseaza_KindleBatchConverter.bat ca alias
        legacy_bat = base_dir / "Lanseaza_KindleBatchConverter.bat"
        with open(legacy_bat, "w", encoding="utf-8") as f:
            f.write(f'@echo off\ntitle K-Bridge\ncd /d "%~dp0"\nstart "" "{pythonw_path}" main.py\n')

        # Compilare Installer cu Inno Setup daca este disponibil
        iscc_candidates = [
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")),
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
        ]
        iscc_exe = None
        for cand in iscc_candidates:
            if cand.is_file():
                iscc_exe = cand
                break

        iss_file = base_dir / "installer.iss"
        if iscc_exe and iss_file.is_file():
            print("\n=== Compilare Windows Setup Installer cu Inno Setup ===")
            print(f"ISCC: {iscc_exe}")
            res_iss = subprocess.run([str(iscc_exe), str(iss_file)], cwd=str(base_dir))
            if res_iss.returncode == 0:
                setup_exe = base_dir / "dist_installer" / "K-Bridge_Setup_v1.0.exe"
                print(f"\n[INSTALLER GATA] Setup-ul comercial a fost creat cu succes in:\n{setup_exe}")
                desktop_setup = Path.home() / "Desktop" / "K-Bridge_Setup_v1.0.exe"
                shutil.copy2(setup_exe, desktop_setup)
                print(f"Copiat pe Desktop: {desktop_setup}")
            else:
                print(f"[Avertisment] Compilarea installer-ului a esuat cu codul: {res_iss.returncode}")
        else:
            print("[Info] Inno Setup nu a fost gasit sau installer.iss lipseste.")

    else:
        print(f"[EROARE] Compilarea a esuat cu codul: {res.returncode}")


if __name__ == "__main__":
    build_exe()
