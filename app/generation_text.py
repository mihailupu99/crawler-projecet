# generation_text.py
from __future__ import annotations
import os, io, re, json, time, base64, math, hashlib
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from PIL import Image
from sqlalchemy import and_

from app.scraper import RESULTS_DIR
from app.db.session import SessionLocal
from app.db.models import Article, TextRow, TextKind, Asset, AssetKind  # your models/enums
from app.db.crud import create_or_get_text, mark_article_text_generated  # <-- use your CRUD

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
# OpenAI-compatible Vision endpoint for Qwen
OAI_COMPAT_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_VL_MODEL = os.getenv("QWEN_VL_MODEL", "qwen-vl-plus")

def _log(msg: str) -> None:
    print(msg, flush=True)

# -------- ID helpers --------

def _parse_article_id(article_id: str) -> Tuple[str, int, int]:
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

# -------- image fetch (DB first, then disk) --------

def _get_image_from_db(db, article_id: str) -> Optional[tuple[bytes, str, Optional[str]]]:
    """
    Returns (bytes, mime, path_if_any) for the newest non-generated image.
    If blob missing, falls back to the asset.path file if it exists.
    """
    try:
        q = (
            db.query(Asset)
            .filter(
                Asset.article_id == article_id,
                Asset.mime.like("image/%"),
                # Prefer non-generated sources; tweak if your enums differ
                Asset.kind != AssetKind.generated_image,
            )
            .order_by(Asset.created_at.desc())
        )
        for a in q.limit(10):
            if getattr(a, "data", None):
                return (bytes(a.data), a.mime or "image/jpeg", getattr(a, "path", None))
            p = Path(getattr(a, "path", "") or "")
            if p.exists():
                return (p.read_bytes(), a.mime or "image/jpeg", str(p))
    except Exception as e:
        _log(f"[V2T] (note) DB image fetch skipped: {e}")
    return None

def _guess_first_image_on_disk(article_id: str) -> Optional[Path]:
    root = Path(RESULTS_DIR) / article_id
    if not root.exists():
        return None
    exts = ("*.jpg", "*.jpeg", "*.png", "*.webp")
    # prefer ./images/*
    for base in [root / "images", root]:
        if base.exists():
            for pat in exts:
                hits = sorted(base.glob(pat))
                if hits:
                    return hits[0]
    # recursive last resort
    for pat in exts:
        hits = sorted(root.rglob(pat))
        if hits:
            return hits[0]
    return None

def _get_image_anywhere(db, article_id: str) -> Optional[tuple[bytes, str, Optional[str]]]:
    got = _get_image_from_db(db, article_id)
    if got:
        return got
    p = _guess_first_image_on_disk(article_id)
    if not p:
        return None
    return (p.read_bytes(), "image/jpeg", str(p))

# -------- model call --------

def _encode_jpeg_base64(img_bytes: bytes, max_side: int = 1600) -> str:
    with Image.open(io.BytesIO(img_bytes)) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_side / max(w, h)) if max(w, h) > max_side else 1.0
        if scale < 1.0:
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")

def _article_prompt(language: str = "en") -> str:
    if language.lower().startswith("en"):
        return (
            "You are a journalist crafting a neutral, factual news article from a single photo.\n"
            "Write:\n"
            "1) A concise H1 headline.\n"
            "2) A 400–600 word article describing the visible scene (who/what/where/when). "
            "Avoid speculation beyond what is visually plausible; do not invent names, dates, or stats. "
            "If some facts are unclear, acknowledge uncertainty explicitly.\n"
            "3) Finish with one line starting with 'ALT:' that gives succinct alt-text."
        )
    return "Write a 400–600 word article for this image, then add a final 'ALT:' line."

