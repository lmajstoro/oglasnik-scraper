"""Periodično praćenje novih oglasa za prodaju stanova."""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as clock, timedelta, timezone
from pathlib import Path

from mailer import ZAGREB, MailError, check_smtp, send_listings
from plavi import PlaviClient, classify_plavi
from scraper import IndexClient, ScraperError, is_renewal
from store import PLAVI, INDEX, Store

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("oglasnik")
# Zadnja provjera u ponoć, prva sljedeća u 8 ujutro. Između nema provjera.
_QUIET_AFTER = clock(0, 0)
_DAY_START = clock(8, 0)


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


@dataclass(frozen=True)
class Watch:
    source: str
    label: str
    store: Store
    client: IndexClient | PlaviClient


def main() -> None:
    _configure_output()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ZagrebFormatter("%(asctime)s INFO: %(message)s", "%d.%m.%Y %H:%M:%S"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    config = load_config()
    try:
        check_smtp(config)
    except MailError as exc:
        raise SystemExit(str(exc)) from exc
    log.info("Verzija je %s.", os.environ.get("APP_VERSION", "dev"))
    log.info("SMTP prijava uspjela. Mail ide na %s.", config.mail_to)
    path = config.database_path
    watches = [
        Watch(INDEX, "Index", Store(path, INDEX), IndexClient(config.page_delay_seconds)),
        Watch(PLAVI, "Plavi oglasnik", Store(path, PLAVI), PlaviClient(config.page_delay_seconds)),
    ]
    interval = timedelta(minutes=config.interval_minutes)
    log.info("Interval provjere je %s min.", config.interval_minutes)
    log.info("Nema provjera od 00:00 do 08:00.")
    log.info("Agencijski oglasi se ne šalju.")
    try:
        while True:
            _wait_out_quiet_hours()
            for watch in watches:
                _run_watch(config, watch)
            _wait_for_next_check(interval)
    except KeyboardInterrupt:
        log.info("Zaustavljeno.")
    finally:
        for watch in watches:
            watch.store.close()


def _run_watch(config: Config, watch: Watch) -> None:
    try:
        if not watch.store.baseline_complete():
            run_baseline(watch)
        if not watch.store.test_sent():
            send_test(config, watch)
        else:
            run_check(config, watch)
    except KeyboardInterrupt:
        raise
    except (ScraperError, MailError, OSError) as exc:
        log.error("%s: ciklus nije uspio: %s", watch.label, exc)
        log.info("%s: zadnji uspjeh ostaje. Novi pokušaj nakon intervala.", watch.label)
    except Exception:
        log.exception("%s: ciklus nije uspio. Novi pokušaj nakon intervala.", watch.label)


def run_baseline(watch: Watch) -> None:
    started = datetime.now(timezone.utc)
    log.info("%s: prvi prolaz sprema postojeće oglase. Mail se ne šalje.", watch.label)
    listings = watch.client.fetch_all()
    added = watch.store.save_baseline(listings)
    watch.store.set_last_success(started)
    log.info(
        "%s: baseline spremljen, %s zapisa, %s ukupno.",
        watch.label,
        added,
        watch.store.count(),
    )


def send_test(config: Config, watch: Watch) -> None:
    count = config.test_listing_count
    if count == 0:
        watch.store.mark_test_sent()
        log.info("%s: TEST_LISTING_COUNT je 0, testni mail se ne šalje.", watch.label)
        return
    samples = _sample_listings(watch.client, count)
    if not samples:
        raise ScraperError(f"{watch.label}: nema oglasa za testni mail.")
    if len(samples) < count:
        log.info("%s: za test je dostupno %s oglasa, ne %s.", watch.label, len(samples), count)
    log.info("%s: šaljem testni mail s %s oglasa.", watch.label, len(samples))
    checked = datetime.now(timezone.utc)
    send_listings(
        config,
        samples,
        checked,
        test=True,
        source=watch.source,
        period_start=checked,
        period_end=checked,
    )
    watch.store.mark_test_sent()
    log.info("%s: testni mail je poslan. Ti oglasi se neće ponoviti.", watch.label)


def _sample_listings(client: IndexClient | PlaviClient, count: int) -> list:
    found = []
    seen: set[int] = set()
    page = 1
    while len(found) < count and page is not None:
        items, next_page, _ = client.fetch_page(page)
        for item in items:
            if item.code in seen or item.legal_entity:
                continue
            seen.add(item.code)
            found.append(item)
            if len(found) == count:
                break
        page = next_page if len(found) < count else None
    return found


def run_check(config: Config, watch: Watch) -> None:
    started = datetime.now(timezone.utc)
    previous = watch.store.last_success()
    period = _period_label(previous, started)
    _write_check_header(period, watch.label)
    try:
        known = watch.store.codes()
        listings = watch.client.fetch_recent(known, previous)
        fresh = [listing for listing in listings if listing.code not in known]
        published, renewed = _split(watch, fresh)
        private, agencies = _without_agencies(published)
        if private:
            send_listings(
                config,
                private,
                started,
                source=watch.source,
                period_start=previous or started,
                period_end=started,
            )
            watch.store.mark_sent(private, datetime.now(timezone.utc))
        watch.store.remember([*renewed, *agencies])
        watch.store.set_last_success(started)
    except (ScraperError, MailError, OSError):
        log.info("Provjera nije uspjela. Mail nije poslan.")
        return
    except Exception:
        log.info("Provjera nije uspjela. Mail nije poslan.")
        log.exception("Provjera nije uspjela.")
        return
    for line in _check_lines(len(private), [item.url for item in renewed]):
        log.info("%s", line)


def _without_agencies(listings: list) -> tuple[list, list]:
    private = [item for item in listings if not item.legal_entity]
    agencies = [item for item in listings if item.legal_entity]
    return private, agencies


def _split(watch: Watch, fresh: list) -> tuple[list, list]:
    if watch.source == PLAVI:
        return classify_plavi(fresh, watch.store.max_code())
    renewed = [item for item in fresh if is_renewal(item)]
    published = [item for item in fresh if not is_renewal(item)]
    return published, renewed


RULE = "-----------------------------------------------------------"


def _write_check_header(period: str, label: str) -> None:
    log.info("%s", RULE)
    log.info("Počinjem provjeru za period %s, %s", period, label)
    log.info("%s", RULE)


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


def in_night_pause(moment: datetime) -> bool:
    current = _as_zagreb(moment).time()
    return _QUIET_AFTER < current < _DAY_START


def next_check_at(after: datetime, interval: timedelta) -> datetime:
    """Sljedeći početak provjere. Ponoć ostaje zadnji termin, pa pauza do 08:00."""
    local = _as_zagreb(after)
    if in_night_pause(local):
        return _at_clock(local, _DAY_START)
    candidate = local + interval
    if not in_night_pause(candidate):
        return candidate
    midnight = _at_clock(candidate, _QUIET_AFTER)
    if local < midnight:
        return midnight
    return _at_clock(candidate, _DAY_START)


def _wait_out_quiet_hours() -> None:
    now = datetime.now(timezone.utc)
    if not in_night_pause(now):
        return
    wake = next_check_at(now, timedelta(0))
    log.info("Noćna pauza. Sljedeća provjera u 08:00.")
    _sleep_until(wake)


def _wait_for_next_check(interval: timedelta) -> None:
    wake = next_check_at(datetime.now(timezone.utc), interval)
    if _as_zagreb(wake).time() == _DAY_START:
        log.info("Noćna pauza. Sljedeća provjera u 08:00.")
    _sleep_until(wake)


def _sleep_until(target: datetime) -> None:
    delay = (target - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        time.sleep(delay)


def _as_zagreb(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZAGREB)


def _at_clock(local: datetime, mark: clock) -> datetime:
    return local.replace(hour=mark.hour, minute=mark.minute, second=0, microsecond=0)


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
