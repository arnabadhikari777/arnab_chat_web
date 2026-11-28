import logging
from logging.config import fileConfig

from flask import current_app

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name, disable_existing_loggers=False)   # keep the app's own loggers alive
logger = logging.getLogger('alembic.env')


def get_engine():
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
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
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


def _collapse_redundant_versions(connection):
    """A database stamped with several revisions of the SAME chain (an old branch left it with e.g. both
    e5b3... and f1a2...) makes Alembic stop with "overlaps with other requested revisions".
    A stamped revision that is an ancestor of another stamped revision carries no information, so it is removed.
    Only alembic_version bookkeeping rows are touched; no user data."""
    from alembic.script import ScriptDirectory
    import sqlalchemy as sa

    if "alembic_version" not in sa.inspect(connection).get_table_names():
        return
    rows = [r[0] for r in connection.execute(sa.text("select version_num from alembic_version"))]
    if len(rows) < 2:
        return
    script = ScriptDirectory.from_config(config)
    keep = set(rows)
    try:
        for rev in rows:
            for r in script.iterate_revisions(rev, "base"):
                if r.revision != rev:
                    keep.discard(r.revision)
    except Exception:  # unknown revision: leave the table alone and let Alembic report it
        return
    redundant = [r for r in rows if r not in keep]
    for rev in redundant:
        connection.execute(sa.text("delete from alembic_version where version_num = :v"), {"v": rev})
        logger.warning("alembic_version: removed %s (an ancestor of another stamped revision)", rev)
    if redundant:
        connection.commit()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    connectable = get_engine()

    with connectable.connect() as connection:
        _collapse_redundant_versions(connection)
        connection.commit()  # end the implicit transaction so Alembic starts (and commits) its own
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
