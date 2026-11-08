from bs4 import BeautifulSoup
from datetime import datetime, timezone
from playwright.async_api import async_playwright

from model.orm import repository
from utils.rss_generator import generate_rss_for_source
from utils.logger import setup_logger

logger = setup_logger("Scrapers.HobbySearch")

class HobbySearchScraper:

    def __init__(self):

        self.BASE_URL = "https://www.1999.co.jp"
        self.SCRAPER_ITEM_NAME = ["HobbySearch画集", "HobbySearch写真集"]

    def parse_html(self, html_content: str) -> list:
        """Parse the 1999.co.jp HTML content and extract article information."""
        soup = BeautifulSoup(html_content, 'html.parser')
        items = []

        for row in soup.select("div.c-product-list__item"):
            try:
                a_tag = row.select_one("a.c-card__info-links")
                if not a_tag:
                    continue

                link = a_tag.get('href', '')
                if link and not link.startswith("http"):
                    link = self.BASE_URL + link

                title_tag = row.select_one("div.c-card__title")
                title = title_tag.text.strip() if title_tag else a_tag.text.strip()

                img_tag = row.select_one("a.c-card__th-links img")
                img_src = ""
                if img_tag and img_tag.get('src'):
                    img_src = img_tag['src']
                    if not img_src.startswith("http"):
                        img_src = self.BASE_URL + img_src

                items.append({
                    "title": title,
                    "link": link,
                    "description": f'<img src="{img_src}" /><br/>' if img_src else "",
                    "pub_date": datetime.now(timezone.utc),
                    "guid": link,
                })
            except Exception as e:
                logger.error(f"Error parsing item: {e}")

        return items

    TIMEOUT_MS = 60_000
    MAX_RETRIES = 3

    async def _fetch_with_retry(self, page, url: str) -> str:
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=self.TIMEOUT_MS)
                if response and not response.ok:
                    raise Exception(f"HTTP {response.status}")

                # 選擇「新しい順」排序後等待頁面更新
                await page.wait_for_selector("#sortSelect", timeout=self.TIMEOUT_MS)
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=self.TIMEOUT_MS):
                    await page.select_option("#sortSelect", label="新しい順")

                return await page.content()
            except Exception as e:
                logger.warning(f"Attempt {attempt}/{self.MAX_RETRIES} failed for {url}: {e}")
                if attempt == self.MAX_RETRIES:
                    raise
        return ""

    async def scrape_and_store(self, session_maker):
        async with async_playwright() as pw:
            browser = await pw.firefox.launch(headless=True)
            page = await browser.new_page()

            for source_name in self.SCRAPER_ITEM_NAME:
                source = await repository.get_source_by_name(session_maker, source_name)
                if source and not repository.source_matches_scraper(source, type(self)):
                    continue
                if not source:
                    logger.error(f"Source '{source_name}' not found in database.")
                    continue

                latest_guid = await repository.get_latest_guid(session_maker, source.id)
                if latest_guid:
                    logger.info(f"Found latest GUID: {latest_guid}")
                else:
                    logger.info("No previous articles found. Initial fetch.")

                logger.info(f"Fetching {source.url}")
                try:
                    html = await self._fetch_with_retry(page, source.url)
                except Exception as e:
                    logger.error(f"Failed to fetch '{source_name}' after {self.MAX_RETRIES} attempts: {e}")
                    continue

                items = self.parse_html(html)
                logger.info(f"Found {len(items)} items on page.")
                items.reverse()

                new_count = await repository.save_articles(session_maker, source.id, items, latest_guid)
                logger.info(f"Saved {new_count} new items.")

                await generate_rss_for_source(session_maker, source, new_items=items if new_count > 0 else [])

            await browser.close()
