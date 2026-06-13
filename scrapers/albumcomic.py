import re
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timezone

from model.orm import repository
from utils.rss_generator import generate_rss_for_source
from utils.logger import setup_logger

logger = setup_logger("Scrapers.AlbumComic")

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}

MAX_RETRIES = 3


class AlbumComicScraper:

    # Source names in DB — must match rss_feed.sources.name exactly
    SCRAPER_ITEM_NAME = [
        "日本販賣通_遊戲相關",
        "日本販賣通_流行影視寫真",
        "日本販賣通_日版畫冊",
        "日本販賣通_動漫ACG相關書籍",
    ]

    def parse_html(self, html_content: str) -> list:
        soup = BeautifulSoup(html_content, "html.parser")
        items = []
        seen = set()

        product_list = soup.find("ul", class_="pt_grid_list")
        if not product_list:
            return items

        for li in product_list.find_all("li", recursive=False):
            a_tag = li.find("a", class_="pt_items_block")
            if not a_tag:
                continue

            link = a_tag.get("href", "").strip()
            if not link:
                continue
            if not link.startswith("http"):
                link = "https://www.albumcomic.com.tw" + link
            if link in seen:
                continue
            seen.add(link)

            title_tag = li.find("h4", class_="pt_title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            if not title:
                span = li.find("span", class_="pt_photo")
                title = span.get("title", "").strip() if span else ""
            if not title:
                continue

            img_url = ""
            span = li.find("span", class_="pt_photo")
            if span:
                style = span.get("style", "")
                m = re.search(r'background-image:\s*url\(["\']?([^"\')\s]+)["\']?\)', style)
                if m:
                    img_url = m.group(1).strip()

            items.append({
                "title": title,
                "link": link,
                "description": f'<img src="{img_url}" /><br/>' if img_url else "",
                "pub_date": datetime.now(timezone.utc),
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
                if not source:
                    logger.error(f"Source '{source_name}' not found in database.")
                    continue

                # source.url should end with /newest (e.g. .../category/124182/newest)
                url = source.url.rstrip("/")
                latest_guid = await repository.get_latest_guid(session_maker, source.id)
                is_initial = latest_guid is None

                if is_initial:
                    logger.info(f"[{source_name}] DB empty — initial fetch.")
                else:
                    logger.info(f"[{source_name}] Latest GUID: {latest_guid}")

                try:
                    html = await self._fetch(http, url)
                    page_items = self.parse_html(html)
                    logger.info(f"[{source_name}] Fetched {len(page_items)} items from {url}")
                except Exception as e:
                    logger.error(f"[{source_name}] Failed to fetch {url}: {e}")
                    continue

                # Collect only new items (stop at known GUID)
                collected = []
                for item in page_items:
                    if not is_initial and item["guid"] == latest_guid:
                        break
                    collected.append(item)

                # Page is newest-first; reverse to store oldest→newest
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
