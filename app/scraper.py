# app/scraper.py
import os
import re
import json
import time
import mimetypes
from html import unescape
from pathlib import Path
from urllib.parse import urljoin
import requests
from requests.exceptions import RequestException
from openpyxl import Workbook
from bs4 import BeautifulSoup

RESULTS_DIR = "crawled_results"
DEFAULT_UA = "MihaiNewsmakerScraper/2.0 (+contact: you@example.com)"
ID_PREFIX = "11LM"
ID_PAD = 3  # 000, 001, ...

# A slightly richer set of headers that helps some CDNs accept non-browser requests
RICH_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en;q=0.9",
}

# ---------- helpers for ID & de-dup ----------


def _existing_urls(results_dir: str = RESULTS_DIR) -> set[str]:
    urls = set()
    if not os.path.isdir(results_dir):
        return urls
    for name in os.listdir(results_dir):
        folder = os.path.join(results_dir, name)
        meta = os.path.join(folder, f"{name}.json")
        if os.path.isfile(meta):
            try:
                with open(meta, "r", encoding="utf-8") as f:
                    u = json.load(f).get("url")
                    if u:
                        urls.add(u)
            except Exception:
                pass
    return urls


def _next_index(results_dir: str = RESULTS_DIR) -> int:
    """Scan folders like 11LM000 and return the next integer (keeps counting across runs)."""
    mx = -1
    if os.path.isdir(results_dir):
        for name in os.listdir(results_dir):
            m = re.match(rf"^{ID_PREFIX}(\d+)$", name)
            if m:
                try:
                    mx = max(mx, int(m.group(1)))
                except ValueError:
                    pass
    return mx + 1


def _make_id(n: int) -> str:
    return f"{ID_PREFIX}{n:0{ID_PAD}d}"


# ---------- WP API bits ----------


def guess_wp_api_base(site_base: str) -> str:
    # prefer language-scoped API (/ro/wp-json/), fall back to root if missing
    from urllib.parse import urlparse, urlunparse

    site_base = site_base.rstrip("/")
    lang_api = f"{site_base}/wp-json/"
    p = urlparse(site_base)
    root = urlunparse((p.scheme, p.netloc, "", "", "", "")).rstrip("/")
    root_api = f"{root}/wp-json/"
    if is_wp_api_available(lang_api):
        return lang_api
    return root_api


def is_wp_api_available(api_base: str, timeout: float = 8.0) -> bool:
    try:
        r = requests.get(api_base, headers={"User-Agent": DEFAULT_UA}, timeout=timeout)
        return r.status_code == 200 and isinstance(r.json(), dict)
    except Exception:
        return False


def backoff_retry_get(
    url: str,
    params: dict | None = None,
    timeout: float = 15.0,
    max_tries: int = 4,
    headers: dict | None = None,
):
    """GET with exponential-ish backoff and optional custom headers."""
    tries = 0
    req_headers = {"User-Agent": DEFAULT_UA, **(headers or {})}
    while True:
        tries += 1
        try:
            r = requests.get(url, params=params, headers=req_headers, timeout=timeout)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "2"))
                time.sleep(min(retry_after, 10))
                if tries < max_tries:
                    continue
            r.raise_for_status()
            return r
        except RequestException:
            if tries >= max_tries:
                raise
            time.sleep(1.5 * tries)


def fetch_wp_posts(site_base: str, limit: int = 10) -> list[dict]:
    api_base = guess_wp_api_base(site_base)
    url = urljoin(api_base, "wp/v2/posts")
    r = backoff_retry_get(
        url,
        params={
            "per_page": min(100, max(1, limit)),
            "page": 1,
            "orderby": "date",
            "order": "desc",
            "_embed": "1",
            # include yoast_head_json for og:image fallback
            "_fields": "id,date,link,title,content,_embedded,yoast_head_json",
        },
    )
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("Unexpected response from WP API")
    return data[:limit]


def _get_featured_image_src(item: dict) -> str | None:
    """Try several ways to extract a representative image for a post."""
    emb = item.get("_embedded", {}) or {}
    media = emb.get("wp:featuredmedia") or []
    if isinstance(media, list) and media:
        m0 = media[0] or {}
        # Prefer the "full" size when available
        sizes = ((m0.get("media_details") or {}).get("sizes") or {})
        full = sizes.get("full") or sizes.get("large") or sizes.get("medium")
        if isinstance(full, dict) and full.get("source_url"):
            return full["source_url"]
        if m0.get("source_url"):
            return m0["source_url"]

    # Yoast/OpenGraph image (common on many WP sites)
    yoast = item.get("yoast_head_json") or {}
    if isinstance(yoast, dict):
        og = yoast.get("og_image")
        if isinstance(og, list) and og and isinstance(og[0], dict) and og[0].get("url"):
            return og[0]["url"]

    return None


