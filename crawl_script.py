import os
import requests
from bs4 import BeautifulSoup
import json
from urllib.parse import urljoin
from openpyxl import Workbook

BASE_URL = "https://newsmaker.md/ro"
RESULTS_DIR = "crawled_results"  # Main directory for all results

def get_latest_post_links(limit=5):
    resp = requests.get(BASE_URL)
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.select("article a"):  # homepage articles
        href = a.get("href")
        if href and href.startswith("https://newsmaker.md/ro/") and href not in links:
            links.append(href)
        if len(links) >= limit:
            break
    return links

def scrape_post(url, index):
    resp = requests.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    # Title
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # Date
    date_tag = soup.find("time")
    date = date_tag.get_text(strip=True) if date_tag else ""

    # Main image
    img_tag = soup.select_one("img.attachment-large")
    img_url = urljoin(url, img_tag["src"]) if img_tag else None

    # Article body
    paragraphs = []
    for p in soup.select("div.elementor-widget-container p"):
        text = p.get_text(strip=True)
        if text and not text.startswith("["):  # skip ads
            paragraphs.append(text)
    body_text = "\n\n".join(paragraphs)

    # Folder for post inside crawled_results
    folder = os.path.join(RESULTS_DIR, f"11LM{index}")
    os.makedirs(folder, exist_ok=True)

    # Save text
    text_path = os.path.join(folder, f"11LM{index}.txt")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(body_text)

    # Save metadata JSON
    metadata = {
        "url": url,
        "title": title,
        "date": date,
        "image_url": img_url,
        "paragraphs_count": len(paragraphs)
    }
    json_path = os.path.join(folder, f"11LM{index}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    # Save image
    img_path = None
    if img_url:
        img_resp = requests.get(img_url)
        ext = os.path.splitext(img_url)[-1].split("?")[0]
        img_path = os.path.join(folder, f"11LM{index}{ext}")
        with open(img_path, "wb") as f:
            f.write(img_resp.content)

    print(f"Saved post {index}: {title[:50]}...")

    # Return structured data for Excel
    return {
        "ID": f"11LM{index}",
        "Title": title,
        "Date": date,
        "URL": url,
        "Body": body_text,
        "ImagePath": img_path
    }

if __name__ == "__main__":
    # Create main results directory if not exists
    os.makedirs(RESULTS_DIR, exist_ok=True)

    links = get_latest_post_links(5)

    # Create Excel workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Posts"
    ws.append(["ID", "Title", "Date", "URL", "Body", "ImagePath"])

    for i, link in enumerate(links):
        data = scrape_post(link, i)
        ws.append([data["ID"], data["Title"], data["Date"], data["URL"], data["Body"], data["ImagePath"]])

    # Save Excel file inside crawled_results folder
    excel_path = os.path.join(RESULTS_DIR, "posts.xlsx")
    wb.save(excel_path)

    print(f"Excel file '{excel_path}' created.")
