# app/main.py
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.scraper import export_existing_to_excel, RESULTS_DIR

import json, os

from app.scraper import scrape_latest_wp_to_files, RESULTS_DIR

from fastapi import Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func, desc
from app.db.session import get_db, SessionLocal
from app.db.models import Article, Asset, TextRow, AssetKind, TextKind

from pathlib import Path

from fastapi import HTTPException
from app.generation import generate_t2i_for_article

from typing import Optional

from app.generation import generate_next_images_below
from app.generation_text import generate_article_for_image, generate_next_articles_below





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
    if not p.exists():
        return ""
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
def compare_rows(request: Request, limit: int = 12):
    posts = summarize_existing_results()[:limit]
    for a in posts:
        a["original_text"] = _read_body_text(a["id"])
        # ⬇️ NEW: attach generated image (if any)
        a["generated_image_web"] = _latest_generated_image_web(a["id"])
        # leave generated_text None for now unless you add it later
        a["generated_text"] = None
        a["gen_text_model"] = None
        a["gen_img_model"] = None
        a["gen_status"] = "No generations yet." if not a["generated_image_web"] else "Generated image available"
    return templates.TemplateResponse(
        "partials/compare_list.html",
        {"request": request, "posts": posts}
    )

@app.post("/api/generate/t2i/first")
def generate_first_t2i():
    print("[API] /api/generate/t2i/first called", flush=True)
    posts = summarize_existing_results()
    if not posts:
        print("[API] no articles", flush=True)
        raise HTTPException(status_code=404, detail="No articles available.")

    first = posts[0]
    article_id = first["id"]
    title = first.get("title") or ""
    body = _read_body_text(article_id)
    if not body:
        print(f"[API] article {article_id} has empty body", flush=True)
        raise HTTPException(status_code=400, detail="First article has no body text.")

    result = generate_t2i_for_article(article_id, title, body)
    print(f"[API] OK -> {result.get('image_path')}", flush=True)
    return {"ok": True, **result}


def _latest_generated_image_web(article_id: str) -> str | None:
    folder = Path(RESULTS_DIR) / article_id / "_gen" / "text_to_image"
    if not folder.exists():
        return None
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    candidates = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts]
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    # Served by StaticFiles at /static (already mounted to crawled_results)
    return f"/static/{article_id}/_gen/text_to_image/{latest.name}"


from fastapi import Form, HTTPException
from typing import Optional

# ... keep your other imports (summarize_existing_results, _read_body_text, etc.)

@app.post("/api/generate/t2i/batch")
def generate_t2i_batch(
    n: int = Form(5),
    mode: str = Form("pending"),  # "pending" | "first"
    force: Optional[str] = Form(None),  # checkbox -> "on" if checked
):
    """
    Generate N images in one go.
    mode="pending": uses DB to pick next N without images (get_pending_articles_for_t2i)
    mode="first":   uses summarize_existing_results() order (like your current 'first')
    """
    force_bool = bool(force)  # "on" -> True, missing -> False
    generated = []

    if mode not in ("pending", "first"):
        raise HTTPException(status_code=400, detail="Invalid mode")

    if mode == "pending":
        # Pick next N from DB that need images
        from app.db.crud import get_pending_articles_for_t2i
        with SessionLocal() as db:
            items = get_pending_articles_for_t2i(db, limit=n)
        if not items:
            raise HTTPException(status_code=404, detail="No pending articles without images.")
        for article_id, title, url in items:
            body = _read_body_text(article_id) or ""
            try:
                res = generate_t2i_for_article(article_id, title or "", body, force=force_bool)
                generated.append({"id": article_id, "path": res.get("image_path")})
            except Exception as e:
                # continue but record the error
                generated.append({"id": article_id, "error": str(e)})

    else:  # mode == "first"
        posts = summarize_existing_results()
        if not posts:
            raise HTTPException(status_code=404, detail="No articles available.")
        # Take the first N posts from your current list
        for post in posts[: max(0, n)]:
            article_id = post["id"]
            title = post.get("title") or ""
            body = _read_body_text(article_id) or ""
            if not body:
                generated.append({"id": article_id, "error": "empty body"})
                continue
            try:
                res = generate_t2i_for_article(article_id, title, body, force=force_bool)
                generated.append({"id": article_id, "path": res.get("image_path")})
            except Exception as e:
                generated.append({"id": article_id, "error": str(e)})

    return {"ok": True, "count": len(generated), "generated": generated, "mode": mode, "force": force_bool}


@app.post("/api/generate/t2i/below_top")
def generate_t2i_below_top(
    n: int = Form(5),
    force: Optional[str] = Form(None),  # checkbox -> "on" if checked
):
    """
    Uses the current top article (first in summarize_existing_results()) as the start ID,
    then generates images for the next N IDs below it.
    """
    posts = summarize_existing_results()
    if not posts:
        raise HTTPException(status_code=404, detail="No articles available.")
    start_id = posts[0]["id"]
    ids = generate_next_images_below(start_id, count=max(0, n), force=bool(force))
    return {"ok": True, "mode": "below_top", "start": start_id, "count": len(ids), "ids": ids, "force": bool(force)}

@app.post("/api/generate/t2i/below")
def generate_t2i_below(
    start_id: str = Form(...),
    n: int = Form(5),
    force: Optional[str] = Form(None),
):
    """
    Same as above but lets the user provide a specific start ID (e.g., 11LM099).
    """
    if not start_id:
        raise HTTPException(status_code=400, detail="start_id is required")
    ids = generate_next_images_below(start_id, count=max(0, n), force=bool(force))
    return {"ok": True, "mode": "below", "start": start_id, "count": len(ids), "ids": ids, "force": bool(force)}


def _top_article_id() -> str:
    # If your "top" is simply the latest created, prefer created_at desc; else id desc.
    with SessionLocal() as db:
        row = db.query(Article).order_by(desc(Article.created_at)).first()
        if row:
            return row.id
        row2 = db.query(Article).order_by(desc(Article.id)).first()
        return row2.id if row2 else ""

@app.post("/api/generate/v2t/first", response_class=PlainTextResponse)
def api_v2t_first(
    model: str = Form("qwen-vl-plus"),
    language: str = Form("en"),
    force: bool = Form(False),
):
    start_id = _top_article_id()
    if not start_id:
        return PlainTextResponse("No articles", status_code=404)
    res = generate_article_for_image(start_id, model=model, language=language, force=force)
    return "OK" if res else "SKIP"

@app.post("/api/generate/v2t/below_top", response_class=PlainTextResponse)
def api_v2t_below_top(
    n: int = Form(5),
    model: str = Form("qwen-vl-plus"),
    language: str = Form("en"),
    force: bool = Form(False),
):
    start_id = _top_article_id()
    if not start_id:
        return PlainTextResponse("No articles", status_code=404)
    generate_next_articles_below(start_id, n, model=model, language=language, force=force)
    return "OK"

@app.post("/api/generate/v2t/below", response_class=PlainTextResponse)
def api_v2t_below(
    start_id: str = Form(...),
    n: int = Form(5),
    model: str = Form("qwen-vl-plus"),
    language: str = Form("en"),
    force: bool = Form(False),
):
    generate_next_articles_below(start_id, n, model=model, language=language, force=force)
    return "OK"