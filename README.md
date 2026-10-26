# K-Bridge

**K-Bridge** is an all-in-one desktop management suite for E-Readers (Kindle, Kobo, etc.) running **KOReader**. It allows users to convert complex or scanned PDF documents into reflowable **EPUB** books, transfer **PDFs with injected metadata**, and wirelessly send **Screensavers / Wallpapers** and **Bookshelf Ornaments** via Wi-Fi SSH/SFTP.

![K-Bridge Icon](K-Bridge_icon.png)

---

## 🌟 Key Features

### 1. 📚 Books & Document Management
* **Dual Format Mode**:
  * **EPUB Conversion**: Pure optical OCR pipeline (via `PyMuPDF` + `Tesseract`) that bypasses broken or corrupted PDF fonts, automatically reconstructs fluid reflowable paragraphs, fixes hyphenations, cleans recurring running headers/footers, and generates full Table of Contents.
  * **Direct PDF Transfer with Metadata**: Injects clean Title, Author, Genre (Subject), and Series directly into PDF properties via `PyMuPDF` without modifying the original source file.
* **Intelligent Metadata & Genre Classifier**: Automatically extracts clean Author and Title from noisy filenames and classifies literature into accurate genres (Crime / Thriller, Romance, Sci-Fi, Fantasy, etc.).
* **One-Click Wi-Fi Transfer**: Send individual books or entire queues straight into your KOReader library (`/mnt/us/Carti/Biblioteca`).

### 2. 🖼️ Screensavers & Wallpapers
* Batch upload wallpapers and screensaver images (`PNG`, `JPG`, `WEBP`, `BMP`) directly to your E-Reader (`/mnt/us/koreader/screensavers`).
* Instant folder and file queue management.

### 3. 🎨 Bookshelf Ornaments
* Manage and wirelessly transfer custom icons and bookshelf ornaments directly into KOReader's Bookshelf extension folder (`/mnt/us/koreader/settings/bookshelf/ornaments`).

### 4. ⚡ Seamless User Experience
* Modern, dark-themed **CustomTkinter** graphical user interface.
* Bundled zero-configuration OCR engine.
* Automatic IP and destination directory persistence.
* Windows Installer with desktop shortcut creation via Inno Setup.

---

## 🛠️ Installation & Setup

### Prerequisites
* **Python 3.10+** (Python 3.11 recommended)
* **Tesseract OCR** (Pre-configured when built with bundled assets)

### Installing Dependencies
```bash
git clone https://github.com/Soshi31/K-Bridge.git
cd K-Bridge
pip install -r requirements.txt
```

### Running the Application
```bash
python main.py
```
Or launch via the convenience batch file:
```cmd
Lanseaza_K-Bridge.bat
```

---

## 📦 Building the Standalone Executable & Installer

To compile the application into a standalone Windows `.exe` and build the Inno Setup commercial installer:

```bash
python build_executable.py
```

* Executable output: `dist/K-Bridge/K-Bridge.exe`
* Installer output: `dist_installer/K-Bridge_Setup_v1.0.exe`

---

## 📱 Connecting to KOReader

1. On your E-Reader, launch **KOReader**.
2. Open the top menu $\rightarrow$ **Tools** (Gear icon) $\rightarrow$ **SSH Server** $\rightarrow$ **Start SSH Server**.
3. Note the IP address displayed on your E-Reader screen (e.g. `192.168.1.50`).
4. Enter this IP address in the **E-Reader IP** field in K-Bridge.
5. You're ready to transfer books, wallpapers, and ornaments wirelessly!

---

## 📄 License
This project is licensed under the MIT License.
