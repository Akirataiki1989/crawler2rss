"""Read Simply Patrick's article and TIL feeds into the local RSS collection."""

import asyncio
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree

import aiohttp

from model.orm import repository
from utils.logger import setup_logger
from utils.rss_generator import generate_rss_for_source

logger = setup_logger("Scrapers.SimplyPatrick")
BASE_URL = "https://blog.simplypatrick.com"
FEED_URLS = (f"{BASE_URL}/posts/index.xml", f"{BASE_URL}/tils/index.xml")
INITIAL_LIMIT = 50
MAX_FEED_BYTES = 16 * 1024 * 1024


def parse_feed(content: bytes) -> list[repository.ArticleItem]:
    root = ElementTree.fromstring(content)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Expected an RSS channel")
    items = []
    for entry in root.findall("./channel/item"):
        title = (entry.findtext("title") or "").strip()
        link = (entry.findtext("link") or "").strip()
        parsed = urlparse(link)
        if not title or len(title) > 255 or parsed.scheme != "https" or parsed.hostname != "blog.simplypatrick.com":
            raise ValueError("RSS item has an invalid title or article URL")
        if not (parsed.path.startswith("/posts/") or parsed.path.startswith("/tils/")):
            raise ValueError("RSS item URL is not an article")
        published = parsedate_to_datetime(entry.findtext("pubDate") or "")
        if published.tzinfo is None:
            raise ValueError("RSS item publication date has no timezone")
        items.append({
            "title": title,
            "link": link,
            "guid": link,
            "description": entry.findtext("description") or "",
            "pub_date": published.astimezone(timezone.utc),
        })
    if not items:
        raise ValueError("RSS feed contains no articles")
    return items


def select_new_items(feeds: list[list[repository.ArticleItem]], latest_guid: str | None) -> list[repository.ArticleItem]:
    unique = {item["guid"]: item for feed in feeds for item in feed}
    items = sorted(unique.values(), key=lambda item: (item["pub_date"], item["guid"]), reverse=True)
    if latest_guid is None:
        return items[:INITIAL_LIMIT]
    for index, item in enumerate(items):
        if item["guid"] == latest_guid:
            return items[:index]
    logger.warning("Latest saved article is absent from the feeds; checking all available items")
    return items


class SimplyPatrickScraper:
    SCRAPER_ITEM_NAME = ["SimplyPatrick"]

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> bytes:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > MAX_FEED_BYTES:
                    raise ValueError(f"Feed exceeds {MAX_FEED_BYTES} bytes: {url}")
            return bytes(body)

    async def scrape_and_store(self, session_maker):
        source = await repository.get_source_by_name(session_maker, self.SCRAPER_ITEM_NAME[0])
        if not source:
            logger.error("Source '%s' not found in database", self.SCRAPER_ITEM_NAME[0])
            return
        if not repository.source_matches_scraper(source, type(self)):
            return
        async with aiohttp.ClientSession(headers={"User-Agent": "crawler2RSS/1.0"}) as session:
            contents = await asyncio.gather(*(self._fetch(session, url) for url in FEED_URLS))
        feeds = [parse_feed(content) for content in contents]
        latest = await repository.get_latest_guid(session_maker, source.id)
        new_items = select_new_items(feeds, latest)
        logger.info("Found %d items across %d feeds; %d candidates", sum(map(len, feeds)), len(feeds), len(new_items))
        if not new_items:
            await generate_rss_for_source(session_maker, source, new_items=[])
            return
        saved = await repository.save_articles(session_maker, source.id, new_items)
        logger.info("Saved %d new articles", saved)
        await generate_rss_for_source(session_maker, source, new_items=new_items)
