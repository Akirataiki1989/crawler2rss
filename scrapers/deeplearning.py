import re
import json
import aiohttp
from datetime import datetime, timezone
from dateutil import parser as dateutil_parser

from model.orm import repository
from utils.rss_generator import generate_rss_for_source
from utils.logger import setup_logger

logger = setup_logger("Scrapers.DeepLearning")

BASE_URL = "https://www.deeplearning.ai"
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}
MAX_RETRIES = 3
LIMIT_INITIAL = 50
LIMIT_CATCHUP = 20


class DeepLearningBatchScraper:

    SCRAPER_ITEM_NAME = ["DeepLearning_TheBatch"]

    def _build_url(self, base_url: str, page: int) -> str:
        return base_url if page == 1 else f"{base_url}/page/{page}"

    def parse_html(self, html_content: str) -> list:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_content, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []

        posts = data.get("props", {}).get("pageProps", {}).get("posts", [])
        items = []

        for post in posts:
            slug = post.get("slug", "").strip()
            if not slug:
                continue
            link = f"{BASE_URL}/the-batch/{slug}/"
            title = post.get("title", "").strip()
            if not title:
                continue

            img_url = post.get("feature_image", "") or ""
            excerpt = (post.get("custom_excerpt") or post.get("excerpt") or "").strip()
            description = f'<img src="{img_url}" /><br/>{excerpt}' if img_url else excerpt

            pub_date = datetime.now(timezone.utc)
            published_at = post.get("published_at", "")
            if published_at:
                try:
                    pub_date = dateutil_parser.parse(published_at)
                except Exception:
                    pass

            items.append({
                "title": title,
                "link": link,
                "description": description,
                "pub_date": pub_date,
                "guid": link,
            })

        return items

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    return await resp.text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}")
                if attempt == MAX_RETRIES:
                    raise
        return ""

    async def scrape_and_store(self, session_maker):
        async with aiohttp.ClientSession(headers=HEADERS) as http:
            for source_name in self.SCRAPER_ITEM_NAME:
                source = await repository.get_source_by_name(session_maker, source_name)
                if source and not repository.source_matches_scraper(source, type(self)):
                    continue
                if not source:
                    logger.error(f"Source '{source_name}' not found in database.")
                    continue

                base_url = source.url.rstrip("/")
                latest_guid = await repository.get_latest_guid(session_maker, source.id)
                is_initial = latest_guid is None

                if is_initial:
                    logger.info(f"[{source_name}] DB empty — initial fetch.")
                else:
                    logger.info(f"[{source_name}] Latest GUID: {latest_guid}")

                collected = []
                collected_guids = set()
                done = False
                page = 1
                limit = LIMIT_INITIAL if is_initial else LIMIT_CATCHUP

                while not done:
                    url = self._build_url(base_url, page)
                    try:
                        html = await self._fetch(http, url)
                        page_items = self.parse_html(html)
                        logger.info(f"[{source_name}] Page {page}: {len(page_items)} items")
                    except Exception as e:
                        logger.error(f"[{source_name}] Failed page {page}: {e}")
                        break

                    if not page_items:
                        break

                    new_on_page = 0
                    for item in page_items:
                        if not is_initial and item["guid"] == latest_guid:
                            done = True
                            break
                        if item["guid"] not in collected_guids:
                            collected.append(item)
                            collected_guids.add(item["guid"])
                            new_on_page += 1
                            if len(collected) >= limit:
                                logger.warning(f"[{source_name}] Stop-loss: reached {limit} items.")
                                done = True
                                break

                    if not done and new_on_page == 0:
                        break

                    page += 1

                collected.reverse()
                logger.info(f"[{source_name}] Collected {len(collected)} new items.")

                if not collected:
                    logger.info(f"[{source_name}] No new items.")
                    await generate_rss_for_source(session_maker, source, new_items=[])
                    continue

                new_count = await repository.save_articles(
                    session_maker, source.id, collected, latest_guid=None
                )
                logger.info(f"[{source_name}] Saved {new_count} new items.")
                await generate_rss_for_source(session_maker, source, new_items=collected)
