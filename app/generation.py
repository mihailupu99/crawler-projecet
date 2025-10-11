from __future__ import annotations
import os
import json
import time
from pathlib import Path
from typing import Optional
import requests
import hashlib
import io
import re

from PIL import Image
from pathlib import Path
from typing import Iterable, List
from app.scraper import RESULTS_DIR
from app.db.session import SessionLocal
from app.db.crud import (
    create_or_get_asset,
    mark_article_image_generated,
    get_pending_articles_for_t2i,
)
from app.db.models import AssetKind, Article, TextRow, TextKind

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
# Singapore/international synchronous endpoint for Qwen-Image
T2I_SYNC_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
MODEL_NAME = "qwen-image-plus"  # or "qwen-image"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _build_prompt(title: str, body: str, max_chars: int = 750) -> str:
    """
    Build a concise visual prompt from article text.
    Keep under the ~800-char limit; include title + first 1–2 sentences.
    """
    title = (title or "").strip()
    body = (body or "").strip().replace("\n", " ")
    piece = (body[: max_chars - len(title) - 20]).strip()
    return f"{title}. Photorealistic editorial illustration about: {piece}"


def _download(url: str, dest: Path) -> bytes:
    _log(f"[T2I] ⇣ downloading image…")
    t0 = time.monotonic()
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    dt_ms = int((time.monotonic() - t0) * 1000)
    _log(f"[T2I] ✓ saved image: {dest} ({len(r.content)} bytes) in {dt_ms} ms")
    return r.content


def _img_meta(img_bytes: bytes) -> tuple[str, int, int]:
    sha = hashlib.sha256(img_bytes).hexdigest()
    with Image.open(io.BytesIO(img_bytes)) as im:
        w, h = im.size
    return sha, w, h


def generate_t2i_for_article(
    article_id: str,
    title: str,
    body: str,
    size: str = "1664*928",
    *,
    force: bool = False,
    store_blob_in_db: bool = True,
) -> dict:
    """
    Generate an image from text for a given article, save under:
      crawled_results/<ID>/_gen/text_to_image/<model>@<ts>.png
    And register it in DB as assets.kind=generated_image.
    Also sets the article's has_image_generated flag.
    Returns a short dict with paths/ids/metadata.
    """
    if not DASHSCOPE_API_KEY:
        _log("[T2I] ❌ DASHSCOPE_API_KEY is not set")
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    prompt = _build_prompt(title, body)
    _log(
        f"[T2I] ▶ start | article={article_id} | model={MODEL_NAME} | size={size}"
    )
    _log(
        f"[T2I] prompt ({len(prompt)} chars): {prompt[:200]}{'…' if len(prompt) > 200 else ''}"
    )

    # Optional fast-path: skip if already generated
    if not force:
        with SessionLocal() as db:
            art = db.get(Article, article_id)
            if art and art.has_image_generated:
                _log(f"[T2I] ⏭ already has image; skipping article={article_id}")
                return {"article_id": article_id, "skipped": True}

    # Build request per Qwen-Image sync API
    payload = {
        "model": MODEL_NAME,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": {
            "size": size,  # 16:9 to match your cards
            "prompt_extend": True,  # let model rewrite short prompts
            "watermark": False,
        },
    }
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }

    _log("[T2I] ⇢ sending request to Alibaba (DashScope)…")
    t0 = time.monotonic()
    resp = requests.post(T2I_SYNC_URL, headers=headers, json=payload, timeout=120)
    dt_ms = int((time.monotonic() - t0) * 1000)
    _log(f"[T2I] ⇠ response HTTP {resp.status_code} in {dt_ms} ms")

    try:
        resp.raise_for_status()
    except requests.HTTPError:
        snippet = (resp.text or "")[:500]
        _log(f"[T2I] ❌ HTTP error body: {snippet}")
        raise

    data = resp.json()

    # Per docs: image URL lives at output.choices[0].message.content[0].image
    # URL lives ~24h -> download immediately.
    try:
        image_url = data["output"]["choices"][0]["message"]["content"][0]["image"]
        _log(
            f"[T2I] ✓ got image URL (temporary): {image_url[:120]}{'…' if len(image_url) > 120 else ''}"
        )
    except Exception:
        _log(f"[T2I] ❌ unexpected response shape: {json.dumps(data)[:600]}")
        raise RuntimeError("Unexpected response: model output structure changed")

    # Persist to disk
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = Path(RESULTS_DIR) / article_id / "_gen" / "text_to_image"
    out_path = out_dir / f"{MODEL_NAME}@{ts}.png"
    img_bytes = _download(image_url, out_path)
    sha256, width, height = _img_meta(img_bytes)

    # Register in DB (asset + article flags) atomically
    _log(f"[T2I] ⇢ updating DB (asset + checkmark)…")
    with SessionLocal() as db, db.begin():
        create_or_get_asset(
            db,
            article_id=article_id,
            kind=AssetKind.generated_image,
            path=str(out_path.as_posix()),
            mime="image/png",
            sha256=sha256,
            width=width,
            height=height,
            data=(img_bytes if store_blob_in_db else None),
            size_bytes=len(img_bytes),
        )
        mark_article_image_generated(db, article_id)
    _log(
        f"[T2I] ✓ DB updated for article={article_id} (sha256={sha256[:12]}… {width}x{height})"
    )

    # Save call metadata for reproducibility
    meta_path = out_dir / f"{MODEL_NAME}@{ts}.json"
    meta = {
        "request": payload,
        "response": data,
        "image_path": str(out_path),
        "sha256": sha256,
        "width": width,
        "height": height,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"[T2I] ✓ metadata saved: {meta_path}")

    _log(f"[T2I] ▶ done | article={article_id}")
    return {
        "article_id": article_id,
        "image_path": str(out_path),
        "prompt": prompt,
        "sha256": sha256,
        "width": width,
        "height": height,
    }


