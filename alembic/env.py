from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context

# ── Make project root importable ────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Import ALL SQLModel table models (required for autogenerate) ────
import src.shared.persistence.models.evaluation_models   # noqa: F401, E402
import src.shared.persistence.models.recommendation_models  # noqa: F401, E402
import src.shared.persistence.models.webhook_models      # noqa: F401, E402

# ── Register pgvector type so autogenerate renders Vector(...) correctly ─
try:
    from pgvector.sqlalchemy import Vector  # noqa: F401, E402
except ImportError:
    pass

# ── Read DATABASE_URL from app settings (.env aware) ────────────────
from src.shared.config.settings import settings  # noqa: E402

# ── Alembic config ───────────────────────────────────────────────────
config = context.config

# Override sqlalchemy.url from .env / settings (not alembic.ini)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def include_sqlmodel_import(migration_script) -> None:  # noqa: ARG001
    """Ensure 'import sqlmodel' is present in every generated migration file."""
    pass


# Alembic script post-processing: inject 'import sqlmodel' after 'import sqlalchemy as sa'
from alembic.autogenerate import renderers  # noqa: E402


@renderers.dispatch_for(type(None))
def _noop(autogen_context, element):  # noqa: ARG001
    return ""


def process_revision_directives(context, revision, directives):  # noqa: ARG001
    """Hook called after autogenerate — injects 'import sqlmodel' into each migration."""
    for directive in directives:
        if hasattr(directive, "imports"):
            directive.imports.add("import sqlmodel")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        process_revision_directives=process_revision_directives,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            process_revision_directives=process_revision_directives,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
