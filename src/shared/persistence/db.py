from sqlalchemy import event
from sqlmodel import SQLModel, create_engine, Session
from src.shared.config.settings import settings

_is_postgres = settings.DATABASE_URL.startswith("postgresql")
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith(
    "sqlite") else {}
_engine_kwargs: dict = {"echo": False, "connect_args": connect_args}
if _is_postgres:
    _engine_kwargs.update(
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )
engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

# Register pgvector types on every new raw psycopg connection so that
# SQLAlchemy can encode/decode vector columns transparently.
if _is_postgres:
    try:
        from pgvector.psycopg import register_vector

        @event.listens_for(engine.sync_engine if hasattr(engine, 'sync_engine') else engine, "connect")
        def _on_connect(dbapi_conn, _rec):
            register_vector(dbapi_conn)
    except Exception:  # pgvector not installed or sync engine variant
        pass


def init_db():
    # Import all model modules so SQLModel.metadata knows about every table.
    import src.shared.persistence.models.evaluation_models  # noqa: F401
    import src.shared.persistence.models.recommendation_models  # noqa: F401
    import src.shared.persistence.models.webhook_models  # noqa: F401

    # Will create all tables
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
