"""
用法：
    python run_one.py <source_name>

範例：
    python run_one.py Shop2000
"""
import asyncio
import importlib
import sys

from model.orm.database import get_async_session_maker
from model.orm import repository
from utils.logger import setup_logger

logger = setup_logger("RunOne")


async def main(source_name: str) -> None:
    session_maker = get_async_session_maker()

    source = await repository.get_source_by_name(session_maker, source_name)
    if not source:
        logger.error(f"Source '{source_name}' not found in database.")
        sys.exit(1)

    if not source.scraper_class:
        logger.error(f"Source '{source_name}' has no scraper_class set.")
        sys.exit(1)

    module_path, class_name = source.scraper_class.rsplit(".", 1)
    module = importlib.import_module(module_path)
    scraper = getattr(module, class_name)()

    logger.info(f"Running scraper: {source.scraper_class}")
    await scraper.scrape_and_store(session_maker)
    logger.info("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
