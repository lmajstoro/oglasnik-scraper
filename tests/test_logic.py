import logging
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mailer import Offer, format_dt, format_price, render_html, render_plain, report_subject
from main import (
    _ZagrebFormatter,
    _check_lines,
    _lower_prices,
    _period_label,
    _without_agencies,
    in_night_pause,
    next_check_at,
)
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
        self.assertEqual(
            _check_lines(0, [], [link]),
            [
                "Nema novih oglasa.",
                "Pronađen 1, nije poslan jer je agencijski:",
                link,
            ],
        )
        self.assertEqual(
            _check_lines(1, [link], [other]),
            [
                "Novih oglasa: 1. Mail je poslan.",
                "Pronađen 1, nije poslan jer je ponovno objavljen:",
                link,
                "Pronađen 1, nije poslan jer je agencijski:",
                other,
            ],
        )
        self.assertEqual(
            _check_lines(0, [], [link, other]),
            [
                "Nema novih oglasa.",
                "Pronađeno 2, nisu poslani jer su agencijski:",
                link,
                other,
            ],
        )
        self.assertEqual(
            _check_lines(0, [], [], 1),
            [
                "Nema novih oglasa.",
                "Obnovljen s nižom cijenom: 1. Mail je poslan.",
            ],
        )
        self.assertEqual(
            _check_lines(2, [link], [other], 2),
            [
                "Novih oglasa: 2. Mail je poslan.",
                "Obnovljenih s nižom cijenom: 2. Mail je poslan.",
                "Pronađen 1, nije poslan jer je ponovno objavljen:",
                link,
                "Pronađen 1, nije poslan jer je agencijski:",
                other,
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

    def test_mail_sorts_by_price_per_square_meter(self):
        checked = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)
        cheap = listing(code=3, title="Jeftini", price_m2=1800)
        mid = listing(code=1, title="Srednji", price_m2=3200)
        dear = listing(code=2, title="Skupi", price_m2=5100)
        unknown = listing(code=4, title="Bez kvadrata", price_m2=None)
        for source in ("index", "plavi"):
            html_body = render_html([dear, unknown, cheap, mid], checked, source=source)
            plain = render_plain([dear, unknown, cheap, mid], checked, source=source)
            for body in (html_body, plain):
                self.assertLess(body.index("Jeftini"), body.index("Srednji"))
                self.assertLess(body.index("Srednji"), body.index("Skupi"))
                self.assertLess(body.index("Skupi"), body.index("Bez kvadrata"))

    def test_plavi_mail_uses_blue_theme(self):
        checked = datetime(2026, 10, 22, 7, 15, tzinfo=timezone.utc)
        body = render_html([listing()], checked, source="plavi")
        plain = render_plain([listing()], checked, source="plavi")
        self.assertIn("#0E6EB8", body)
        self.assertIn("Novi oglasi – Plavi oglasnik", body)
        self.assertIn("oglasnik.hr – automatski izvještaj", body)
        self.assertNotIn("#e30613", body)
        self.assertIn("Novi oglasi – Plavi oglasnik", plain)


class AgencyFilterTests(unittest.TestCase):
    def test_agencies_are_kept_out_of_the_mail_for_both_sources(self):
        private = listing(code=1, title="Privatni", legal_entity=False)
        agency = listing(code=2, title="Agencija", legal_entity=True)
        kept, skipped = _without_agencies([agency, private])
        self.assertEqual([item.title for item in kept], ["Privatni"])
        self.assertEqual([item.title for item in skipped], ["Agencija"])


class ScheduleTests(unittest.TestCase):
    def test_daytime_keeps_the_interval(self):
        after = _zagreb(2026, 10, 23, 21, 0)
        self.assertEqual(next_check_at(after, timedelta(minutes=5)), _zagreb(2026, 10, 23, 21, 5))

    def test_midnight_stays_the_last_check(self):
        after = _zagreb(2026, 10, 23, 23, 55)
        self.assertEqual(next_check_at(after, timedelta(minutes=5)), _zagreb(2026, 10, 24, 0, 0))
        self.assertFalse(in_night_pause(_zagreb(2026, 10, 24, 0, 0)))

    def test_interval_that_would_cross_midnight_stops_at_midnight(self):
        after = _zagreb(2026, 10, 23, 23, 57)
        self.assertEqual(next_check_at(after, timedelta(minutes=5)), _zagreb(2026, 10, 24, 0, 0))

    def test_after_midnight_the_next_check_is_eight(self):
        after = _zagreb(2026, 10, 24, 0, 0)
        self.assertEqual(next_check_at(after, timedelta(minutes=5)), _zagreb(2026, 10, 24, 8, 0))

    def test_night_hours_wait_until_morning(self):
        moment = _zagreb(2026, 10, 24, 2, 30)
        self.assertTrue(in_night_pause(moment))
        self.assertEqual(next_check_at(moment, timedelta(minutes=5)), _zagreb(2026, 10, 24, 8, 0))
        self.assertTrue(in_night_pause(_zagreb(2026, 10, 24, 7, 59, 59)))
        self.assertFalse(in_night_pause(_zagreb(2026, 10, 24, 8, 0)))

    def test_dst_fallback_still_resumes_at_eight_local(self):
        after = _zagreb(2026, 10, 25, 0, 10)
        self.assertEqual(next_check_at(after, timedelta(minutes=5)), _zagreb(2026, 10, 25, 8, 0))


def _zagreb(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo("Europe/Zagreb"))


class SubjectTests(unittest.TestCase):
    def test_index_and_plavi_share_the_same_shape(self):
        start = _zagreb(2026, 10, 23, 21, 10)
        end = _zagreb(2026, 10, 23, 21, 15)
        self.assertEqual(
            report_subject(3, source="index", period_start=start, period_end=end),
            "Novi Oglasi Index (3 komada) - 23.10.2026 21:10 - 21:15",
        )
        self.assertEqual(
            report_subject(1, source="plavi", period_start=start, period_end=end),
            "Novi Oglasi Plavi oglasnik (1 komad) - 23.10.2026 21:10 - 21:15",
        )

    def test_count_uses_croatian_plural(self):
        start = end = _zagreb(2026, 10, 23, 8, 0)
        self.assertIn("(1 komad)", report_subject(1, source="index", period_start=start, period_end=end))
        self.assertIn("(11 komada)", report_subject(11, source="index", period_start=start, period_end=end))
        self.assertIn("(21 komad)", report_subject(21, source="index", period_start=start, period_end=end))
        self.assertIn("(4 komada)", report_subject(4, source="plavi", period_start=start, period_end=end))

    def test_midnight_period_shows_both_dates(self):
        start = _zagreb(2026, 10, 23, 23, 55)
        end = _zagreb(2026, 10, 24, 0, 0)
        self.assertEqual(
            report_subject(2, source="index", period_start=start, period_end=end),
            "Novi Oglasi Index (2 komada) - 23.10.2026 23:55 - 24.10.2026 00:00",
        )

    def test_mail_keeps_the_same_subject_shape_when_marked_as_test(self):
        moment = _zagreb(2026, 10, 23, 21, 25)
        self.assertEqual(
            report_subject(1, source="plavi", period_start=moment, period_end=moment, test=True),
            "Test: Novi Oglasi Plavi oglasnik (1 komad) - 23.10.2026 21:25 - 21:25",
        )

    def test_lower_price_mail_strikes_the_old_price(self):
        checked = datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc)
        offer = Offer(listing(price=159000, price_m2=2650), 180000, 3000)
        body = render_html([offer], checked)
        plain = render_plain([offer], checked)
        self.assertIn("Obnovljeni oglasi – Index nekretnine", body)
        self.assertIn("Niža cijena", body)
        self.assertIn("Obnova, niža cijena", body)
        self.assertIn("line-through", body)
        self.assertIn("180.000 €", body)
        self.assertIn("159.000 €", body)
        self.assertIn("3.000 €", body)
        self.assertIn("2.650 €", body)
        self.assertIn("Pronađen 1 obnovljen oglas s nižom cijenom.", body)
        self.assertIn("180.000 € → 159.000 €", plain)
        self.assertIn("Obnova, niža cijena", plain)

    def test_mixed_mail_uses_one_summary(self):
        checked = datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc)
        fresh = listing(code=1, price=200000, price_m2=4000)
        renewed = Offer(listing(code=2, price=159000, price_m2=2650), 180000, 3000)
        body = render_html([fresh, renewed], checked, source="plavi")
        self.assertIn("Oglasi – Plavi oglasnik", body)
        self.assertIn("Nove objave i niže cijene", body)
        self.assertIn("Pronađen 1 novi oglas i 1 obnovljen s nižom cijenom.", body)
        self.assertIn("#0E6EB8", body)
        self.assertNotIn("#e30613", body)
        start = end = _zagreb(2026, 10, 25, 8, 0)
        self.assertEqual(
            report_subject(2, source="plavi", period_start=start, period_end=end, reduced=1),
            "Novi i obnovljeni Plavi oglasnik (2 komada) - 25.10.2026 08:00 - 08:00",
        )
        self.assertEqual(
            report_subject(1, source="index", period_start=start, period_end=end, reduced=1),
            "Obnovljeni oglasi, niža cijena Index (1 komad) - 25.10.2026 08:00 - 08:00",
        )


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
            columns = [row[1] for row in index.conn.execute("PRAGMA table_info(seen)")]
            self.assertIn("price_history", columns)
            index.close()
            plavi.close()

    def test_price_history_keeps_only_changes_and_ignores_unknown_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "seen.sqlite")
            store = Store(path, "index")
            when = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
            current = listing(code=5, price=180000, price_m2=3000)
            store.remember([current])
            self.assertEqual(store.backfill_prices([current, listing(code=9, price=1)], when), 1)
            self.assertEqual(store.codes(), {5})
            self.assertEqual(store.quote(5), (180000, 3000))
            self.assertEqual(store.record_price(current, when), "same")
            cheaper = listing(code=5, price=159000, price_m2=2650)
            agency = listing(code=6, price=100, price_m2=10, legal_entity=True)
            store.remember([agency])
            store.record_price(listing(code=6, price=200, price_m2=20, legal_entity=True), when)
            found = _lower_prices(store, [cheaper, agency, cheaper])
            self.assertEqual([item.listing.code for item in found], [5])
            self.assertEqual(found[0].previous_price, 180000)
            self.assertEqual(found[0].previous_price_m2, 3000)
            self.assertEqual(store.record_price(cheaper, when), "lower")
            self.assertEqual(store.record_price(cheaper, when), "same")
            history = store._history(5)
            self.assertEqual([point["price"] for point in history], [180000, 159000])
            self.assertIsNone(store.price_sweep_version())
            store.mark_price_sweep("abc")
            self.assertEqual(store.price_sweep_version(), "abc")
            store.close()


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
