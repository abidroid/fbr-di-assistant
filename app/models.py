"""SQLAlchemy ORM models: the `users` and `documents` tables in Neon.

Chunks and vectors are not stored here; they live in Qdrant, linked by
``doc_id`` and ``owner_id`` in each point's payload.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DocumentStatus(enum.StrEnum):
    """Lifecycle of an uploaded document through the ingestion pipeline."""

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class User(Base):
    """An account that owns documents and asks questions.

    Attributes:
        id: Random UUID primary key (not guessable, unlike 1, 2, 3...).
        email: Login name; unique across all users.
        full_name: Display name.
        hashed_password: argon2 hash; the plain password is never stored.
        created_at: Set by the database when the row is inserted.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Document(Base):
    """One uploaded file and the result of ingesting it.

    Attributes:
        id: Random UUID; also the ``doc_id`` in Qdrant payloads and the file name on disk.
        owner_id: The user who uploaded it; their documents go when the user is deleted.
        filename: Original file name, shown in citations.
        file_type: Lower-case extension: pdf, docx, pptx, txt or md.
        size_bytes: File size in bytes.
        sha256: Hash of the file bytes, used to detect duplicate uploads.
        status: Where the document is in the ingestion pipeline.
        pages: Page or slide count, once known.
        chunk_count: Number of chunks stored in Qdrant.
        ocr_pages: Pages that needed OCR or captioning (no text layer).
        warnings: Non-fatal notes from ingestion, e.g. suspicious text found.
        error: Why ingestion failed, when status is ``failed``.
        created_at: Upload time.
        updated_at: Last status change.
    """

    __tablename__ = "documents"
    # The same user cannot upload identical bytes twice; different users can.
    __table_args__ = (
        UniqueConstraint("owner_id", "sha256", name="uq_documents_owner_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # native_enum=False stores a VARCHAR plus a CHECK constraint instead of a
    # Postgres ENUM type, so adding a status later is a simple migration.
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            native_enum=False,
            length=20,
            name="document_status",
            create_constraint=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=DocumentStatus.QUEUED,
    )
    pages: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
