import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main
from model.orm.repository import source_matches_scraper
from scrapers.albumcomic import AlbumComicScraper


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_launch_per_class(self):
        sources = [
            SimpleNamespace(name="first", scraper_class="scrapers.albumcomic.AlbumComicScraper"),
            SimpleNamespace(name="second", scraper_class="scrapers.albumcomic.AlbumComicScraper"),
            SimpleNamespace(name="third", scraper_class="scrapers.shop2000.Shop2000Scraper"),
            SimpleNamespace(name="unset", scraper_class=None),
        ]
        with patch.object(main.repository, "get_active_sources", AsyncMock(return_value=sources)), \
             patch.object(main, "run_scraper", AsyncMock()) as launch:
            await main.run_scrapers(object())
        self.assertCountEqual([call.args[1].name for call in launch.await_args_list], ["first", "third"])

    def test_only_active_sources_assigned_to_scraper_match(self):
        class_name = "scrapers.albumcomic.AlbumComicScraper"
        for active, assigned, expected in [
            (True, class_name, True),
            (False, class_name, False),
            (True, "scrapers.shop2000.Shop2000Scraper", False),
            (True, None, False),
        ]:
            with self.subTest(active=active, assigned=assigned):
                source = SimpleNamespace(is_active=active, scraper_class=assigned)
                self.assertEqual(source_matches_scraper(source, AlbumComicScraper), expected)


if __name__ == "__main__":
    unittest.main()
