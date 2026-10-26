# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/macea/Desktop/PDF to epub/main.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/macea/AppData/Local/Programs/Python/Python311/Lib/site-packages/customtkinter', 'customtkinter'), ('C:/Users/macea/Desktop/PDF to epub/K-Bridge.ico', '.'), ('C:/Users/macea/Desktop/PDF to epub/K-Bridge_icon.png', '.')],
    hiddenimports=['customtkinter', 'pymupdf', 'fitz', 'pytesseract', 'PIL', 'ebooklib', 'ebooklib.epub', 'bs4', 'paramiko', 'cryptography'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pymupdf.layout', 'pymupdf_layout', 'onnxruntime'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='K-Bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Users/macea/Desktop/PDF to epub/K-Bridge.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='K-Bridge',
)
