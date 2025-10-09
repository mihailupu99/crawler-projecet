from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
from .models import Article, Asset, TextRow, AssetKind, TextKind

def upsert_article(db: Session, *, id: str, url: str, title: str,
                   published_at: datetime | None, paragraphs_count: int):
    existing = db.get(Article, id)
    if existing:
        existing.title = title
        existing.url = url
        existing.paragraphs_count = paragraphs_count
        existing.updated_at = datetime.utcnow()
        if published_at:
            existing.published_at = published_at
        return existing
    a = Article(
        id=id, url=url, title=title,
        published_at=published_at, paragraphs_count=paragraphs_count
    )
    db.add(a)
    return a

def create_or_get_text(db: Session, *, article_id: str, kind: TextKind,
                       path: str, preview: str | None, tokens: int | None = None) -> TextRow:
    # Avoid duplicates for same article/kind/path
    q = select(TextRow).where(
        TextRow.article_id == article_id,
        TextRow.kind == kind,
        TextRow.path == path
    )
    row = db.execute(q).scalars().first()
    if row:
        return row
    row = TextRow(article_id=article_id, kind=kind, path=path, preview=preview, tokens=tokens)
    db.add(row)
    return row

def create_or_get_asset(db: Session, *, article_id: str, kind: AssetKind,
                        path: str, mime: str | None = None, sha256: str | None = None,
                        width: int | None = None, height: int | None = None) -> int:
    q = select(Asset).where(
        Asset.article_id == article_id,
        Asset.kind == kind,
        Asset.path == path
    )
    row = db.execute(q).scalars().first()
    if row:
        return row.id
    row = Asset(
        article_id=article_id, kind=kind, path=path, mime=mime,
        sha256=sha256, width=width, height=height
    )
    db.add(row)
    db.flush()  # get id
    return row.id
