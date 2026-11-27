import asyncio
import importlib
import os
import traceback
from model.orm.database import get_async_session_maker, Source
from model.orm import repository
from utils.logger import setup_logger
from utils.logs_api import LogsApiClient, new_ulid
from utils.rss_generator import generate_all_rss

logger = setup_logger("Main")

CONCURRENCY = 5


def configured_logs_client() -> LogsApiClient | None:
    if not os.environ.get("LOGS_API_URL") or not os.environ.get("LOGS_INGEST_TOKEN"):
        return None
    return LogsApiClient.from_env()


def emit_log(client: LogsApiClient | None, level: str, event: str, message: str, **fields) -> None:
    if client is None:
        return
    try:
        client.emit(level, event, message, **fields)
    except Exception:
        logger.exception("Unable to queue log event %s", event)


async def run_scraper(session_maker, source: Source, logs_client: LogsApiClient | None, run_id: str) -> None:
    if not source.scraper_class:
        logger.warning(f"No scraper_class set for source: '{source.name}', skipping.")
        emit_log(
            logs_client,
            "WARNING",
            "crawler.source.skipped",
            f"Source {source.name} has no scraper class",
            run_id=run_id,
            context={"source_name": source.name},
        )
        return
    try:
        module_path, class_name = source.scraper_class.rsplit(".", 1)
        module = importlib.import_module(module_path)
        scraper = getattr(module, class_name)()
        logger.info(f"Starting scraper for '{source.name}' ({source.scraper_class})")
        emit_log(
            logs_client,
            "INFO",
            "crawler.source.started",
            f"Started source {source.name}",
            run_id=run_id,
            context={"source_name": source.name, "scraper_class": source.scraper_class},
        )
        await scraper.scrape_and_store(session_maker)
    except Exception as e:
        logger.error(f"Scraper failed for '{source.name}': {e}")
        emit_log(
            logs_client,
            "ERROR",
            "crawler.source.failed",
            f"Source {source.name} failed: {e}",
            run_id=run_id,
            context={"source_name": source.name, "scraper_class": source.scraper_class},
            error_type=type(e).__name__,
            error_message=str(e),
            stack_trace=traceback.format_exc(),
        )


async def run_scrapers(session_maker, logs_client: LogsApiClient | None, run_id: str) -> None:
    active_sources = await repository.get_active_sources(session_maker)
    if not active_sources:
        logger.warning("No active sources found.")
        emit_log(logs_client, "WARNING", "crawler.sources.empty", "No active sources found.", run_id=run_id)
        return

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def run_with_semaphore(source):
        async with semaphore:
            await run_scraper(session_maker, source, logs_client, run_id)

    # Each scraper processes all of its configured sources, so launch it once.
    sources_by_class = {}
    for source in active_sources:
        if not source.scraper_class:
            logger.warning(f"No scraper_class set for source: '{source.name}', skipping.")
            continue
        sources_by_class.setdefault(source.scraper_class, source)

    await asyncio.gather(*[run_with_semaphore(s) for s in sources_by_class.values()])


async def main():
    logs_client = configured_logs_client()
    run_id = new_ulid()
    logger.info("=== Application Started ===")
    emit_log(logs_client, "INFO", "crawler.run.started", "Crawler run started", run_id=run_id)
    session_maker = get_async_session_maker()
    try:
        logger.info(f"Running scrapers (max {CONCURRENCY} concurrent)...")
        await run_scrapers(session_maker, logs_client, run_id)

        logger.info("Generating RSS feeds...")
        await generate_all_rss(session_maker)

        logger.info("=== Process completed successfully ===")
        emit_log(logs_client, "INFO", "crawler.run.completed", "Crawler run completed", run_id=run_id)
    except Exception as exc:
        emit_log(
            logs_client,
            "CRITICAL",
            "crawler.run.failed",
            f"Crawler run failed: {exc}",
            run_id=run_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
            stack_trace=traceback.format_exc(),
        )
        raise
    finally:
        if logs_client is not None:
            logs_client.flush()


if __name__ == "__main__":
    asyncio.run(main())
