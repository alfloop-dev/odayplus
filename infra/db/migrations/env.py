import os
from logging.config import fileConfig

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import Column, MetaData, String, Table, engine_from_config, inspect, pool, select

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if (
    config.config_file_name is not None
    and config.file_config.has_section("loggers")
):
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = None
APPLICATION_VERSION_TABLE = "oday_plus_alembic_version"


def version_table_for(connection) -> tuple[str, str | None]:
    """Keep other applications' migration history intact on a shared database.

    Existing ODay installations keep their recognized legacy history. New
    installations use a separate table; we never stamp, rename or erase a
    foreign revision to make an upgrade proceed. Ambiguous application state
    requires diagnosis before any migration is applied.
    """
    schema = "public" if connection.dialect.name == "postgresql" else None
    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema=schema))
    legacy_revisions = set()
    if "alembic_version" in tables:
        legacy = Table(
            "alembic_version", MetaData(), Column("version_num", String), schema=schema
        )
        legacy_revisions = set(connection.execute(select(legacy.c.version_num)).scalars())
    known_revisions = {
        revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()
    }
    application_legacy_revisions = legacy_revisions & known_revisions
    if application_legacy_revisions:
        if application_legacy_revisions != legacy_revisions:
            raise RuntimeError("Refusing mixed application and foreign legacy migration history")
        if APPLICATION_VERSION_TABLE in tables:
            raise RuntimeError("Refusing application history in both migration version tables")
        return "alembic_version", schema
    if APPLICATION_VERSION_TABLE not in tables and schema is not None:
        if inspector.has_table("tenants", schema="core"):
            raise RuntimeError(
                "Untracked existing application schema; inspect migration history before upgrading"
            )
    return APPLICATION_VERSION_TABLE, schema

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("ODAY_DATABASE_URL")
    if not url:
        raise RuntimeError("Set ODAY_DATABASE_URL or sqlalchemy.url before running migrations")
    # Alembic stores options in ConfigParser, where URL-encoded '%' characters
    # must be doubled before assignment to survive interpolation.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() will emit the given string to the
    script output.

    """
    url = database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "pyformat"},
        version_table=APPLICATION_VERSION_TABLE,
        version_table_schema="public" if url.startswith("postgres") else None,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    database_url()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Inspection starts a transaction in SQLAlchemy 2. Own that transaction so
    # version writes and PostgreSQL DDL commit or roll back together.
    with connectable.begin() as connection:
        version_table, version_schema = version_table_for(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=version_table,
            version_table_schema=version_schema,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
