from datetime import datetime, timezone
from typing import Literal, NotRequired, TypedDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from model.orm.database import Article, Source, ScrapeLog

SessionMaker = async_sessionmaker[AsyncSession]


class ArticleItem(TypedDict):
    title: str
    link: str
    guid: str
    description: NotRequired[str]
    pub_date: NotRequired[datetime]


async def get_latest_guid(session_maker: SessionMaker, source_id: int) -> str | None:
    async with session_maker() as session:
        stmt = select(Article.guid).where(Article.source_id == source_id).order_by(Article.id.desc()).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_active_sources(session_maker: SessionMaker) -> list[Source]:
    async with session_maker() as session:
        stmt = select(Source).where(Source.is_active == True)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_source_by_name(session_maker: SessionMaker, name: str) -> Source | None:
    async with session_maker() as session:
        stmt = select(Source).where(Source.name == name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_articles_for_source(session_maker: SessionMaker, source_id: int, limit: int = 50) -> list[Article]:
    async with session_maker() as session:
        stmt = select(Article).where(Article.source_id == source_id).order_by(Article.id.desc()).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def save_articles(
    session_maker: SessionMaker,
    source_id: int,
    items: list[ArticleItem],
    latest_guid: str | None = None,
) -> int:
    items_to_insert: list[ArticleItem] = []
    for item in items:
        if latest_guid and item['guid'] == latest_guid:
            break
        items_to_insert.append(item)

    items_to_insert.reverse()
    new_count = 0

    async with session_maker() as session:
        for item in items_to_insert:
            try:
                session.add(Article(
                    source_id=source_id,
                    title=item['title'],
                    link=item['link'],
                    description=item.get('description', ''),
                    pub_date=item.get('pub_date') or datetime.now(timezone.utc),
                    guid=item['guid'],
                ))
                await session.commit()
                new_count += 1
            except IntegrityError:
                await session.rollback()
            except Exception:
                await session.rollback()
                raise

    return new_count


async def log_scrape_task(
    session_maker: SessionMaker,
    source_id: int,
    started_at: datetime,
    ended_at: datetime,
    status: Literal["SUCCESS", "FAILED"],
    new_items_count: int,
    error_message: str | None = None,
) -> None:
    async with session_maker() as session:
        session.add(ScrapeLog(
            source_id=source_id,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            new_items_count=new_items_count,
            error_message=error_message,
        ))
        await session.commit()
