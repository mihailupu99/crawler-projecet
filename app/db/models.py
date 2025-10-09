from __future__ import annotations
from sqlalchemy import String, Integer, Enum, ForeignKey, DateTime, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
import enum
from .base import Base

class AssetKind(str, enum.Enum):
    original_image = "original_image"
    thumb = "thumb"
    generated_image = "generated_image"
    other = "other"

class TextKind(str, enum.Enum):
    original_text = "original_text"
    generated_text_from_image = "generated_text_from_image"
    other = "other"

class Direction(str, enum.Enum):
    image_to_text = "image→text"
    text_to_image = "text→image"

class RunStatus(str, enum.Enum):
    queued="queued"; running="running"; succeeded="succeeded"; failed="failed"; skipped="skipped"

class Article(Base):
    __tablename__ = "articles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g., "11LM001"
    url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1024))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paragraphs_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    assets: Mapped[list[Asset]] = relationship(back_populates="article", cascade="all, delete-orphan")
    texts:  Mapped[list[TextRow]] = relationship(back_populates="article", cascade="all, delete-orphan")
    runs:   Mapped[list[Run]] = relationship(back_populates="article", cascade="all, delete-orphan")

class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    kind: Mapped[AssetKind] = mapped_column(Enum(AssetKind))
    path: Mapped[str] = mapped_column(String(1024))        # relative to RESULTS_DIR
    mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    article: Mapped[Article] = relationship(back_populates="assets")

class TextRow(Base):
    __tablename__ = "texts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    kind: Mapped[TextKind] = mapped_column(Enum(TextKind))
    path: Mapped[str] = mapped_column(String(1024))        # stored on disk
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)  # first ~512 chars
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    article: Mapped[Article] = relationship(back_populates="texts")

class ModelRow(Base):
    __tablename__ = "models"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    modality: Mapped[str] = mapped_column(String(64))  # 'vision-llm', 'text-llm', 'text-to-image'
    default_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"), nullable=True)
    input_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    input_text_id: Mapped[int | None] = mapped_column(ForeignKey("texts.id"), nullable=True)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.queued)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    article: Mapped[Article] = relationship(back_populates="runs")
    model: Mapped[ModelRow | None] = relationship()
