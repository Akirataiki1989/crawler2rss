import re
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timezone

from model.orm import repository
from utils.rss_generator import generate_rss_for_source
from utils.logger import setup_logger

logger = setup_logger("Scrapers.PopCostume")

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}

LIMIT_INITIAL = 200
LIMIT_CATCHUP = 100


class PopCostumeScraper:

    BASE_URL = "https://www.popcostume.com"
    SCRAPER_ITEM_NAME = ["PopCostume"]
    MAX_RETRIES = 3

    def parse_html(self, html_content: str) -> list:
        soup = BeautifulSoup(html_content, "html.parser")
        seen = set()
        items = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "").strip()

            # 只處理產品頁連結 (/{slug}/1.html)，排除分類分頁連結
            if not href.endswith("/1.html") or href.startswith("/products/"):
                continue

            img_tag = a_tag.find("img")
            if not img_tag:
                continue

            link = self.BASE_URL + href
            if link in seen:
                continue
            seen.add(link)

            img_src = img_tag.get("src", "").strip()
            if img_src.startswith("//"):
                img_src = "https:" + img_src
            elif img_src and not img_src.startswith("http"):
                img_src = self.BASE_URL + img_src

            title = img_tag.get("alt", "").strip() or a_tag.get_text(strip=True)
            if not title:
                continue

            items.append({
                "title": title,
                "link": link,
                "description": f'<img src="{img_src}" /><br/>' if img_src else "",
                "pub_date": datetime.now(timezone.utc),
                "guid": link,
            })

        return items

    def _get_last_page(self, soup: BeautifulSoup) -> int:
        max_page = 1
        for a_tag in soup.find_all("a", href=re.compile(r"^/products/\d+\.html$")):
            m = re.search(r"/products/(\d+)\.html", a_tag["href"])
            if m:
                max_page = max(max_page, int(m.group(1)))
        return max_page

    async def _fetch_page(self, session: aiohttp.ClientSession, page: int) -> str:
        url = f"{self.BASE_URL}/products/{page}.html"
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    return await resp.text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"Attempt {attempt}/{self.MAX_RETRIES} failed for {url}: {e}")
                if attempt == self.MAX_RETRIES:
                    raise
        return ""

    async def scrape_and_store(self, session_maker):
        async with aiohttp.ClientSession(headers=HEADERS) as http:
            for source_name in self.SCRAPER_ITEM_NAME:
                source = await repository.get_source_by_name(session_maker, source_name)
                if not source:
                    logger.error(f"Source '{source_name}' not found in database.")
                    continue

                latest_guid = await repository.get_latest_guid(session_maker, source.id)
                is_initial = latest_guid is None

                if is_initial:
                    logger.info("DB empty — initial fetch from page 1.")
                else:
                    logger.info(f"Latest GUID: {latest_guid}")

                try:
                    first_html = await self._fetch_page(http, 1)
                except Exception as e:
                    logger.error(f"Failed to fetch page 1: {e}")
                    continue

                first_soup = BeautifulSoup(first_html, "html.parser")
                last_page = self._get_last_page(first_soup)
                logger.info(f"Total pages: {last_page}")

                collected: list = []
                done = False
                limit = LIMIT_INITIAL if is_initial else LIMIT_CATCHUP

                for page in range(1, last_page + 1):
                    if done:
                        break

                    try:
                        page_items = (
                            self.parse_html(first_html)
                            if page == 1
                            else self.parse_html(await self._fetch_page(http, page))
                        )
                        logger.info(f"Page {page}: {len(page_items)} items")
                    except Exception as e:
                        logger.error(f"Failed to fetch page {page}: {e}")
                        break

                    for item in page_items:
                        if not is_initial and item["guid"] == latest_guid:
                            done = True
                            break
                        collected.append(item)
                        if len(collected) >= limit:
                            if not is_initial:
                                logger.warning(f"Stop-loss: reached {limit} items, stopping.")
                            done = True
                            break

                # 頁面從新到舊排列，反轉後以舊→新寫入資料庫
                collected.reverse()
                logger.info(f"Collected {len(collected)} new items.")

                if not collected:
                    logger.info("No new items.")
                    await generate_rss_for_source(session_maker, source, new_items=[])
                    continue

                new_count = await repository.save_articles(
                    session_maker, source.id, collected, latest_guid=None
                )
                logger.info(f"Saved {new_count} new items.")

                await generate_rss_for_source(session_maker, source, new_items=collected)