def _first_img_from_content(item: dict) -> str | None:
    """Fallback to the first <img> from post content if featured/OG image is missing."""
    content_html = (item.get("content") or {}).get("rendered", "") or ""
    soup = BeautifulSoup(content_html, "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        return unescape(img["src"])
    return None


def _normalize_post(item: dict) -> dict:
    title_html = (item.get("title") or {}).get("rendered", "") or ""
    soup_title = BeautifulSoup(title_html, "html.parser")
    title = soup_title.get_text(strip=True)

    content_html = (item.get("content") or {}).get("rendered", "") or ""
    soup = BeautifulSoup(content_html, "html.parser")
    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    body_text = "\n\n".join(paragraphs)

    # Try featured image, then OG image, then first <img> in content
    img = _get_featured_image_src(item) or _first_img_from_content(item)

    return {
        "Title": title,
        "Date": item.get("date") or "",
        "URL": item.get("link") or "",
        "Body": body_text,
        "ImageURL": img,
        "ParagraphsCount": len(paragraphs),
    }


def _save_post_files(row: dict, id_str: str) -> dict:
    folder = Path(RESULTS_DIR) / id_str
    folder.mkdir(parents=True, exist_ok=True)

    # text
    (folder / f"{id_str}.txt").write_text(row["Body"], encoding="utf-8")

    # image (with better resilience + visibility into failures)
    img_path = None
    img_error = None
    if row.get("ImageURL"):
        try:
            # some origins require a referer and broader Accept header
            headers = {**RICH_HEADERS, "Referer": row.get("URL") or ""}
            ir = backoff_retry_get(row["ImageURL"], timeout=20.0, headers=headers)

            # Derive extension from Content-Type when URL doesn't have one / has CDN params
            ct = ir.headers.get("Content-Type", "").split(";")[0].strip().lower()
            guessed_ext = mimetypes.guess_extension(ct) if ct else None
            if not guessed_ext:
                # fallback to URL path extension (minus query), then .jpg
                guessed_ext = os.path.splitext(row["ImageURL"])[-1].split("?")[0] or ".jpg"
            if guessed_ext == ".jpe":
                guessed_ext = ".jpg"

            img_path = folder / f"{id_str}{guessed_ext}"
            img_path.write_bytes(ir.content)

        except Exception as e:
            img_error = f"{type(e).__name__}: {e}"
            img_path = None

    # metadata JSON (include image diagnostics)
    metadata = {
        "url": row["URL"],
        "title": row["Title"],
        "date": row["Date"],
        "image_url": row.get("ImageURL"),
        "paragraphs_count": row.get("ParagraphsCount", 0),
        "image_saved_path": str(img_path) if img_path else None,
        "image_error": img_error,
    }
    (folder / f"{id_str}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "ID": id_str,
        "Title": row["Title"],
        "Date": row["Date"],
        "URL": row["URL"],
        "Body": row["Body"],
        "ImagePath": str(img_path) if img_path else None,
    }


def scrape_latest_wp_to_files(site_base: str, limit: int = 5):
    Path(RESULTS_DIR).mkdir(parents=True, exist_ok=True)

    # fetch latest from WP
    wp_items = fetch_wp_posts(site_base, limit=limit)
    normalized = [_normalize_post(it) for it in wp_items]

    # filter out URLs we already saved
    seen = _existing_urls()
    fresh = [r for r in normalized if r["URL"] and r["URL"] not in seen]
    if not fresh:
        # still rebuild Excel from current run to keep compatibility
        excel_path = Path(RESULTS_DIR) / "posts.xlsx"
        _write_excel_from_rows([], excel_path)
        return [], str(excel_path)

    # allocate sequential 11LM IDs
    n = _next_index()
    saved_rows = []
    for row in fresh:
        id_str = _make_id(n)
        saved_rows.append(_save_post_files(row, id_str))
        n += 1

    # write excel for JUST the newly saved rows (same behavior you had)
    excel_path = Path(RESULTS_DIR) / "posts.xlsx"
    _write_excel_from_rows(saved_rows, excel_path)
    return saved_rows, str(excel_path)


def _write_excel_from_rows(rows: list[dict], excel_path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Posts"
    ws.append(["ID", "Title", "Date", "URL", "Body", "ImagePath"])
    for r in rows:
        ws.append([r["ID"], r["Title"], r["Date"], r["URL"], r["Body"], r["ImagePath"]])
    wb.save(excel_path)
