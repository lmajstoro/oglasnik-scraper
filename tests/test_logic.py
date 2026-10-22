import logging
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mailer import format_dt, format_price, render_html, render_plain
from main import _ZagrebFormatter, _check_lines, _period_label
from plavi import classify_plavi, extract_ads, listing_from_ad
from scraper import Listing, _next_page, is_renewal, parse_time, should_stop
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

    def test_renewal_is_not_a_new_listing(self):
        published = listing(code=7646760, renewed_at=None)
        bumped = listing(
            code=7462995,
            posted_at=datetime(2026, 10, 22, 9, 47, tzinfo=timezone.utc),
            renewed_at=datetime(2026, 10, 22, 9, 47, tzinfo=timezone.utc),
        )
        older = listing(
            code=7357884,
            posted_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
            renewed_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
        )
        self.assertFalse(is_renewal(published))
        self.assertTrue(is_renewal(bumped))
        self.assertTrue(is_renewal(older))

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

    def test_log_time_is_zagreb(self):
        record = logging.LogRecord("oglasnik", logging.INFO, __file__, 1, "poruka", (), None)
        record.created = datetime(2026, 10, 22, 10, 1, 45, tzinfo=timezone.utc).timestamp()
        record.msecs = 45
        formatted = _ZagrebFormatter("%(asctime)s %(message)s").format(record)
        self.assertTrue(formatted.startswith("2026-10-22 12:01:45,045 "))

    def test_check_report_permutations(self):
        link = "https://www.index.hr/oglasi/nekretnine/prodaja-stanova/oglas/stan/7462995"
        other = "https://www.index.hr/oglasi/nekretnine/prodaja-stanova/oglas/stan/7462940"
        self.assertEqual(_check_lines(0, []), ["Nema novih oglasa."])
        self.assertEqual(
            _check_lines(0, [link, other]),
            [
                "Nema novih oglasa.",
                "Pronađeno 2, nisu poslani jer su ponovno objavljeni:",
                link,
                other,
            ],
        )
        self.assertEqual(_check_lines(1, []), ["Novih oglasa: 1. Mail je poslan."])
        self.assertEqual(
            _check_lines(3, [link]),
            [
                "Novih oglasa: 3. Mail je poslan.",
                "Pronađen 1, nije poslan jer je ponovno objavljen:",
                link,
            ],
        )

    def test_period_is_zagreb_clock(self):
        previous = datetime(2026, 10, 22, 11, 13, tzinfo=timezone.utc)
        started = datetime(2026, 10, 22, 11, 18, tzinfo=timezone.utc)
        self.assertEqual(_period_label(previous, started), "13:13 - 13:18")

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
        self.assertIn("Novi oglasi – Index nekretnine", html_body)
        self.assertIn("Naslov", html_body)
        self.assertIn("#e30613", html_body)
        self.assertNotIn("#f4f0e8", html_body)
        self.assertNotIn(">Objavljen", html_body)
        self.assertNotIn("testni oglas", html_body.lower())

    def test_test_note_is_included_when_requested(self):
        item = listing()
        checked = datetime(2026, 10, 22, 7, 15, tzinfo=timezone.utc)
        note = "Ovo je testni oglas."
        self.assertIn(note, render_html([item], checked, note))
        self.assertIn(note, render_plain([item], checked, note))

    def test_any_count_uses_the_same_table(self):
        items = [listing(code=index, title=f"Stan {index}") for index in range(1, 5)]
        checked = datetime(2026, 10, 22, 7, 15, tzinfo=timezone.utc)
        body = render_html(items, checked)
        self.assertIn("Pronađeno 4 novih oglasa.", body)
        self.assertEqual(body.count("Otvori oglas"), 4)
        self.assertIn("09:15", body)

    def test_plavi_mail_uses_blue_theme(self):
        checked = datetime(2026, 10, 22, 7, 15, tzinfo=timezone.utc)
        body = render_html([listing()], checked, source="plavi")
        plain = render_plain([listing()], checked, source="plavi")
        self.assertIn("#0E6EB8", body)
        self.assertIn("Novi oglasi – Plavi oglasnik", body)
        self.assertIn("oglasnik.hr – automatski izvještaj", body)
        self.assertNotIn("#e30613", body)
        self.assertIn("Novi oglasi – Plavi oglasnik", plain)


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
            renewed = listing(
                code=7462995,
                posted_at=datetime(2026, 10, 22, 9, 47, tzinfo=timezone.utc),
                renewed_at=datetime(2026, 10, 22, 9, 47, tzinfo=timezone.utc),
            )
            self.assertEqual(store.remember([renewed, old]), 1)
            remembered = store.conn.execute(
                "SELECT sent_at FROM seen WHERE code = 7462995"
            ).fetchone()
            self.assertIsNone(remembered["sent_at"])
            store.close()

    def test_same_code_can_exist_for_both_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "seen.sqlite")
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE seen (
                    code INTEGER PRIMARY KEY,
                    posted_at TEXT NOT NULL,
                    renewed_at TEXT,
                    sent_at TEXT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL
                );
                CREATE TABLE meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT INTO seen VALUES (
                    5, '2026-10-22T10:00:00+00:00', NULL, NULL, 'Stan', 'https://index.example/5'
                );
                INSERT INTO meta VALUES ('baseline_complete', '1');
                INSERT INTO meta VALUES ('last_success_at', '2026-10-22T10:00:00+00:00');
                """
            )
            conn.close()
            index = Store(path, "index")
            plavi = Store(path, "plavi")
            self.assertTrue(index.baseline_complete())
            self.assertEqual(index.last_success(), datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
            self.assertEqual(index.codes(), {5})
            self.assertFalse(plavi.baseline_complete())
            self.assertEqual(plavi.codes(), set())
            plavi.remember([listing(code=5, title="Plavi stan")])
            self.assertEqual(plavi.codes(), {5})
            self.assertEqual(index.count(), 1)
            self.assertEqual(plavi.count(), 1)
            published, renewed = classify_plavi(
                [listing(code=9), listing(code=4)],
                plavi.max_code(),
            )
            self.assertEqual([item.code for item in published], [9])
            self.assertEqual([item.code for item in renewed], [4])
            index.close()
            plavi.close()


class PlaviParseTests(unittest.TestCase):
    def test_extract_ad_keeps_braces_inside_title(self):
        raw = (
            '{"ad":{"id":5,"title":"Stan {centar}","sort_date":1792653674,'
            '"price":{"value":100000},"location":[{"name":"Hrvatska"},'
            '{"name":"Grad Zagreb"},{"name":"Novi Zagreb"},{"name":"Blato"}],'
            '"user":{"type":"trgovina"},"searchable":{"re_total_area":50}}}'
        )
        ads = extract_ads(raw.replace('"', '\\"'))
        self.assertEqual(len(ads), 1)
        item = listing_from_ad(ads[0], "https://oglasnik.hr/stanovi-prodaja/stan-oglas-5")
        self.assertEqual(item.title, "Stan {centar}")
        self.assertEqual(item.code, 5)
        self.assertTrue(item.legal_entity)
        self.assertEqual(item.county, "Grad Zagreb")
        self.assertEqual(item.city, "Novi Zagreb")
        self.assertEqual(item.settlement, "Blato")
        self.assertEqual(item.price, 100000)
        self.assertEqual(item.area, 50)
        self.assertEqual(item.price_m2, 2000)


if __name__ == "__main__":
    unittest.main()
