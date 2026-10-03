from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .connection import Base


class Download(Base):
    __tablename__ = "downloads"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)

    downloaded_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )

    total_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    progress: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    speed_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )

    content_type: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    supports_resume: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    resumed_from_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )

    file_available: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    storage: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    b2_bucket: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    b2_object: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
