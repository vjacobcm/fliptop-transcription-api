import logging
from collections.abc import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

logger = logging.getLogger(__name__)

connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=connect_args)


def _add_missing_columns() -> None:
    """Add nullable columns that `create_all` cannot add to existing tables.

    A stopgap until this project has real migrations: it only handles adding
    nullable columns, which is all the schema has needed so far.
    """
    inspector = inspect(engine)

    for table, column, ddl in (("segment", "source", "VARCHAR"),):
        if not inspector.has_table(table):
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        if column in existing:
            continue

        logger.info("Adding %s.%s to the existing database", table, column)
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    # Import registers the tables on SQLModel.metadata before create_all.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()

    from app.services.glossary import seed_glossary
    from app.services.search import init_search

    with Session(engine) as session:
        seed_glossary(session)

    init_search()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