def generate_missing_images(limit: int = 20, dry_run: bool = False):
    """
    Process next N articles that don't have images yet.
    """
    with SessionLocal() as db:
        items = get_pending_articles_for_t2i(db, limit=limit)

    ids = [aid for (aid, _title, _url) in items]

    for article_id, title, url in items:
        try:
            generate_t2i_for_article(article_id, title or "", "", force=False)
        except Exception as e:
            _log(f"[T2I] ⚠ failed for {article_id}: {e}")

    if dry_run:
        return ids
    
def _parse_article_id(article_id: str) -> tuple[str, int, int]:
    """
    '11LM099' -> ('11LM', 99, 3)  (prefix, number, zero-padding width)
    """
    m = re.match(r"^(.*?)(\d+)$", article_id)
    if not m:
        return ("", -1, 0)
    prefix, num_str = m.group(1), m.group(2)
    return prefix, int(num_str), len(num_str)

def _ids_below(start_id: str, count: int) -> List[str]:
    prefix, num, width = _parse_article_id(start_id)
    if num < 0:
        return []
    return [f"{prefix}{str(num - k).zfill(width)}" for k in range(1, count + 1) if num - k >= 0]

def _load_article_body(db, article_id: str) -> str:
    """
    Try to load full body from TextRow(kind=original_text) if present on disk,
    otherwise fall back to preview; otherwise empty.
    """
    row = (
        db.query(TextRow)
        .filter(TextRow.article_id == article_id, TextRow.kind == TextKind.original_text)
        .order_by(TextRow.created_at.desc())
        .first()
    )
    if not row:
        return ""
    try:
        p = Path(row.path)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    return row.preview or ""

def generate_next_images_below(start_article_id: str, count: int = 5, *, force: bool = False, dry_run: bool = False) -> list[str]:
    """
    Example: start_article_id='11LM099', count=5 -> attempts 11LM098, 097, 096, 095, 094.
    Skips ones that already have images unless force=True.
    Returns the list of IDs it attempted (after filtering not-found/already-done when force=False).
    """
    targets = _ids_below(start_article_id, count)
    if not targets:
        _log(f"[T2I] No numeric-suffix IDs found below {start_article_id}")
        return []

    to_process: list[str] = []
    with SessionLocal() as db:
        for aid in targets:
            art = db.get(Article, aid)
            if not art:
                _log(f"[T2I] (skip) article not found: {aid}")
                continue
            if art.has_image_generated and not force:
                _log(f"[T2I] (skip) already has image: {aid}")
                continue
            to_process.append(aid)

    if not to_process:
        _log("[T2I] Nothing to generate in the selected window.")
        return []

    _log(f"[T2I] Will process ({len(to_process)}): {', '.join(to_process)}")
    if dry_run:
        return to_process

    for aid in to_process:
        with SessionLocal() as db:
            art = db.get(Article, aid)
            title = art.title or aid
            body = _load_article_body(db, aid)
        try:
            generate_t2i_for_article(aid, title, body, force=force)
        except Exception as e:
            _log(f"[T2I] ⚠ failed for {aid}: {e}")

    return to_process