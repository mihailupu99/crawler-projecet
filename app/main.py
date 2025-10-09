# app/main.py
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.scraper import export_existing_to_excel, RESULTS_DIR

import json, os

from app.scraper import scrape_latest_wp_to_files, RESULTS_DIR

SITE_BASE = "https://newsmaker.md/ro"

# ⬇️ richer summary for templates (includes local image path + metadata)
def summarize_existing_results(results_dir: str = "crawled_results"):
    out = []
    if not os.path.isdir(results_dir):
        return out

    IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}

    for name in sorted(os.listdir(results_dir), reverse=True):
        folder = os.path.join(results_dir, name)
        if not os.path.isdir(folder):
            continue

        meta_path = os.path.join(folder, f"{name}.json")
        if not os.path.isfile(meta_path):
            continue

        # find the downloaded image file (WP<ID>.<ext>)
        local_image_web = None
        try:
            for fname in os.listdir(folder):
                root, ext = os.path.splitext(fname)
                if root == name and ext.lower() in IMG_EXTS:
                    # served by StaticFiles at /static
                    local_image_web = f"/static/{name}/{fname}"
                    break
        except Exception:
            pass

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            m = {}

        out.append({
            "id": name,
            "title": m.get("title", ""),
            "date": m.get("date", ""),
            "url": m.get("url", ""),
            "image_url": m.get("image_url"),
            "local_image_web": local_image_web,     # prefer this in UI
            "paragraphs_count": m.get("paragraphs_count", 0),
        })
    return out

app = FastAPI()
templates = Jinja2Templates(directory="app/web/templates")
app.mount("/static", StaticFiles(directory="crawled_results"), name="static")

# Home renders the shell; posts list is loaded via HTMX
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

# Fragment to render posts list (used by HTMX)
@app.get("/partials/posts", response_class=HTMLResponse)
def posts_partial(request: Request):
    posts = summarize_existing_results()
    return templates.TemplateResponse("partials/posts_fragment.html", {"request": request, "posts": posts})

# HTMX scrape endpoint: scrape, then return updated posts list fragment
@app.post("/scrape-fragment", response_class=HTMLResponse)
def scrape_fragment(request: Request, limit: int = 100):
    inserted = scrape_latest_wp_to_files(SITE_BASE, limit=limit)
    posts = summarize_existing_results()
    return templates.TemplateResponse(
        "partials/posts_fragment.html",
        {"request": request, "posts": posts}
    )


@app.get("/export.xlsx")
def export_xlsx():
    # Build fresh Excel from whatever is already crawled
    path = export_existing_to_excel()
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="posts.xlsx"
    )
