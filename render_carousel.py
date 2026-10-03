"""
Perender: membuat 5 gambar JPEG (1080x1350) dari "resep" carousel, di server GitHub.

Resep ada di folder sumber/ dengan nama <tanggal>.html (atau <tanggal>.gz.b64 = HTML yang dikompres lalu di-base64).
Logo ada di aset/logo.b64.txt (PNG yang di-base64), dipulihkan jadi aset/logo.png saat jalan.

Hanya merender carousel yang:
  - tanggalnya = hari ini (WIB) atau POST_DATE,
  - statusnya 'posting' (atau 'approved'),
  - gambarnya belum ada di folder images/<tanggal>/.
Hasil: images/<tanggal>/slide1.jpg ... slide5.jpg

Variabel opsional: POST_DATE=YYYY-MM-DD, SLIDES=5
"""

import base64
import csv
import gzip
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent
SLIDES = int(os.getenv("SLIDES", "5") or "5")
W, H = 1080, 1350


def log(msg):
    print(msg, flush=True)


def auto_upload_nyala():
    try:
        with open(ROOT / "pengaturan.json", encoding="utf-8") as f:
            return bool(json.load(f).get("auto_upload", False))
    except Exception:
        return False


def siapkan_logo():
    png = ROOT / "aset" / "logo.png"
    b64 = ROOT / "aset" / "logo.b64.txt"
    if png.exists():
        return
    if not b64.exists():
        raise RuntimeError("aset/logo.b64.txt tidak ada (logo belum diunggah).")
    data = base64.b64decode("".join(b64.read_text().split()))
    png.write_bytes(data)
    log(f"Logo dipulihkan: {png} ({len(data) // 1024} KB)")


def siapkan_foto():
    """Foto latar milik sendiri: aset/foto/<nama>.b64.txt dipulihkan jadi aset/foto/<nama>.jpg."""
    folder = ROOT / "aset" / "foto"
    if not folder.exists():
        return
    for b64 in folder.glob("*.b64.txt"):
        jpg = folder / (b64.name[: -len(".b64.txt")] + ".jpg")
        if not jpg.exists():
            jpg.write_bytes(base64.b64decode("".join(b64.read_text().split())))
            log(f"Foto dipulihkan: {jpg.name}")


def cari_chrome():
    for nama in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(nama)
        if p:
            return p
    raise RuntimeError("Chrome tidak ditemukan di server.")


def ambil_html(tanggal):
    """Kembalikan path file HTML resep yang siap dibuka (di dalam folder sumber/ agar jalur ../aset/ benar)."""
    sumber = ROOT / "sumber"
    plain = sumber / f"{tanggal}.html"
    if plain.exists():
        return plain
    gz = sumber / f"{tanggal}.gz.b64"
    if gz.exists():
        isi = gzip.decompress(base64.b64decode("".join(gz.read_text().split())))
        tmp = sumber / f"_tmp_{tanggal}.html"
        tmp.write_bytes(isi)
        return tmp
    raise RuntimeError(f"Resep untuk {tanggal} tidak ada di sumber/")


def render(tanggal):
    siapkan_logo()
    siapkan_foto()
    chrome = cari_chrome()
    html = ambil_html(tanggal)
    out = ROOT / "images" / tanggal
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        for i in range(1, SLIDES + 1):
            png = pathlib.Path(td) / f"s{i}.png"
            cmd = [
                chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                f"--window-size={W},{H}", "--virtual-time-budget=30000",
                f"--screenshot={png}", f"file://{html}?only={i}",
            ]
            subprocess.run(cmd, check=False, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not png.exists():
                raise RuntimeError(f"Slide {i}: Chrome tidak menghasilkan gambar.")
            img = Image.open(png).convert("RGB")
            if img.size != (W, H):
                log(f"  Peringatan slide {i}: ukuran {img.size}, disesuaikan ke {W}x{H}")
                canvas = Image.new("RGB", (W, H), (12, 13, 16))
                canvas.paste(img.crop((0, 0, min(img.width, W), min(img.height, H))), (0, 0))
                img = canvas
            tujuan = out / f"slide{i}.jpg"
            img.save(tujuan, "JPEG", quality=93)
            ukuran = tujuan.stat().st_size // 1024
            log(f"  slide{i}.jpg  {ukuran} KB")
            if ukuran < 40:
                raise RuntimeError(f"Slide {i} terlalu kecil ({ukuran} KB), kemungkinan gagal memuat bahan dari internet.")
    if html.name.startswith("_tmp_"):
        html.unlink(missing_ok=True)


def main():
    if not auto_upload_nyala():
        log("Saklar auto upload = MATI. Tidak ada yang dirender.")
        return

    today = os.getenv("POST_DATE") or datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d")
    log(f"Perender - tanggal target: {today}")

    with open(ROOT / "posts.csv", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    target = [
        r for r in rows
        if r.get("tanggal", "").strip() == today
        and r.get("status", "").strip().lower() in ("posting", "approved")
    ]
    if not target:
        log("Tidak ada carousel berstatus 'posting' untuk tanggal ini. Selesai.")
        return

    gagal = False
    for r in target:
        out = ROOT / "images" / today
        sudah = all((out / f"slide{i}.jpg").exists() for i in range(1, SLIDES + 1))
        if sudah:
            log(f"Gambar {today} sudah ada, tidak dirender ulang.")
            continue
        log(f"Merender carousel {today} ...")
        try:
            render(today)
            log("Render selesai.")
        except Exception as e:
            log(f"GAGAL render: {e}")
            gagal = True
    if gagal:
        sys.exit(1)


if __name__ == "__main__":
    main()
