import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, text

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.schema import UniqueConstraint

Base = declarative_base()

class Source(Base):
    __tablename__ = 'sources'
    __table_args__ = {'schema': 'rss_feed'}

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    rss_filename = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    scraper_class = Column(String(255))  # e.g. "scrapers.hobbySearch.HobbySearchScraper"

class Article(Base):
    __tablename__ = 'articles'
    __table_args__ = (
        UniqueConstraint('source_id', 'guid', name='uix_source_guid'),
        {'schema': 'rss_feed'}
    )

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('rss_feed.sources.id', ondelete='CASCADE'), nullable=False)
    title = Column(String(255), nullable=False)
    link = Column(Text, nullable=False)
    description = Column(Text)
    pub_date = Column(DateTime(timezone=True))
    guid = Column(Text, nullable=False)

class ScrapeLog(Base):
    __tablename__ = 'scrape_logs'
    __table_args__ = {'schema': 'rss_feed'}

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('rss_feed.sources.id', ondelete='CASCADE'), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True))
    status = Column(String(50), nullable=False)
    new_items_count = Column(Integer, default=0)
    error_message = Column(Text)

_engine = None
_session_maker = None

def get_async_session_maker():
    global _engine, _session_maker
    if _session_maker is None:
        db_url = os.environ.get("DATABASE_URL","")
        _engine = create_async_engine(db_url, echo=False)
        _session_maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _session_maker

async def init_db():
    get_async_session_maker()  # 確保 _engine 已初始化
    async with _engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS rss_feed;"))
        await conn.run_sync(Base.metadata.create_all)
