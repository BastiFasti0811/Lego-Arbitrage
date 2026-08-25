"""Alembic migration environment."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# Import all models so Alembic can detect them
# Das ganze Paket, keine Handliste: DismissedOffer stand nicht darin und
# landete nur deshalb im Metadata, weil app/models/__init__.py jedes Modell
# importiert. Wer die Liste je auf Einzelimporte umstellt, laesst `alembic
# check` das Fehlen einer Tabelle als "drop it" vorschlagen.
import app.models  # noqa: F401 — registriert jedes Modell an Base.metadata
from alembic import context
from app.config import settings
from app.models.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
