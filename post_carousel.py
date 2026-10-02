"""
Auto-post carousel Instagram dari posts.csv.

Cara kerja:
1. Cari baris di posts.csv dengan tanggal = hari ini (zona waktu WIB) dan status = approved
2. Buat container untuk tiap gambar, lalu container carousel
3. Publish, lalu ubah status jadi "posted" dan isi link_hasil

Rahasia (token) TIDAK ditulis di sini, tapi dibaca dari environment variable:
  IG_USER_ID, IG_ACCESS_TOKEN
Opsional:
  GRAPH_HOST      (default: graph.instagram.com; pakai graph.facebook.com kalau login lewat Facebook)
  GRAPH_VERSION   (default: v25.0; cek versi terbaru di dokumentasi Meta)
  IMAGE_BASE_URL  (awalan URL kalau kolom gambar hanya berisi nama file)
  DRY_RUN=true    (hanya simulasi, tidak posting dan tidak mengubah CSV)
  POST_DATE       (YYYY-MM-DD, untuk mengetes tanggal tertentu)
"""

import csv
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

CSV_PATH = os.getenv("POSTS_CSV", "posts.csv")
GRAPH_HOST = os.getenv("GRAPH_HOST", "graph.instagram.com")
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v25.0")
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
BASE = f"https://{GRAPH_HOST}/{GRAPH_VERSION}"

IG_USER_ID = os.getenv("IG_USER_ID", "")
TOKEN = os.getenv("IG_ACCESS_TOKEN", "")


def api_post(path, params):
    params = {**params, "access_token": TOKEN}
    r = requests.post(f"{BASE}/{path}", data=params, timeout=60)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"API error di /{path}: {data.get('error', data)}")
    return data


def api_get(path, params):
    params = {**params, "access_token": TOKEN}
    r = requests.get(f"{BASE}/{path}", params=params, timeout=60)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"API error di /{path}: {data.get('error', data)}")
    return data


def full_url(item):
    item = item.strip()
    if item.startswith("http"):
        return item
    return IMAGE_BASE_URL.rstrip("/") + "/" + item.lstrip("/")


def wait_until_ready(container_id, timeout_s=120):
    start = time.time()
    while time.time() - start < timeout_s:
        status = api_get(container_id, {"fields": "status_code"}).get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Container {container_id} berstatus {status}")
        time.sleep(3)
    raise RuntimeError(f"Container {container_id} belum siap setelah {timeout_s} detik")


def post_carousel(image_urls, caption):
    children = []
    for url in image_urls:
        child = api_post(
            f"{IG_USER_ID}/media",
            {"image_url": url, "is_carousel_item": "true"},
        )
        children.append(child["id"])

    for child_id in children:
        wait_until_ready(child_id)

    parent = api_post(
        f"{IG_USER_ID}/media",
        {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": caption,
        },
    )
    wait_until_ready(parent["id"])

    published = api_post(f"{IG_USER_ID}/media_publish", {"creation_id": parent["id"]})
    return published["id"]


def get_permalink(media_id):
    try:
        return api_get(media_id, {"fields": "permalink"}).get("permalink", "")
    except Exception as e:  # tidak fatal, postingan sudah terbit
        print(f"Peringatan: gagal mengambil permalink: {e}")
        return ""


def auto_upload_nyala():
    """Saklar utama: baca pengaturan.json. Kalau file hilang/rusak, anggap MATI (lebih aman)."""
    try:
        with open("pengaturan.json", encoding="utf-8") as f:
            return bool(json.load(f).get("auto_upload", False))
    except Exception as e:
        print(f"Peringatan: pengaturan.json tidak terbaca ({e}). Dianggap MATI.")
        return False


def main():
    if not auto_upload_nyala():
        print("Saklar auto upload = MATI (pengaturan.json). Tidak ada yang diposting.")
        return

    if not DRY_RUN and (not IG_USER_ID or not TOKEN):
        sys.exit("IG_USER_ID dan IG_ACCESS_TOKEN belum diisi (cek GitHub Secrets).")

    today = os.getenv("POST_DATE") or datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d")
    print(f"Tanggal target: {today} (DRY_RUN={DRY_RUN})")

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "link_hasil" not in fieldnames:
        fieldnames.append("link_hasil")

    todo = [
        r for r in rows
        if r.get("tanggal", "").strip() == today
        and r.get("status", "").strip().lower() in ("posting", "approved")
    ]

    drafts = [r for r in rows if r.get("tanggal", "").strip() == today
              and r.get("status", "").strip().lower() == "draft"]
    if drafts:
        print(f"{len(drafts)} carousel hari ini berstatus 'draft' -> dilewati (tidak diposting).")

    if not todo:
        print("Tidak ada carousel berstatus 'posting' untuk hari ini. Selesai.")
        return

    failed = False
    for row in todo:
        images = [full_url(x) for x in row.get("gambar", "").split("|") if x.strip()]
        caption = row.get("caption", "")

        if not 2 <= len(images) <= 10:
            print(f"GAGAL: carousel harus berisi 2-10 gambar (ditemukan {len(images)}).")
            failed = True
            continue

        if DRY_RUN:
            print(f"[SIMULASI] Akan posting {len(images)} slide:")
            for u in images:
                print(f"  - {u}")
            print(f"  Caption: {caption[:80]}...")
            continue

        try:
            media_id = post_carousel(images, caption)
        except Exception as e:
            print(f"GAGAL posting: {e}")
            failed = True
            continue

        row["status"] = "posted"
        row["link_hasil"] = get_permalink(media_id)
        print(f"Berhasil terposting: {row['link_hasil'] or media_id}")

        # simpan segera supaya tidak dobel posting kalau ada error setelahnya
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
