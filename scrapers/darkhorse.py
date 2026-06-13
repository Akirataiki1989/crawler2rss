import re
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timezone

from model.orm import repository
from utils.rss_generator import generate_rss_for_source
from utils.logger import setup_logger

logger = setup_logger("Scrapers.DarkHorse")

BASE_URL = "https://www.darkhorse.com"
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
LIMIT_INITIAL = 50   # 資料表為空時，從第 1 頁開始最多抓幾筆
LIMIT_CATCHUP = 100  # 資料表不為空時的停損點


class DarkHorseScraper:

    SCRAPER_ITEM_NAME = ["DarkHorse_ArtBook"]

    def _build_url(self, base_url: str, page: int) -> str:
        end_year = datetime.now().year + 1
        url = f"{base_url}&end={end_year}"
        if page > 1:
            url += f"&p={page}"
        return url

    def parse_html(self, html_content: str) -> list:
        soup = BeautifulSoup(html_content, "html.parser")
        items = []
        seen = set()

        for div in soup.find_all("div", class_="product-result"):
            img_div = div.find("div", class_="product-img")
            if not img_div:
                continue

            a_tag = img_div.find("a")
            img_tag = img_div.find("img")
            if not a_tag:
                continue

            href = a_tag.get("href", "").strip()
            if not href:
                continue
            link = BASE_URL + href if not href.startswith("http") else href
            if link in seen:
                continue
            seen.add(link)

            # Title from standalone div>a (not inside product-img or product-price)
            title = ""
            for child in div.find_all("div", recursive=False):
                classes = child.get("class", [])
                if "product-img" not in classes and "product-price" not in classes:
                    a = child.find("a")
                    if a:
                        title = a.get_text(strip=True)
                        break
            if not title and img_tag:
                alt = img_tag.get("alt", "")
                title = re.sub(r"^Cover image for\s+", "", alt).strip()
            if not title:
                continue

            img_url = img_tag.get("src", "") if img_tag else ""

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

                    # No new items on this page = results are wrapping/repeating
                    if not done and new_on_page == 0:
                        break

                    page += 1

                # Results are newest-first; reverse to store oldest→newest
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
