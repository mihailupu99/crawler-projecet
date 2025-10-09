# app/main.py
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.scraper import export_existing_to_excel, RESULTS_DIR

import json, os

from app.scraper import scrape_latest_wp_to_files, RESULTS_DIR

from fastapi import Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from app.db.session import get_db
from app.db.models import Article, Asset, TextRow, AssetKind, TextKind

from pathlib import Path

from fastapi import HTTPException



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

def _read_body_text(article_id: str) -> str:
    p = Path(RESULTS_DIR) / article_id / f"{article_id}.txt"
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _find_article_summary(article_id: str):
    posts = summarize_existing_results()
    for p in posts:
        if p.get("id") == article_id:
            return p
    return None

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

@app.get("/api/articles")
def list_articles(db: Session = Depends(get_db)):
    # Fetch articles
    articles = db.execute(
        select(Article).order_by(Article.created_at.desc())
    ).scalars().all()

    results = []
    for a in articles:
        img_count = db.execute(
            select(func.count(Asset.id)).where(
                Asset.article_id == a.id,
                Asset.kind == AssetKind.original_image
            )
        ).scalar_one()

        txt_count = db.execute(
            select(func.count(TextRow.id)).where(
                TextRow.article_id == a.id,
                TextRow.kind == TextKind.original_text
            )
        ).scalar_one()

        results.append({
            "id": a.id,
            "title": a.title,
            "url": a.url,
            "paragraphs": a.paragraphs_count,
            "published_at": a.published_at,  
            "images": img_count,
            "texts": txt_count,
        })
    return results

# Full compare panel (original vs generated)
@app.get("/partials/article_compare", response_class=HTMLResponse)
def article_compare(request: Request, article_id: str = Query(...)):
    meta = _find_article_summary(article_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Article not found")

    original_text = _read_body_text(article_id)
    # placeholders for future generated artifacts
    generated_text = None
    generated_image_web = None
    gen_text_model = None
    gen_img_model = None
    gen_status = "No generations yet"

    ctx = {
        "request": request,
        "article": {
            **meta,
            "original_text": original_text,
            "generated_text": generated_text,
            "generated_image_web": generated_image_web,
            "gen_text_model": gen_text_model,
            "gen_img_model": gen_img_model,
            "gen_status": gen_status,
        },
    }
    return templates.TemplateResponse("partials/article_compare.html", ctx)

# Smaller panel that just shows the right-hand (Generated) side; useful for polling later
@app.get("/partials/generated_panel", response_class=HTMLResponse)
def generated_panel(request: Request, article_id: str = Query(...)):
    meta = _find_article_summary(article_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Article not found")
    # same placeholders for now
    ctx = {
        "request": request,
        "article": {
            **meta,
            "generated_text": None,
            "generated_image_web": None,
            "gen_text_model": None,
            "gen_img_model": None,
            "gen_status": "No generations yet",
        },
    }
    return templates.TemplateResponse("partials/generated_panel.html", ctx)

@app.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request):
    # Shell page; list loads via HTMX
    return templates.TemplateResponse("compare.html", {"request": request, "title": "Compare"})

@app.get("/partials/compare_rows", response_class=HTMLResponse)
def compare_rows(request: Request, limit: int = Query(12, ge=1, le=200)):
    posts = summarize_existing_results()[:limit]
    # enrich with original text + placeholders for generated
    for a in posts:
        a["original_text"] = _read_body_text(a["id"])
        a["generated_text"] = None
        a["generated_image_web"] = None
        a["gen_text_model"] = None
        a["gen_img_model"] = None
        a["gen_status"] = "No generations yet."
    return templates.TemplateResponse(
        "partials/compare_list.html",
        {"request": request, "posts": posts}
    )