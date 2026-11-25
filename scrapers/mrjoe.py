"""Scrape MR.JOE HOBBY's newest product listings."""

import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import aiohttp
from bs4 import BeautifulSoup

from model.orm import repository
from utils.logger import setup_logger
from utils.rss_generator import generate_rss_for_source

logger = setup_logger("Scrapers.MRJoe")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
}

LIMIT_INITIAL = 50
LIMIT_CATCHUP = 50
MAX_RETRIES = 3


def _image_from_style(style: str) -> str:
    match = re.search(r"background-image\s*:\s*url\((['\"]?)(.*?)\1\)", style, re.IGNORECASE)
    return match.group(2).strip() if match else ""


class MrJoeScraper:
    SCRAPER_ITEM_NAME = ["MR.JOE_最新上架"]

    def parse_html(self, html_content: str) -> list:
        soup = BeautifulSoup(html_content, "html.parser")
        items = []
        seen = set()

        for product in soup.select(".js_product_block > li"):
            anchor = product.select_one("a.pt_items_block[href]")
            title_tag = product.select_one("h4.pt_title")
            photo = product.select_one(".pt_photo")
            if not anchor or not title_tag or not photo:
                continue

            link = urljoin("https://store.mrjoe.com.tw/", anchor["href"].strip())
            parsed = urlparse(link)
            if parsed.netloc != "store.mrjoe.com.tw" or not parsed.path.startswith("/product/detail/"):
                continue

            title = title_tag.get_text(" ", strip=True)
            image_url = _image_from_style(photo.get("style", ""))
            image_url = urljoin(link, image_url)
            if not title or len(title) > 255 or not image_url or link in seen:
                continue

            seen.add(link)
            items.append({
                "title": title,
                "link": link,
                "description": f'<img src="{image_url}" /><br/>',
                "pub_date": datetime.now(timezone.utc),
                "guid": link,
            })

        return items

    def _page_url(self, base_url: str, page: int) -> str:
        if page == 1:
            return base_url
        parsed = urlparse(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["page"] = str(page)
        return urlunparse(parsed._replace(query=urlencode(query)))

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    response.raise_for_status()
                    return await response.text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.warning("Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, url, exc)
                if attempt == MAX_RETRIES:
                    raise
        return ""

    async def scrape_and_store(self, session_maker):
        source = await repository.get_source_by_name(session_maker, self.SCRAPER_ITEM_NAME[0])
        if not source:
            logger.error("Source '%s' not found in database.", self.SCRAPER_ITEM_NAME[0])
            return
        if not repository.source_matches_scraper(source, type(self)):
            return

        latest_guid = await repository.get_latest_guid(session_maker, source.id)
        is_initial = latest_guid is None
        limit = LIMIT_INITIAL if is_initial else LIMIT_CATCHUP
        logger.info("[%s] %s; limit=%d", source.name, "initial fetch" if is_initial else f"latest GUID: {latest_guid}", limit)

        collected = []
        seen = set()
        done = False
        async with aiohttp.ClientSession(headers=HEADERS) as http:
            for page in range(1, 146):
                if done:
                    break
                url = self._page_url(source.url, page)
                try:
                    page_items = self.parse_html(await self._fetch(http, url))
                except Exception as exc:
                    logger.error("[%s] Failed to fetch page %d: %s", source.name, page, exc)
                    break
                logger.info("[%s] Page %d: %d items", source.name, page, len(page_items))
                if not page_items:
                    break

                for item in page_items:
                    if not is_initial and item["guid"] == latest_guid:
                        done = True
                        break
                    if item["guid"] in seen:
                        continue
                    seen.add(item["guid"])
                    collected.append(item)
                    if len(collected) >= limit:
                        done = True
                        break

                if is_initial:
                    break

        collected.reverse()
        logger.info("[%s] Collected %d new items.", source.name, len(collected))
        if not collected:
            await generate_rss_for_source(session_maker, source, new_items=[])
            return

        saved = await repository.save_articles(session_maker, source.id, collected, latest_guid=None)
        logger.info("[%s] Saved %d new items.", source.name, saved)
        await generate_rss_for_source(session_maker, source, new_items=collected)
