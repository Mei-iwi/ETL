from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


def make_engine(url):
    options = {"connect_timeout": 5} if make_url(url).get_backend_name() == "postgresql" else {}
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True, connect_args=options)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def enable_fk(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


def sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)
