import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from xml.etree import ElementTree

from scrapers.simplypatrick import SimplyPatrickScraper, parse_feed, select_new_items
from utils import rss_generator


def feed(title, path, date, description='excerpt'):
    return (f'<rss><channel><item><title>{title}</title>'
            f'<link>https://blog.simplypatrick.com/{path}</link>'
            f'<pubDate>{date}</pubDate><description>{description}</description>'
            '</item></channel></rss>').encode()


class SimplyPatrickTests(unittest.TestCase):
    def test_parse_preserves_content_and_converts_date(self):
        items = parse_feed(feed('Article', 'posts/example/', 'Fri, 06 Nov 2026 20:25:00 -0800', '&lt;p&gt;Hello&lt;/p&gt;'))
        self.assertEqual(items[0]['description'], '<p>Hello</p>')
        self.assertEqual(items[0]['pub_date'], datetime(2026, 11, 7, 4, 25, tzinfo=timezone.utc))
        self.assertEqual(items[0]['guid'], items[0]['link'])

    def test_reject_invalid_and_empty_feed(self):
        for data in [b'<rss><channel/></rss>', feed('A', 'posts/a/', 'invalid'), b'<html/>']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_feed(data)

    def test_merge_deduplicate_and_stop_at_latest(self):
        older = parse_feed(feed('Old', 'tils/old/', 'Sat, 21 Feb 2026 00:00:00 -0800'))[0]
        newer = parse_feed(feed('New', 'posts/new/', 'Fri, 06 Nov 2026 20:25:00 -0800'))[0]
        self.assertEqual(select_new_items([[older, newer], [newer]], None), [newer, older])
        self.assertEqual(select_new_items([[older, newer]], older['guid']), [newer])
        self.assertEqual(select_new_items([[older, newer]], newer['guid']), [])


class SimplyPatrickAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_rss_keeps_newest_first(self):
        source = SimpleNamespace(id=1, name='SimplyPatrick', url='https://blog.simplypatrick.com/', rss_filename='feed.xml')
        items = [SimpleNamespace(title=title, link=f'https://blog.simplypatrick.com/posts/{title}/',
                                 guid=title, description='', pub_date=datetime(2026, 1, day, tzinfo=timezone.utc))
                 for title, day in [('new', 2), ('old', 1)]]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(rss_generator, 'RSS_OUTPUT_DIR', Path(directory)), \
             patch.object(rss_generator.repository, 'get_articles_for_source', AsyncMock(return_value=items)):
            await rss_generator.generate_rss_for_source(None, source, new_items=items)
            titles = [item.findtext('title') for item in ElementTree.parse(Path(directory) / 'feed.xml').findall('./channel/item')]
        self.assertEqual(titles, ['new', 'old'])

    async def test_fetch_failure_does_not_write(self):
        source = SimpleNamespace(id=1, is_active=True, scraper_class='scrapers.simplypatrick.SimplyPatrickScraper')
        with patch('scrapers.simplypatrick.repository.get_source_by_name', AsyncMock(return_value=source)), \
             patch.object(SimplyPatrickScraper, '_fetch', AsyncMock(side_effect=ValueError('failed'))), \
             patch('scrapers.simplypatrick.repository.save_articles', AsyncMock()) as save, \
             patch('scrapers.simplypatrick.generate_rss_for_source', AsyncMock()) as rss:
            with self.assertRaises(ValueError):
                await SimplyPatrickScraper().scrape_and_store(None)
            save.assert_not_awaited()
            rss.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
