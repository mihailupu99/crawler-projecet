from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.scraper import scrape_latest_to_files, summarize_existing_results, RESULTS_DIR

app = FastAPI()
templates = Jinja2Templates(directory="app/web/templates")
app.mount("/static", StaticFiles(directory="crawled_results"), name="static")

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    posts = summarize_existing_results()
    return templates.TemplateResponse("home.html", {"request": request, "posts": posts})

@app.post("/scrape")
def scrape(limit: int = 5):
    rows, excel_path = scrape_latest_to_files(limit=limit)
    return JSONResponse({"inserted": len(rows), "excel": excel_path})

@app.get("/export.xlsx")
def export_xlsx():
    path = Path(RESULTS_DIR) / "posts.xlsx"
    if not path.exists():
        return JSONResponse({"error": "No export yet. Run /scrape first."}, status_code=404)
    return FileResponse(str(path), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="posts.xlsx")
