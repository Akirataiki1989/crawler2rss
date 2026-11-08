import asyncio
import importlib
from model.orm.database import get_async_session_maker, Source
from model.orm import repository
from utils.logger import setup_logger
from utils.rss_generator import generate_all_rss

logger = setup_logger("Main")

CONCURRENCY = 5


async def run_scraper(session_maker, source: Source) -> None:
    if not source.scraper_class:
        logger.warning(f"No scraper_class set for source: '{source.name}', skipping.")
        return
    try:
        module_path, class_name = source.scraper_class.rsplit(".", 1)
        module = importlib.import_module(module_path)
        scraper = getattr(module, class_name)()
        logger.info(f"Starting scraper for '{source.name}' ({source.scraper_class})")
        await scraper.scrape_and_store(session_maker)
    except Exception as e:
        logger.error(f"Scraper failed for '{source.name}': {e}")


async def run_scrapers(session_maker) -> None:
    active_sources = await repository.get_active_sources(session_maker)
    if not active_sources:
        logger.warning("No active sources found.")
        return

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def run_with_semaphore(source):
        async with semaphore:
            await run_scraper(session_maker, source)

    # Each scraper processes all of its configured sources, so launch it once.
    sources_by_class = {}
    for source in active_sources:
        if not source.scraper_class:
            logger.warning(f"No scraper_class set for source: '{source.name}', skipping.")
            continue
        sources_by_class.setdefault(source.scraper_class, source)

    await asyncio.gather(*[run_with_semaphore(s) for s in sources_by_class.values()])


async def main():
    logger.info("=== Application Started ===")
    session_maker = get_async_session_maker()

    logger.info(f"Running scrapers (max {CONCURRENCY} concurrent)...")
    await run_scrapers(session_maker)

    logger.info("Generating RSS feeds...")
    await generate_all_rss(session_maker)

    logger.info("=== Process completed successfully ===")


if __name__ == "__main__":
    asyncio.run(main())
