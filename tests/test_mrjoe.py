import unittest

from scrapers.mrjoe import MrJoeScraper


class MrJoeScraperTests(unittest.TestCase):
    def test_parse_newest_product_cards_title_cover_and_link(self):
        html = """
        <ul class="js_product_block">
          <li>
            <a class="pt_items_block" href="/product/detail/123">
              <span class="pt_photo" style="background-image: url(https://img.example/a.jpg);"></span>
              <h4 class="pt_title">新品 A</h4>
            </a>
          </li>
          <li>
            <a class="pt_items_block" href="https://store.mrjoe.com.tw/product/detail/456">
              <span class="pt_photo" style="background-image:url('https://img.example/b.png')"></span>
              <h4 class="pt_title">新品 B</h4>
            </a>
          </li>
          <li><a class="pt_items_block" href="/category/1"><h4 class="pt_title">分類</h4></a></li>
        </ul>
        """
        items = MrJoeScraper().parse_html(html)

        self.assertEqual([item["title"] for item in items], ["新品 A", "新品 B"])
        self.assertEqual(items[0]["link"], "https://store.mrjoe.com.tw/product/detail/123")
        self.assertIn('src="https://img.example/a.jpg"', items[0]["description"])
        self.assertEqual(items[0]["guid"], items[0]["link"])


if __name__ == "__main__":
    unittest.main()
