"""SQLAlchemy ORM models for the BookFlow index."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class LibraryFolder(Base):
    """A registered library root directory."""

    __tablename__ = "library_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(4096), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scan_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_scan_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_scan_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    books: Mapped[list[Book]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )


class Book(Base):
    """An indexed ebook file, located at folder.path + relative_path."""

    __tablename__ = "books"
    __table_args__ = (UniqueConstraint("folder_id", "relative_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder_id: Mapped[int] = mapped_column(
        ForeignKey("library_folders.id", ondelete="CASCADE"), nullable=False
    )
    relative_path: Mapped[str] = mapped_column(String(4096), nullable=False)

    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    authors: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    publisher: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    series: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    file_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    folder: Mapped[LibraryFolder] = relationship(back_populates="books")
    progression: Mapped[Progression | None] = relationship(
        back_populates="book", uselist=False, cascade="all, delete-orphan"
    )
    optimized: Mapped[list[OptimizedBook]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )


class Progression(Base):
    """Latest reading progression for a book (single-user: one per book)."""

    __tablename__ = "progressions"
    __table_args__ = (UniqueConstraint("book_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )

    progression: Mapped[float | None] = mapped_column(JSON, nullable=True)
    modified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    references: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    book: Mapped[Book] = relationship(back_populates="progression")


class OptimizedBook(Base):
    """A cached, device-optimized rendition of a source book."""

    __tablename__ = "optimized_books"
    __table_args__ = (UniqueConstraint("book_id", "profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    profile: Mapped[str] = mapped_column(String(8), nullable=False)

    source_mtime: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    optimized_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    book: Mapped[Book] = relationship(back_populates="optimized")
