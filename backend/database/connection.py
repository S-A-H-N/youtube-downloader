import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


DATABASE_URL = os.environ.get("DATABASE_URL")


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = None


if DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
    )

    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )


def get_db():
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
