"""資料庫連線與 session 工廠。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def make_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    engine = create_engine(database_url or get_settings().database_url, future=True)
    return sessionmaker(engine, expire_on_commit=False)
