# Add RSS Scraper

You are helping add a new website to the RSS scraper project at `\\192.168.0.104\web\codeserver\scraper`.

The user provided this starting URL: $ARGUMENTS

---

## Your workflow

### Step 0 — If no URL given, ask first
If `$ARGUMENTS` is empty, ask: "請提供要爬取的網站網址。"

---

### Step 1 — Fetch and analyze the URL
Use Gemini CLI to fetch the provided URL and analyze the HTML structure:

```bash
gemini -p "fetch <URL> and extract: 1) product/article list HTML structure with CSS classes, 2) pagination mechanism (URL pattern for page 2), 3) title/link/image selectors, show actual HTML snippets" --yolo -o text --skip-trust
```

Summarize what you found in 3–4 bullet points.

---

### Step 2 — Ask clarifying questions (one at a time, wait for answer)

**Q1:** 這個網站需要爬取幾個分類/子頁面？請直接列出每個分類的完整 URL。
（例如：https://example.com/category/123，一行一個）

**Q2:** 請為每個分類取一個**識別名稱**（中英文皆可），這會是資料庫 source name，也會是 RSS feed 的 `<title>`。
（例如：`遊戲相關`、`albumcomic_games`）

**Q3:** 這個網站需要用 **Playwright（動態渲染）** 還是 **aiohttp（靜態請求）**？
- 如果商品列表需要 JavaScript 執行才能顯示 → Playwright
- 如果直接 HTTP GET 就能看到商品 HTML → aiohttp（推薦，更簡單）

---

### Step 3 — Generate scraper code

Based on the analysis and answers, generate a new scraper file at `scrapers/<sitename>.py`.

**Follow this exact pattern** (modeled after `scrapers/albumcomic.py`):

```python
import re
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timezone

from model.orm import repository
from utils.rss_generator import generate_rss_for_source
from utils.logger import setup_logger

logger = setup_logger("Scrapers.<ClassName>")

HEADERS = { ... }  # standard browser headers
MAX_RETRIES = 3


class <ClassName>Scraper:

    SCRAPER_ITEM_NAME = [
        "<source_name_1>",
        "<source_name_2>",
        # ...
    ]

    def parse_html(self, html_content: str) -> list:
        # Parse product list using BeautifulSoup
        # Return list of dicts: title, link, description (img html), pub_date, guid

    async def _fetch(self, session, url: str) -> str:
        # GET the URL with retries (no pagination — single page fetch)

    async def scrape_and_store(self, session_maker):
        async with aiohttp.ClientSession(headers=HEADERS) as http:
            for source_name in self.SCRAPER_ITEM_NAME:
                source = await repository.get_source_by_name(session_maker, source_name)
                if not source:
                    logger.error(f"Source '{source_name}' not found in database.")
                    continue
                # Fetch source.url, collect only new items (stop at latest_guid)
                # reverse() then save_articles + generate_rss_for_source
```

---

### Step 4 — Generate SQL

Generate an `init_<sitename>.sql` file.
Note: `rss_feed.sources.name` has no UNIQUE constraint, use `WHERE NOT EXISTS` pattern:

```sql
INSERT INTO rss_feed.sources (name, url, rss_filename, is_active, scraper_class)
SELECT v.name, v.url, v.rss_filename, v.is_active, v.scraper_class
FROM (VALUES
  ('<source_name_1>', '<url_1>', '<source_name_1>.xml', TRUE, 'scrapers.<module>.<ClassName>Scraper'),
  ('<source_name_2>', '<url_2>', '<source_name_2>.xml', TRUE, 'scrapers.<module>.<ClassName>Scraper')
) AS v(name, url, rss_filename, is_active, scraper_class)
WHERE NOT EXISTS (
  SELECT 1 FROM rss_feed.sources WHERE name = v.name
);
```

---

### Step 5 — Confirm and write files

Show the user:
1. The generated `scrapers/<sitename>.py` content
2. The generated `init_<sitename>.sql` content

Ask: "確認後我就寫入檔案。需要修改任何地方嗎？"

After confirmation, write both files using the Write tool.

---

### Step 6 — Remind about next steps

After writing:
```
✓ 已建立 scrapers/<sitename>.py
✓ 已建立 init_<sitename>.sql

下一步：
1. 執行 SQL 將 sources 加入資料庫：
   psql -U <user> -d <db> -f init_<sitename>.sql

2. 測試單一爬蟲：
   python run_one.py <source_name_1>

3. 確認無誤後執行全部：
   python main.py
```