def _call_qwen_vl_article(image_b64_jpeg: str, prompt: str, model: str, timeout: int = 120) -> str:
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": "Be accurate; avoid hallucinations."}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64_jpeg}" }},
                ],
            },
        ],
    }
    t0 = time.monotonic()
    resp = requests.post(OAI_COMPAT_URL, headers=headers, json=payload, timeout=timeout)
    _log(f"[V2T] ⇠ {model} HTTP {resp.status_code} in {int((time.monotonic()-t0)*1000)} ms")
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()

# -------- main ops --------

def _approx_tokens(s: str) -> int:
    # coarse tiktoken-ish estimate; good enough for previews/metrics
    return max(1, math.ceil(len(s) / 4))

def generate_article_for_image(
    article_id: str,
    *,
    model: str = DEFAULT_VL_MODEL,
    language: str = "en",
    force: bool = False,
) -> Optional[dict]:
    """
    Build a 400–600 word article from the first available image for this article.
    Saves to RESULTS_DIR/<ID>/_gen/image_to_article/<model>@<ts>.md and writes TextRow(kind=vision_article).
    Also sets Article.has_text_generated + timestamps via mark_article_text_generated.
    """
    with SessionLocal() as db:
        # Respect existing text unless force=True
        try:
            kind = getattr(TextKind, "vision_article", None) or getattr(TextKind, "summary", None)
            if not force and kind:
                exists = (
                    db.query(TextRow)
                    .filter(and_(TextRow.article_id == article_id, TextRow.kind == kind))
                    .first()
                )
                if exists:
                    _log(f"[V2T] (skip) already has text for {article_id}")
                    return None
        except Exception:
            pass

        got = _get_image_anywhere(db, article_id)
        if not got:
            _log(f"[V2T] (skip) no image found for article={article_id}")
            return None

        img_bytes, mime, located_path = got
        image_b64 = _encode_jpeg_base64(img_bytes)
        prompt = _article_prompt(language)
        text = _call_qwen_vl_article(image_b64, prompt, model=model)

        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out_dir = Path(RESULTS_DIR) / article_id / "_gen" / "image_to_article"
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / f"{model}@{ts}.md"
        md_path.write_text(text, encoding="utf-8")

        meta = {
            "model": model,
            "article_id": article_id,
            "image_source": located_path or "db:blob",
            "output_path": str(md_path),
            "prompt": prompt,
            "created_at": ts,
            "endpoint": OAI_COMPAT_URL,
        }
        (out_dir / f"{model}@{ts}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # DB bookkeeping (use your CRUD)
        try:
            kind = getattr(TextKind, "vision_article", None) or TextKind.original_text
            preview = text[:280]
            tokens = _approx_tokens(text)
            with db.begin():
                create_or_get_text(
                    db,
                    article_id=article_id,
                    kind=kind,
                    path=str(md_path),
                    preview=preview,
                    tokens=tokens,
                )
                mark_article_text_generated(db, article_id)
        except Exception as e:
            _log(f"[V2T] (note) DB write skipped: {e}")

        _log(f"[V2T] ✓ saved article: {md_path}")
        return {"article_id": article_id, "text_path": str(md_path)}

def generate_next_articles_below(
    start_article_id: str,
    count: int = 2,
    *,
    model: str = DEFAULT_VL_MODEL,
    language: str = "en",
    force: bool = False,
    dry_run: bool = False,
) -> List[str]:
    targets = _ids_below(start_article_id, count)
    if not targets:
        _log(f"[V2T] No numeric-suffix IDs found below {start_article_id}")
        return []

    _log(f"[V2T] Will process ({len(targets)}): {', '.join(targets)}")
    if dry_run:
        return targets

    done: List[str] = []
    for aid in targets:
        try:
            with SessionLocal() as db:
                art = db.get(Article, aid)
            if not art:
                _log(f"[V2T] (skip) article not found: {aid}")
                continue
            res = generate_article_for_image(aid, model=model, language=language, force=force)
            if res:
                done.append(aid)
        except Exception as e:
            _log(f"[V2T] ⚠ failed for {aid}: {e}")
    return done
