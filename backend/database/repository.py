from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Download


def create_download(db: Session, data: dict[str, Any]) -> Download:
    download = Download(**data)
    db.add(download)
    db.commit()
    db.refresh(download)
    return download


def get_download(db: Session, download_id: str) -> Download | None:
    statement = select(Download).where(Download.id == download_id)
    return db.scalar(statement)


def update_download(
    db: Session,
    download_id: str,
    **fields: Any,
) -> Download | None:
    download = get_download(db, download_id)

    if download is None:
        return None

    for key, value in fields.items():
        if hasattr(download, key):
            setattr(download, key, value)

    db.commit()
    db.refresh(download)

    return download
