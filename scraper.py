"""Čitanje prodaje stanova za Zagrebačku i Grad Zagreb s Index Oglasa."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

HOME_URL = "https://www.index.hr/oglasi/"
API_URL = "https://www.index.hr/oglasi/api/aditem"
REFERER = "https://www.index.hr/oglasi/nekretnine/prodaja-stanova/pretraga"
COUNTY_IDS = (
    "b50e0d7d-8f7b-4ee8-af58-47851b4f46ee",  # Zagrebačka
    "056b6c84-e6f1-433f-8bdc-9b8dbb86d6fb",  # Grad Zagreb
)
PAGE_SIZE = 24
MIN_PAGES = 5
MAX_PAGES = 400
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 oglasnik-scraper/1.0"
)


class ScraperError(RuntimeError):
    pass


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Listing:
    code: int
    title: str
    price: float | None
    price_m2: float | None
    area: float | None
    rooms: int | None
    settlement: str | None
    city: str | None
    county: str | None
    posted_at: datetime
    renewed_at: datetime | None
    is_promoted: bool
    legal_entity: bool
    url: str

    @property
    def activity(self) -> datetime:
        if self.renewed_at and self.renewed_at > self.posted_at:
            return self.renewed_at
        return self.posted_at

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Listing:
        if raw.get("code") is None:
            raise ValueError("oglas nema kod")
        posted = parse_time(raw.get("postedTime"))
        if posted is None:
            raise ValueError(f"oglas {raw.get('code')} nema datum objave")
        summary = raw.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        rooms = summary.get("numberOfRooms")
        area = summary.get("area")
        slug = (raw.get("smartLink") or "").strip("/")
        code = int(raw["code"])
        if slug:
            url = (
                "https://www.index.hr/oglasi/nekretnine/prodaja-stanova/oglas/"
                f"{slug}/{code}"
            )
        else:
            url = f"https://www.index.hr/oglasi/nekretnine/prodaja-stanova/oglas/{code}"
        title = " ".join(str(raw.get("title") or "").split()) or f"Oglas {code}"
        return cls(
            code=code,
            title=title,
            price=_number(raw.get("price")),
            price_m2=_number(raw.get("priceM2")),
            area=_number(area),
            rooms=int(rooms) if rooms is not None and rooms != "" else None,
            settlement=_text(raw.get("settlementName")),
            city=_text(raw.get("cityName")),
            county=_text(raw.get("countyName")),
            posted_at=posted,
            renewed_at=parse_time(raw.get("renewalTime")),
            is_promoted=bool(raw.get("isPromoted")),
            legal_entity=bool(raw.get("legalEntity")),
            url=url,
        )


def is_renewal(listing: Listing) -> bool:
    """Obnova postavi renewalTime. Prva objava ga nema.

    Index tada često prepiše i postedTime na isti trenutak i vrati stari kod
    u pretragu. Sam kod zato nije dovoljan da se obnova razlikuje od nove objave.
    """
    return listing.renewed_at is not None


def should_stop(items: list[Listing], known: set[int], last_success: datetime | None) -> bool:
    """Stani na običnoj stranici koja je cijela već poslana i starija od zadnjeg ciklusa.

    Promovirane stranice nisu kronološke, pa se na njima ne staje.
    Nepoznat kod ili aktivnost novija ili jednaka zadnjem ciklusu drži čitanje.
    """
    if not items:
        return True
    if all(item.is_promoted for item in items):
        return False
    if any(item.code not in known for item in items):
        return False
    if last_success is None:
        return False
    newest = max(item.activity for item in items)
    return as_utc(newest) < as_utc(last_success)


class IndexClient:
    def __init__(self, page_delay_seconds: float = 0.3) -> None:
        self.page_delay_seconds = page_delay_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.8",
            }
        )
        self._ready = False

    def fetch_all(self) -> list[Listing]:
        return self._collect(full=True, known=set(), last_success=None)

    def fetch_recent(self, known: set[int], last_success: datetime | None) -> list[Listing]:
        return self._collect(full=False, known=known, last_success=last_success)

    def _collect(
        self,
        full: bool,
        known: set[int],
        last_success: datetime | None,
    ) -> list[Listing]:
        self._ensure_session()
        found: dict[int, Listing] = {}
        page = 1
        total_hint: int | None = None
        while page <= MAX_PAGES:
            if page > 1 and self.page_delay_seconds > 0:
                time.sleep(self.page_delay_seconds)
            items, next_page, total = self.fetch_page(page)
            if total_hint is None:
                total_hint = total
            if not items:
                break
            before = len(found)
            for item in items:
                found.setdefault(item.code, item)
            logger = log.info if full else log.debug
            logger(
                "Index, stranica %s: %s oglasa (ukupno pročitano %s%s)",
                page,
                len(found) - before,
                len(found),
                f" od {total_hint}" if total_hint else "",
            )
            if not full and page >= MIN_PAGES and should_stop(items, known, last_success):
                log.debug("Stranica %s je starija od zadnje provjere, čitanje staje.", page)
                break
            if next_page is None:
                break
            page = next_page
        else:
            raise ScraperError(f"Pretraga ima više od {MAX_PAGES} stranica.")
        if not found:
            raise ScraperError("API nije vratio nijedan oglas.")
        return list(found.values())

    def fetch_page(self, page: int) -> tuple[list[Listing], int | None, int | None]:
        self._ensure_session()
        params: list[tuple[str, str]] = [
            ("category", "flats-for-sale"),
            ("module", "real-estate"),
            ("sortOption", "4"),
            ("useRelevancyScoreSort", "false"),
            ("itemPerPage", str(PAGE_SIZE)),
            ("page", str(page)),
        ]
        for county_id in COUNTY_IDS:
            params.append(("includeCountyIds", county_id))
        payload = self._get_json(API_URL, params)
        items: list[Listing] = []
        for raw in payload.get("data") or []:
            if not isinstance(raw, dict):
                continue
            try:
                items.append(Listing.from_api(raw))
            except (TypeError, ValueError) as exc:
                log.warning("Preskačem oglas na stranici %s: %s", page, exc)
        return items, _next_page(page, payload, len(items)), _optional_int(payload.get("count"))

    def _ensure_session(self) -> None:
        if self._ready:
            return
        self._open_home()

    def _open_home(self) -> None:
        try:
            response = self._request(HOME_URL, params=None, accept="text/html")
        except requests.RequestException as exc:
            raise ScraperError(f"Naslovnica Index Oglasa nije dostupna: {exc}") from exc
        if response is None or response.status_code >= 400:
            status = response.status_code if response is not None else "bez odgovora"
            raise ScraperError(f"Naslovnica Index Oglasa vratila je {status}.")
        self._ready = True

    def _get_json(self, url: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        refreshed = False
        for attempt in range(5):
            try:
                response = self._request(url, params=params, accept="application/json")
            except requests.RequestException as exc:
                delay = min(60.0, float(2 ** (attempt + 1)))
                log.warning("Mrežni poziv nije uspio (%s). Čekam %s s.", exc, delay)
                time.sleep(delay)
                continue
            if response.status_code == 400 and not refreshed:
                log.info("API je vratio 400, ponovno uzimam kolačiće.")
                self._ready = False
                self._open_home()
                refreshed = True
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                delay = _retry_delay(response, attempt)
                log.warning("API je vratio %s. Čekam %s s.", response.status_code, delay)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise ScraperError(
                    f"API je vratio {response.status_code}: {response.text[:200]}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ScraperError("API nije vratio JSON.") from exc
            if not isinstance(payload, dict):
                raise ScraperError("API je vratio neočekivan odgovor.")
            return payload
        raise ScraperError("API ne odgovara nakon nekoliko pokušaja.")

    def _request(
        self,
        url: str,
        params: list[tuple[str, str]] | None,
        accept: str,
    ) -> requests.Response:
        headers = {"Accept": accept, "Referer": REFERER}
        return self.session.get(url, params=params, headers=headers, timeout=30)


def _next_page(current: int, payload: dict[str, Any], item_count: int) -> int | None:
    # Zadnja stranica vraća nextPage: -1. Samo veći pozitivni broj znači nastavak.
    nxt = payload.get("nextPage")
    if isinstance(nxt, int) and nxt > current:
        return nxt
    if isinstance(nxt, str) and nxt.isdigit() and int(nxt) > current:
        return int(nxt)
    if nxt is None and item_count >= PAGE_SIZE:
        return current + 1
    return None


def _retry_delay(response: requests.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After")
    if header and header.isdigit():
        return min(60.0, float(header))
    return min(60.0, float(2 ** (attempt + 1)))


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
