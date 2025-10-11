from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
from .models import Article, Asset, TextRow, AssetKind, TextKind


def upsert_article(
    db: Session,
    *,
    id: str,
    url: str,
    title: str,
    published_at: datetime | None,
    paragraphs_count: int,
):
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
        id=id,
        url=url,
        title=title,
        published_at=published_at,
        paragraphs_count=paragraphs_count,
    )
    db.add(a)
    return a


def create_or_get_text(
    db: Session,
    *,
    article_id: str,
    kind: TextKind,
    path: str,
    preview: str | None,
    tokens: int | None = None,
) -> TextRow:
    # Avoid duplicates for same article/kind/path
    q = select(TextRow).where(
        TextRow.article_id == article_id, TextRow.kind == kind, TextRow.path == path
    )
    row = db.execute(q).scalars().first()
    if row:
        return row
    row = TextRow(
        article_id=article_id, kind=kind, path=path, preview=preview, tokens=tokens
    )
    db.add(row)
    return row


def create_or_get_asset(
    db: Session,
    *,
    article_id: str,
    kind: AssetKind,
    path: str,
    mime: str | None = None,
    sha256: str | None = None,
    width: int | None = None,
    height: int | None = None,
    data: bytes | None = None,
    size_bytes: int | None = None,
) -> int:
    """
    Create asset if (article_id, kind, path) not present.
    Note: 'sha256' is globally unique in your schema. If the same sha256 already exists
    for a DIFFERENT article, this function will raise to avoid returning an asset
    bound to another article.
    """
    q = select(Asset).where(
        Asset.article_id == article_id, Asset.kind == kind, Asset.path == path
    )
    row = db.execute(q).scalars().first()
    if row:
        return row.id

    if sha256:
        q2 = select(Asset).where(Asset.sha256 == sha256)
        existing_by_hash = db.execute(q2).scalars().first()
        if existing_by_hash:
            if existing_by_hash.article_id != article_id:
                raise ValueError(
                    "Asset with same sha256 already exists for a different article. "
                    "Either remove the unique constraint on 'assets.sha256' or pass sha256=None here."
                )
            return existing_by_hash.id

    row = Asset(
        article_id=article_id,
        kind=kind,
        path=path,
        mime=mime,
        sha256=sha256,
        width=width,
        height=height,
        data=data,
        size_bytes=size_bytes,
    )
    db.add(row)
    db.flush()  # get id
    return row.id


def mark_article_image_generated(db: Session, article_id: str) -> None:
    a = db.get(Article, article_id)
    if not a:
        raise ValueError(f"Article {article_id} not found")
    a.has_image_generated = True
    a.image_generated_at = datetime.utcnow()
    a.updated_at = datetime.utcnow()
    db.add(a)


def mark_article_text_generated(db: Session, article_id: str) -> None:
    a = db.get(Article, article_id)
    if not a:
        raise ValueError(f"Article {article_id} not found")
    a.has_text_generated = True
    a.text_generated_at = datetime.utcnow()
    a.updated_at = datetime.utcnow()
    db.add(a)


def get_pending_articles_for_t2i(
    db: Session, *, limit: int = 50
) -> list[tuple[str, str, str]]:
    """
    Return (id, title, url) for articles with no generated image yet.
    """
    stmt = (
        select(Article.id, Article.title, Article.url)
        .where(Article.has_image_generated.is_(False))
        .order_by(Article.created_at.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).all())
