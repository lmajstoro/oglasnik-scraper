"""Čitanje prodaje stanova za Zagrebačku i Grad Zagreb s Plavog oglasnika."""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from scraper import Listing, ScraperError, should_stop

log = logging.getLogger(__name__)

ZAGREB = ZoneInfo("Europe/Zagreb")
SEARCH_URL = "https://oglasnik.hr/stanovi-prodaja"
PAGE_SIZE = 100
MIN_PAGES = 1
MAX_PAGES = 60
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 oglasnik-scraper/1.0"
)
_AD_MARK = '"ad":{'


def classify_plavi(fresh: list[Listing], max_code: int | None) -> tuple[list[Listing], list[Listing]]:
    """Novi je samo broj veći od dosad viđenog. Manji broj je povratak stare objave."""
    if max_code is None:
        return [], list(fresh)
    published = [item for item in fresh if item.code > max_code]
    renewed = [item for item in fresh if item.code <= max_code]
    return published, renewed


def extract_ads(payload: str) -> list[dict]:
    text = payload.replace('\\"', '"')
    ads: list[dict] = []
    seen: set[int] = set()
    cursor = 0
    while True:
        found = text.find(_AD_MARK, cursor)
        if found < 0:
            break
        start = found + len('"ad":')
        blob, end = _read_object(text, start)
        cursor = end
        if blob is None:
            break
        try:
            raw = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        code = int(raw["id"])
        if code in seen:
            continue
        seen.add(code)
        ads.append(raw)
    return ads


def listing_from_ad(raw: dict, url: str | None = None) -> Listing:
    posted = _posted_at(raw)
    county, city, settlement = _location(raw)
    price = _price(raw)
    area = _area(raw)
    price_m2 = price / area if price is not None and area else None
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    code = int(raw["id"])
    title = " ".join(str(raw.get("title") or "").split()) or f"Oglas {code}"
    if not url:
        url = f"https://oglasnik.hr/stanovi-prodaja/{_slug(title)}-oglas-{code}"
    return Listing(
        code=code,
        title=title,
        price=price,
        price_m2=price_m2,
        area=area,
        rooms=None,
        settlement=settlement,
        city=city,
        county=county,
        posted_at=posted,
        renewed_at=None,
        is_promoted=False,
        legal_entity=user.get("type") == "trgovina",
        url=url,
    )


class PlaviClient:
    def __init__(self, page_delay_seconds: float = 0.3) -> None:
        self.page_delay_seconds = page_delay_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.8",
                "RSC": "1",
                "Accept": "text/x-component",
                "Referer": SEARCH_URL,
            }
        )

    def fetch_all(self) -> list[Listing]:
        return self._collect(full=True, known=set(), last_success=None)

    def fetch_recent(self, known: set[int], last_success: datetime | None) -> list[Listing]:
        return self._collect(full=False, known=known, last_success=last_success)

    def fetch_page(self, page: int) -> tuple[list[Listing], int | None, int | None]:
        payload = self._get(page)
        listings: list[Listing] = []
        for raw in extract_ads(payload):
            code = int(raw["id"])
            try:
                listings.append(listing_from_ad(raw))
            except (TypeError, ValueError) as exc:
                log.warning("Preskačem oglas %s: %s", code, exc)
        markers = payload.replace('\\"', '"').count(_AD_MARK)
        nxt = page + 1 if markers >= PAGE_SIZE else None
        return listings, nxt, None

    def _collect(
        self,
        full: bool,
        known: set[int],
        last_success: datetime | None,
    ) -> list[Listing]:
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
                "Plavi oglasnik, stranica %s: %s oglasa (ukupno pročitano %s%s)",
                page,
                len(found) - before,
                len(found),
                f" od {total_hint}" if total_hint else "",
            )
            if len(found) == before:
                break
            if not full and page >= MIN_PAGES and should_stop(items, known, last_success):
                log.debug("Plavi oglasnik, stranica %s je starija od zadnje provjere.", page)
                break
            if next_page is None:
                break
            page = next_page
        else:
            raise ScraperError(f"Plavi oglasnik ima više od {MAX_PAGES} stranica.")
        if not found:
            raise ScraperError("Plavi oglasnik nije vratio nijedan oglas.")
        return list(found.values())

    def _get(self, page: int) -> str:
        params = {
            "f[4][7442]": "true",
            "f[4][7760]": "true",
            "page": str(page),
        }
        for attempt in range(5):
            try:
                response = self.session.get(SEARCH_URL, params=params, timeout=45)
            except requests.RequestException as exc:
                delay = min(60.0, float(2 ** (attempt + 1)))
                log.warning("Plavi oglasnik nije dostupan (%s). Čekam %s s.", exc, delay)
                time.sleep(delay)
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                delay = min(60.0, float(2 ** (attempt + 1)))
                log.warning("Plavi oglasnik je vratio %s. Čekam %s s.", response.status_code, delay)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise ScraperError(
                    f"Plavi oglasnik je vratio {response.status_code}: {response.text[:200]}"
                )
            return response.content.decode("utf-8")
        raise ScraperError("Plavi oglasnik ne odgovara nakon nekoliko pokušaja.")


def _slug(title: str) -> str:
    text = unicodedata.normalize("NFKD", title.casefold())
    text = text.replace("đ", "d")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "oglas"


def _read_object(text: str, start: int) -> tuple[str | None, int]:
    if start >= len(text) or text[start] != "{":
        return None, start + 1
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
    return None, len(text)


def _posted_at(raw: dict) -> datetime:
    stamp = raw.get("sort_date")
    if isinstance(stamp, int):
        return datetime.fromtimestamp(stamp, timezone.utc)
    published = raw.get("publish")
    if isinstance(published, str) and published.strip():
        local = datetime.strptime(published.strip(), "%d.%m.%Y %H:%M:%S")
        return local.replace(tzinfo=ZAGREB).astimezone(timezone.utc)
    raise ValueError(f"oglas {raw.get('id')} nema vrijeme")


def _location(raw: dict) -> tuple[str | None, str | None, str | None]:
    names = []
    for part in raw.get("location") or []:
        if not isinstance(part, dict):
            continue
        name = str(part.get("name") or "").strip()
        if name and name.casefold() != "hrvatska":
            names.append(name)
    county = names[0] if names else None
    city = names[1] if len(names) > 1 else None
    settlement = names[2] if len(names) > 2 else None
    return county, city, settlement


def _price(raw: dict) -> float | None:
    price = raw.get("price")
    if not isinstance(price, dict):
        return None
    value = price.get("value")
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _area(raw: dict) -> float | None:
    searchable = raw.get("searchable")
    if not isinstance(searchable, dict):
        return None
    value = searchable.get("re_total_area")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return float(value)
