# app/generation.py
from __future__ import annotations
import os, json, time
from pathlib import Path
from typing import Optional
import requests

from app.scraper import RESULTS_DIR
from app.db.session import SessionLocal
from app.db.crud import create_or_get_asset
from app.db.models import AssetKind

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
# Singapore/international synchronous endpoint for Qwen-Image (recommended by docs)
T2I_SYNC_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
MODEL_NAME = "qwen-image-plus"  # per docs, this and qwen-image are supported

def _build_prompt(title: str, body: str, max_chars: int = 750) -> str:
    """
    Build a concise visual prompt from article text.
    Keep under the 800-char limit; include title + first 1-2 sentences.
    """
    title = (title or "").strip()
    body = (body or "").strip().replace("\n", " ")
    piece = (body[: max_chars - len(title) - 20]).strip()
    # A tiny style hint helps diffusion models
    return f"{title}. Photorealistic editorial illustration about: {piece}"

def _download(url: str, dest: Path) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return r.content

def generate_t2i_for_article(article_id: str, title: str, body: str,
                             size: str = "1664*928") -> dict:
    """
    Generate an image from text for a given article, save under:
      crawled_results/<ID>/_gen/text_to_image/<model>@<ts>.png
    And register it in DB as assets.kind=generated_image.
    Returns a short dict with paths/ids.
    """
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    prompt = _build_prompt(title, body)
    # Build request per Qwen-Image sync API
    payload = {
        "model": MODEL_NAME,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]
        },
        "parameters": {
            "size": size,          # 16:9 to match your cards
            "prompt_extend": True, # let model rewrite short prompts
            "watermark": False,
        },
    }
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(T2I_SYNC_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    # Per docs: image URL lives at output.choices[0].message.content[0].image
    # URL lives ~24h -> download immediately.
    try:
        image_url = data["output"]["choices"][0]["message"]["content"][0]["image"]
    except Exception:
        raise RuntimeError(f"Unexpected response: {json.dumps(data)[:500]}")

    # Persist to disk
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = Path(RESULTS_DIR) / article_id / "_gen" / "text_to_image"
    out_path = out_dir / f"{MODEL_NAME}@{ts}.png"
    img_bytes = _download(image_url, out_path)

    # Register in DB
    with SessionLocal() as db:
        create_or_get_asset(
            db,
            article_id=article_id,
            kind=AssetKind.generated_image,
            path=str(out_path.as_posix()),
            mime="image/png",
            sha256=None,
            width=None,
            height=None,
        )
        db.commit()

    # Save call metadata for reproducibility
    meta_path = out_dir / f"{MODEL_NAME}@{ts}.json"
    meta_path.write_text(
        json.dumps(
            {"request": payload, "response": data, "image_path": str(out_path)},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    return {"article_id": article_id, "image_path": str(out_path), "prompt": prompt}
