import os, json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from openpyxl import Workbook

BASE_URL = "https://newsmaker.md/ro"
RESULTS_DIR = "crawled_results"

def get_latest_post_links(limit=5):
    resp = requests.get(BASE_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.select("article a"):
        href = a.get("href")
        if href and href.startswith("https://newsmaker.md/ro/") and href not in links:
            links.append(href)
        if len(links) >= limit:
            break
    return links

def scrape_post(url, index):
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("h1"); title = title_tag.get_text(strip=True) if title_tag else ""
    date_tag = soup.find("time"); date = date_tag.get_text(strip=True) if date_tag else ""
    img_tag = soup.select_one("img.attachment-large")
    img_url = urljoin(url, img_tag["src"]) if img_tag else None

    paragraphs = []
    for p in soup.select("div.elementor-widget-container p"):
        text = p.get_text(strip=True)
        if text and not text.startswith("["):
            paragraphs.append(text)
    body_text = "\n\n".join(paragraphs)

    folder = os.path.join(RESULTS_DIR, f"11LM{index}")
    os.makedirs(folder, exist_ok=True)

    with open(os.path.join(folder, f"11LM{index}.txt"), "w", encoding="utf-8") as f:
        f.write(body_text)

    metadata = {
        "url": url,
        "title": title,
        "date": date,
        "image_url": img_url,
        "paragraphs_count": len(paragraphs)
    }
    with open(os.path.join(folder, f"11LM{index}.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    img_path = None
    if img_url:
        try:
            img_resp = requests.get(img_url, timeout=15)
            ext = os.path.splitext(img_url)[-1].split("?")[0] or ".jpg"
            img_path = os.path.join(folder, f"11LM{index}{ext}")
            with open(img_path, "wb") as f:
                f.write(img_resp.content)
        except Exception:
            img_path = None

    return {
        "ID": f"11LM{index}",
        "Title": title,
        "Date": date,
        "URL": url,
        "Body": body_text,
        "ImagePath": img_path
    }

def scrape_latest_to_files(limit=5):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    links = get_latest_post_links(limit)
    rows = []
    for i, link in enumerate(links):
        rows.append(scrape_post(link, i))
    # write excel for compatibility
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Posts"
    ws.append(["ID", "Title", "Date", "URL", "Body", "ImagePath"])
    for r in rows:
        ws.append([r["ID"], r["Title"], r["Date"], r["URL"], r["Body"], r["ImagePath"]])
    excel_path = os.path.join(RESULTS_DIR, "posts.xlsx")
    wb.save(excel_path)
    return rows, excel_path

def summarize_existing_results():
    """Return lightweight rows for the UI from existing JSONs if present."""
    out = []
    if not os.path.isdir(RESULTS_DIR):
        return out
    for name in sorted(os.listdir(RESULTS_DIR)):
        folder = os.path.join(RESULTS_DIR, name)
        meta = os.path.join(folder, f"{name}.json")
        if os.path.isfile(meta):
            try:
                with open(meta, "r", encoding="utf-8") as f:
                    m = json.load(f)
                out.append({
                    "id": name,
                    "title": m.get("title",""),
                    "date": m.get("date",""),
                    "url": m.get("url",""),
                })
            except Exception:
                pass
    return out
