"""Periodično praćenje novih oglasa za prodaju stanova."""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mailer import ZAGREB, MailError, check_smtp, send_listings
from scraper import IndexClient, ScraperError, is_renewal
from store import Store

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("oglasnik")


@dataclass(frozen=True)
class Config:
    interval_minutes: int
    database_path: str
    page_delay_seconds: float
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    mail_from: str
    mail_to: str
    test_listing_count: int


def main() -> None:
    _configure_output()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ZagrebFormatter("INFO: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    config = load_config()
    try:
        check_smtp(config)
    except MailError as exc:
        raise SystemExit(str(exc)) from exc
    log.info("SMTP prijava uspjela. Mail ide na %s.", config.mail_to)
    store = Store(config.database_path)
    client = IndexClient(page_delay_seconds=config.page_delay_seconds)
    interval = config.interval_minutes * 60
    log.info("Interval provjere je %s min.", config.interval_minutes)
    try:
        while True:
            try:
                if not store.baseline_complete():
                    run_baseline(store, client)
                if not store.test_sent():
                    send_test(config, store, client)
                else:
                    run_check(config, store, client)
            except KeyboardInterrupt:
                raise
            except (ScraperError, MailError, OSError) as exc:
                log.error("Ciklus nije uspio: %s", exc)
                log.info("Zadnji uspjeh ostaje. Novi pokušaj nakon intervala.")
            except Exception:
                log.exception("Ciklus nije uspio. Novi pokušaj nakon intervala.")
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Zaustavljeno.")
    finally:
        store.close()


def run_baseline(store: Store, client: IndexClient) -> None:
    started = datetime.now(timezone.utc)
    log.info("Prvi prolaz sprema postojeće oglase. Mail se ne šalje.")
    listings = client.fetch_all()
    added = store.save_baseline(listings)
    store.set_last_success(started)
    log.info("Baseline spremljen: %s zapisa, %s ukupno u bazi.", added, store.count())


def send_test(config: Config, store: Store, client: IndexClient) -> None:
    count = config.test_listing_count
    if count == 0:
        store.mark_test_sent()
        log.info("TEST_LISTING_COUNT je 0, testni mail se ne šalje.")
        return
    samples = _sample_listings(client, count)
    if not samples:
        raise ScraperError("Nema oglasa za testni mail.")
    if len(samples) < count:
        log.info("Za test je dostupno %s oglasa, ne %s.", len(samples), count)
    log.info("Šaljem testni mail s %s oglasa.", len(samples))
    send_listings(config, samples, datetime.now(timezone.utc), test=True)
    store.mark_test_sent()
    log.info("Testni mail je poslan. Ti oglasi se neće ponoviti.")


def _sample_listings(client: IndexClient, count: int) -> list:
    found = []
    seen: set[int] = set()
    page = 1
    while len(found) < count and page is not None:
        items, next_page, _ = client.fetch_page(page)
        for item in items:
            if item.code in seen:
                continue
            seen.add(item.code)
            found.append(item)
            if len(found) == count:
                break
        page = next_page if len(found) < count else None
    return found


def run_check(config: Config, store: Store, client: IndexClient) -> None:
    started = datetime.now(timezone.utc)
    period = _period_label(store.last_success(), started)
    _write_check_header(period)
    try:
        known = store.codes()
        listings = client.fetch_recent(known, store.last_success())
        fresh = [listing for listing in listings if listing.code not in known]
        renewed = [listing for listing in fresh if is_renewal(listing)]
        published = [listing for listing in fresh if not is_renewal(listing)]
        if published:
            send_listings(config, published, started)
            store.mark_sent(published, datetime.now(timezone.utc))
        if renewed:
            store.remember(renewed)
        store.set_last_success(started)
    except (ScraperError, MailError, OSError):
        log.info("Provjera nije uspjela. Mail nije poslan.")
        return
    except Exception:
        log.info("Provjera nije uspjela. Mail nije poslan.")
        log.exception("Provjera nije uspjela.")
        return
    for line in _check_lines(len(published), [item.url for item in renewed]):
        log.info("%s", line)


RULE = "-----------------------------------------------------------"


def _write_check_header(period: str) -> None:
    print(RULE, flush=True)
    log.info("Počinjem provjeru za period %s", period)
    print(RULE, flush=True)


def _check_lines(published: int, renewed_urls: list[str]) -> list[str]:
    if published == 0:
        lines = ["Nema novih oglasa."]
    else:
        lines = [f"Novih oglasa: {published}. Mail je poslan."]
    if not renewed_urls:
        return lines
    count = len(renewed_urls)
    if count == 1:
        lines.append("Pronađen 1, nije poslan jer je ponovno objavljen:")
    else:
        lines.append(f"Pronađeno {count}, nisu poslani jer su ponovno objavljeni:")
    lines.extend(renewed_urls)
    return lines


def _period_label(previous: datetime | None, started: datetime) -> str:
    end = _hhmm(started)
    if previous is None:
        return f"{end} - {end}"
    return f"{_hhmm(previous)} - {end}"


def _hhmm(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZAGREB).strftime("%H:%M")


def load_config() -> Config:
    _load_env_file(ROOT / ".env")
    interval = _required_int("CHECK_INTERVAL_MINUTES", default="5", minimum=1)
    delay = _required_float("PAGE_DELAY_SECONDS", default="0.3", minimum=0)
    database = _resolve(os.environ.get("DATABASE_PATH", "data/seen.sqlite"))
    smtp_port = _required_int("SMTP_PORT", default="587", minimum=1)
    test_count = _required_int("TEST_LISTING_COUNT", default="1", minimum=0)
    return Config(
        interval_minutes=interval,
        database_path=database,
        page_delay_seconds=delay,
        smtp_host=_required("SMTP_HOST"),
        smtp_port=smtp_port,
        smtp_user=_required("SMTP_USER"),
        smtp_password=_required("SMTP_PASSWORD"),
        mail_from=_required("MAIL_FROM"),
        mail_to=_required("MAIL_TO"),
        test_listing_count=test_count,
    )


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"U .env nedostaje {name}.")
    return value


def _required_int(name: str, default: str, minimum: int) -> int:
    raw = os.environ.get(name, default).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} mora biti cijeli broj.") from exc
    if value < minimum:
        raise SystemExit(f"{name} mora biti najmanje {minimum}.")
    return value


def _required_float(name: str, default: str, minimum: float) -> float:
    raw = os.environ.get(name, default).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} mora biti broj.") from exc
    if value < minimum:
        raise SystemExit(f"{name} mora biti najmanje {minimum}.")
    return value


def _resolve(path: str) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return str(candidate)


class _ZagrebFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created, ZAGREB)
        if datefmt:
            return moment.strftime(datefmt)
        return moment.strftime("%Y-%m-%d %H:%M:%S") + f",{int(record.msecs):03d}"


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    main()
