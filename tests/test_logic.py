import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mailer import format_dt, format_price, render_html, render_plain
from scraper import Listing, _next_page, parse_time, should_stop
from store import Store


def listing(**overrides) -> Listing:
    posted = datetime(2026, 10, 16, 11, 11, tzinfo=timezone.utc)
    data = dict(
        code=100,
        title="Stan",
        price=165000,
        price_m2=3511,
        area=47,
        rooms=1,
        settlement="Ferenščica",
        city="Peščenica - Žitnjak",
        county="Grad Zagreb",
        posted_at=posted,
        renewed_at=None,
        is_promoted=False,
        legal_entity=False,
        url="https://www.index.hr/oglasi/nekretnine/prodaja-stanova/oglas/stan/100",
    )
    data.update(overrides)
    return Listing(**data)


class StopTests(unittest.TestCase):
    def test_promoted_page_does_not_stop(self):
        old = listing(code=1, is_promoted=True, posted_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        last = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.assertFalse(should_stop([old], {1}, last))

    def test_old_known_organic_page_stops(self):
        item = listing(code=1, posted_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
        last = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.assertTrue(should_stop([item], {1}, last))

    def test_unknown_code_keeps_reading(self):
        item = listing(code=2, posted_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
        last = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.assertFalse(should_stop([item], {1}, last))

    def test_renewal_newer_than_last_check_keeps_reading(self):
        item = listing(
            code=1,
            posted_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            renewed_at=datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc),
        )
        last = datetime(2026, 10, 22, 7, 0, tzinfo=timezone.utc)
        self.assertFalse(should_stop([item], {1}, last))

    def test_equal_activity_does_not_stop(self):
        moment = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
        item = listing(code=1, posted_at=moment)
        self.assertFalse(should_stop([item], {1}, moment))

    def test_empty_page_stops(self):
        self.assertTrue(should_stop([], set(), datetime.now(timezone.utc)))

    def test_last_page_marker_does_not_continue(self):
        self.assertIsNone(_next_page(323, {"nextPage": -1}, 3))
        self.assertEqual(_next_page(4, {"nextPage": 5}, 24), 5)


class FormatTests(unittest.TestCase):
    def test_zagreb_local_time(self):
        moment = datetime(2026, 10, 16, 11, 11, tzinfo=timezone.utc)
        self.assertEqual(format_dt(moment), "16.10.2026. 13:11")
        self.assertEqual(format_dt(None), "—")

    def test_price(self):
        self.assertEqual(format_price(355000), "355.000 €")
        self.assertEqual(format_price(None), "—")

    def test_email_shows_title_price_location_seller_and_link(self):
        item = listing(legal_entity=True, price=165000, price_m2=3511)
        checked = datetime(2026, 10, 22, 7, 15, tzinfo=timezone.utc)
        html_body = render_html([item], checked)
        plain = render_plain([item], checked)
        for body in (html_body, plain):
            self.assertIn(item.title, body)
            self.assertIn("165.000 €", body)
            self.assertIn("3.511 €/m²", body)
            self.assertIn("Ferenščica, Peščenica - Žitnjak, Grad Zagreb", body)
            self.assertIn("(Agencija)", body)
            self.assertIn(item.url, body)
        self.assertIn("Otvori oglas", html_body)
        self.assertIn("border:1px solid #d6d3d1", html_body)
        self.assertNotIn("#f4f0e8", html_body)
        self.assertNotIn("Objavljen", html_body)
        self.assertNotIn("testni oglas", html_body.lower())

    def test_test_note_is_included_when_requested(self):
        item = listing()
        checked = datetime(2026, 10, 22, 7, 15, tzinfo=timezone.utc)
        note = "Ovo je testni oglas."
        self.assertIn(note, render_html([item], checked, note))
        self.assertIn(note, render_plain([item], checked, note))

    def test_more_than_three_uses_compact_rows(self):
        items = [listing(code=index, title=f"Stan {index}") for index in range(1, 5)]
        checked = datetime(2026, 10, 22, 7, 15, tzinfo=timezone.utc)
        compact = render_html(items, checked)
        cards = render_html(items[:3], checked)
        self.assertIn("padding:10px 14px 2px", compact)
        self.assertIn("padding:18px 20px 8px", cards)
        self.assertNotIn("padding:10px 14px 2px", cards)


class ParseTests(unittest.TestCase):
    def test_api_payload(self):
        parsed = Listing.from_api(
            {
                "code": 7638804,
                "title": "  Stan\ncentar ",
                "price": 200000,
                "priceM2": 4000,
                "postedTime": "2026-10-16T11:11:00.000Z",
                "renewalTime": "2026-10-19T17:07:00.000Z",
                "isPromoted": False,
                "legalEntity": False,
                "smartLink": "stan-centar",
                "settlementName": "Centar",
                "cityName": "Donji Grad",
                "countyName": "Grad Zagreb",
                "summary": {"area": 50, "numberOfRooms": 2},
            }
        )
        self.assertEqual(parsed.code, 7638804)
        self.assertEqual(parsed.title, "Stan centar")
        self.assertEqual(parsed.rooms, 2)
        self.assertEqual(parsed.activity, parse_time("2026-10-19T17:07:00.000Z"))
        self.assertTrue(parsed.url.endswith("/oglas/stan-centar/7638804"))


class StoreTests(unittest.TestCase):
    def test_baseline_does_not_count_as_mail_and_mark_sent_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(str(Path(folder) / "seen.sqlite"))
            old = listing(code=1)
            new = listing(code=2, posted_at=datetime.now(timezone.utc))
            self.assertFalse(store.baseline_complete())
            store.save_baseline([old])
            self.assertTrue(store.baseline_complete())
            self.assertEqual(store.codes(), {1})
            row = store.conn.execute("SELECT sent_at FROM seen WHERE code = 1").fetchone()
            self.assertIsNone(row["sent_at"])
            fresh = [item for item in [old, new] if item.code not in store.codes()]
            self.assertEqual([item.code for item in fresh], [2])
            sent_at = datetime.now(timezone.utc)
            store.mark_sent(fresh, sent_at)
            self.assertEqual(store.codes(), {1, 2})
            mailed = store.conn.execute("SELECT sent_at FROM seen WHERE code = 2").fetchone()
            self.assertIsNotNone(mailed["sent_at"])
            moment = datetime.now(timezone.utc) - timedelta(minutes=5)
            store.set_last_success(moment)
            self.assertEqual(store.last_success(), moment)
            self.assertFalse(store.test_sent())
            store.mark_test_sent()
            self.assertTrue(store.test_sent())
            store.close()


if __name__ == "__main__":
    unittest.main()
