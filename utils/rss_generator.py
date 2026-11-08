import os
import re
from datetime import timezone
from email.utils import formatdate
from pathlib import Path
from typing import List
from feedgen.feed import FeedGenerator
from dotenv import load_dotenv

from utils.logger import setup_logger
from model.orm import repository
from model.orm.database import Source, Article

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logger = setup_logger("Utils.RSS")

RSS_OUTPUT_DIR = Path(os.environ.get("RSS_OUTPUT_DIR", ".")).resolve()


def _update_last_build_date(path: Path) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    now_rfc = formatdate(usegmt=True)
    updated = re.sub(
        r"<lastBuildDate>[^<]*</lastBuildDate>",
        f"<lastBuildDate>{now_rfc}</lastBuildDate>",
        content,
    )
    path.write_text(updated, encoding="utf-8")


async def generate_rss_for_source(
    session_maker, source: Source, new_items: list | None = None
) -> None:
    output_path = RSS_OUTPUT_DIR / source.rss_filename

    if new_items is not None and len(new_items) == 0:
        # 無新資料，只更新 lastBuildDate
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _update_last_build_date(output_path)
        logger.info(f"RSS lastBuildDate updated (no new items) — {source.name}")
        return

    rss_limit = max(50, len(new_items)) if new_items else 50
    items: List[Article] = await repository.get_articles_for_source(session_maker, source.id, limit=rss_limit)

    fg = FeedGenerator()
    fg.title(source.name)
    fg.link(href=source.url, rel="alternate")
    fg.description(f"Latest items from {source.name}")
    fg.language("zh-TW")

    for item in items:
        # feedgen prepends by default; preserve newest-first DB order for this feed.
        fe = fg.add_entry(order="append" if source.name == "SimplyPatrick" else "prepend")
        fe.id(item.guid)
        fe.title(item.title)
        fe.link(href=item.link)
        if item.description:
            fe.description(item.description)
        if item.pub_date:
            pub_date = item.pub_date
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            fe.pubDate(pub_date)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fg.rss_file(str(output_path))
    logger.info(f"RSS generated: {output_path} ({len(items)} items) — {source.name}")


async def generate_all_rss(session_maker) -> None:
    sources = await repository.get_active_sources(session_maker)
    for source in sources:
        await generate_rss_for_source(session_maker, source)
