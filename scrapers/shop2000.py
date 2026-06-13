import math
import re
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timezone

from model.orm import repository
from utils.rss_generator import generate_rss_for_source
from utils.logger import setup_logger

logger = setup_logger("Scrapers.Shop2000")

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
    "Upgrade-Insecure-Requests": "1",
}

LIMIT_INITIAL = 50   # 資料表為空時，從第 1 頁開始最多抓幾筆
LIMIT_CATCHUP = 100   # 資料表不為空時的停損點


class Shop2000Scraper:

    BASE_URL = "https://www.shop2000.com.tw"
    SCRAPER_ITEM_NAME = ["依大模型", "H-TOYS"]
    MAX_RETRIES = 3

    def parse_html(self, html_content: str, base_url: str) -> list:
        soup = BeautifulSoup(html_content, "html.parser")
        items = []

        table = soup.find("table", id="plist_tb")
        if not table:
            logger.warning("plist_tb table not found in HTML.")
            return items

        for td in table.find_all("td"):
            try:
                img_tag = td.find("img", class_="pimg")
                if not img_tag:
                    continue

                psn = img_tag.get("psn", "").strip()
                img_src = img_tag.get("src", "").strip()
                if not psn:
                    continue

                li_tag = td.find("li", psn=psn)
                title = li_tag.get_text(strip=True) if li_tag else psn

                if img_src and not img_src.startswith("http"):
                    img_src = self.BASE_URL + img_src

                link = f"{base_url}/product/p{psn}"

                items.append({
                    "title": title,
                    "link": link,
                    "description": f'<img src="{img_src}" /><br/>' if img_src else "",
                    "pub_date": datetime.now(timezone.utc),
                    "guid": link,
                })
            except Exception as e:
                logger.error(f"Error parsing td: {e}")

        return items

    async def _get_record_count(self, session: aiohttp.ClientSession, url: str) -> int:
        """GET 首頁，從 class='pt11' 內含「共 N 筆」取得總筆數，同時讓 session 存下 cookie。"""
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            html = await resp.text(encoding="utf-8", errors="replace")

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(class_="pt11"):
            m = re.search(r"共\s*([\d,]+)\s*筆", tag.get_text())
            if m:
                count = int(m.group(1).replace(",", ""))
                logger.info(f"RecordCount from page: {count}")
                return count

        raise ValueError("找不到「共 N 筆」節點，無法取得 RecordCount")

    async def _fetch_page(
        self, session: aiohttp.ClientSession, url: str, page: int, record_count: int
    ) -> str:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        post_data = {"p": str(page), "RecordCount": str(record_count)}
        extra_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": origin,
            "Referer": url,
        }

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                async with session.post(
                    url,
                    data=post_data,
                    headers=extra_headers,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status >= 400:
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status
                        )
                    return await resp.text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"Attempt {attempt}/{self.MAX_RETRIES} failed for {url} p={page}: {e}")
                if attempt == self.MAX_RETRIES:
                    raise
        return ""

    def _base_shop_url(self, full_url: str) -> str:
        parsed = urlparse(full_url)
        first_segment = parsed.path.strip("/").split("/")[0]
        if first_segment in ("home", "product", ""):
            # 子域名制：shop 在 host，path 無 slug
            return f"{parsed.scheme}://{parsed.netloc}"
        return f"{parsed.scheme}://{parsed.netloc}/{first_segment}"

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
                    logger.info("DB empty — initial fetch from page 1, limit 100 items.")
                else:
                    logger.info(f"Latest GUID: {latest_guid}")

                try:
                    record_count = await self._get_record_count(http, source.url)
                except Exception as e:
                    logger.error(f"Failed to get record count: {e}")
                    continue

                base_shop_url = self._base_shop_url(source.url)

                # 先抓第 1 頁以確定每頁筆數，再算最後一頁頁碼
                try:
                    first_html = await self._fetch_page(http, source.url, 1, record_count)
                except Exception as e:
                    logger.error(f"Failed to fetch page 1: {e}")
                    continue

                first_items = self.parse_html(first_html, base_shop_url)
                items_per_page = len(first_items) if first_items else 20
                last_page = math.ceil(record_count / items_per_page)
                logger.info(f"items_per_page={items_per_page}, last_page={last_page}")

                collected: list = []
                done = False
                limit = LIMIT_INITIAL if is_initial else LIMIT_CATCHUP

                for page in range(1, last_page + 1):
                    if done:
                        break

                    try:
                        page_items = (
                            first_items if page == 1
                            else self.parse_html(
                                await self._fetch_page(http, source.url, page, record_count),
                                base_shop_url,
                            )
                        )
                        logger.info(f"Page {page} items: {len(page_items)}")
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
                                logger.warning(
                                    f"Stop-loss: reached {limit} uncrawled items, stopping."
                                )
                            done = True
                            break

                # 從第 1 頁收集的資料是新→舊，反轉成舊→新再存入
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
