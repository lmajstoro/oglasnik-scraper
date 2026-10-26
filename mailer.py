"""Jedan mail sa svim oglasima koji još nisu poslani."""

from __future__ import annotations

import html
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from plavi import SEARCH_URL as PLAVI_SEARCH_BASE
from scraper import COUNTY_IDS, Listing

ZAGREB = ZoneInfo("Europe/Zagreb")
DIGEST_TO = "luka.majstorovic321@gmail.com"
_WEEKDAYS = ("pon", "uto", "sri", "čet", "pet", "sub", "ned")
TEST_NOTE_ONE = (
    "Ovo je testni oglas. Služi samo za provjeru da mail stiže i neće se poslati ponovo."
)
TEST_NOTE_MANY = (
    "Ovo su testni oglasi. Služe samo za provjeru da mail stiže i neće se poslati ponovo."
)


class MailError(RuntimeError):
    pass


@dataclass(frozen=True)
class Offer:
    listing: Listing
    previous_price: float | None = None
    previous_price_m2: float | None = None


def send_listings(
    config,
    listings: list[Listing],
    checked_at: datetime,
    *,
    test: bool = False,
    source: str = "index",
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> None:
    offers = _ordered(listings)
    if not offers:
        return
    note = test_note(len(offers)) if test else None
    reduced = sum(1 for item in offers if item.previous_price is not None)
    subject = report_subject(
        len(offers),
        source=source,
        period_start=period_start or checked_at,
        period_end=period_end or checked_at,
        test=test,
        reduced=reduced,
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.mail_from
    message["To"] = config.mail_to
    message.set_content(render_plain(offers, checked_at, note, source=source), charset="utf-8")
    message.add_alternative(render_html(offers, checked_at, note, source=source), subtype="html")
    try:
        with connect(config) as smtp:
            smtp.send_message(message)
    except MailError:
        raise
    except smtplib.SMTPException as exc:
        raise MailError(f"Slanje maila nije uspjelo: {exc}") from exc


def test_note(count: int) -> str:
    if count == 1:
        return TEST_NOTE_ONE
    return TEST_NOTE_MANY


def check_smtp(config) -> None:
    try:
        with connect(config) as smtp:
            smtp.noop()
    except MailError:
        raise
    except smtplib.SMTPException as exc:
        raise MailError(f"SMTP provjera nije uspjela: {exc}") from exc


def render_plain(
    listings: list[Listing],
    checked_at: datetime,
    note: str | None = None,
    source: str = "index",
) -> str:
    ordered = _ordered(listings)
    theme, _subtitle, summary = _copy(ordered, source)
    blocks = []
    for offer in ordered:
        listing = offer.listing
        blocks.append(
            "\n".join(
                [
                    f"{listing.title} ({seller_label(listing.legal_entity)})",
                    *_plain_price(offer),
                    format_location(listing),
                    listing.url,
                ]
            )
        )
    header = (
        f"{theme['title']}\n"
        f"{summary}\n"
        f"Provjera: {format_dt(checked_at)}"
    )
    if note:
        header = f"{header}\n{note}"
    return header + "\n\n" + "\n\n".join(blocks) + "\n"


def render_html(
    listings: list[Listing],
    checked_at: datetime,
    note: str | None = None,
    source: str = "index",
) -> str:
    ordered = _ordered(listings)
    theme, subtitle, summary = _copy(ordered, source)
    rows = "\n".join(_row(offer, theme["link"]) for offer in ordered)
    note_html = ""
    if note:
        note_html = (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            "line-height:1.4;color:#9a3412;padding-top:8px;\">"
            f"{html.escape(note)}</div>"
        )
    return f"""<!DOCTYPE html>
<html lang="hr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(theme['title'])}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;">
          <tr>
            <td bgcolor="{theme['header']}" style="background:{theme['header']};padding:22px 28px;border-radius:16px 16px 0 0;">
              <div style="font-family:Arial,Helvetica,sans-serif;font-size:22px;line-height:1.3;font-weight:700;color:#ffffff;">{html.escape(theme['title'])}</div>
              <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.4;color:{theme['sub']};padding-top:6px;">{html.escape(subtitle)}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 28px 6px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.4;color:#6b7280;">
              {html.escape(summary)}
              {note_html}
            </td>
          </tr>
          <tr>
            <td style="padding:8px 16px 12px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#9ca3af;">Naslov</td>
                  <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#9ca3af;white-space:nowrap;">Cijena</td>
                  <td align="right" style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#9ca3af;">Link</td>
                </tr>
                {rows}
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 18px;border-top:1px solid #f3f4f6;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#9ca3af;">{html.escape(theme['footer'])}</td>
                  <td align="right" style="font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:700;color:#4b5563;white-space:nowrap;">{html.escape(format_clock(checked_at))}</td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def report_subject(
    count: int,
    *,
    source: str,
    period_start: datetime,
    period_end: datetime,
    test: bool = False,
    reduced: int = 0,
) -> str:
    name = "Plavi oglasnik" if source == "plavi" else "Index"
    unit = "komad" if count % 10 == 1 and count % 100 != 11 else "komada"
    span = _report_span(period_start, period_end)
    fresh = count - reduced
    if reduced and fresh:
        subject = f"Novi i obnovljeni {name} ({count} {unit}) - {span}"
    elif reduced:
        subject = f"Obnovljeni oglasi, niža cijena {name} ({count} {unit}) - {span}"
    else:
        subject = f"Novi Oglasi {name} ({count} {unit}) - {span}"
    if test:
        return f"Test: {subject}"
    return subject


def _report_span(period_start: datetime, period_end: datetime) -> str:
    start = _as_zagreb(period_start)
    end = _as_zagreb(period_end)
    if start.date() == end.date():
        return f"{start:%d.%m.%Y} {start:%H:%M} - {end:%H:%M}"
    return f"{start:%d.%m.%Y} {start:%H:%M} - {end:%d.%m.%Y} {end:%H:%M}"


def _as_zagreb(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZAGREB)


def _theme(source: str) -> dict[str, str]:
    if source == "plavi":
        return {
            "title": "Novi oglasi – Plavi oglasnik",
            "header": "#0E6EB8",
            "sub": "#d6ebfa",
            "link": "#0E6EB8",
            "footer": "oglasnik.hr – automatski izvještaj",
        }
    return {
        "title": "Novi oglasi – Index nekretnine",
        "header": "#e30613",
        "sub": "#ffd5d5",
        "link": "#1d4ed8",
        "footer": "Index.hr oglasi – automatski izvještaj",
    }


def _row(offer: Offer, link_color: str = "#1d4ed8") -> str:
    listing = offer.listing
    title, url, location, price, per_m2, label = _bits(listing)
    renewal = ""
    if offer.previous_price is not None:
        renewal = (
            '<div style="font-size:12px;color:#b45309;padding-top:4px;">Obnova, niža cijena</div>'
        )
    return f"""<tr>
                  <td style="padding:14px 12px;border-bottom:1px solid #f3f4f6;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.4;color:#111827;vertical-align:top;">
                    {title}
                    <div style="font-size:12px;color:#6b7280;padding-top:2px;">{location} ({label})</div>
                    {renewal}
                  </td>
                  <td style="padding:14px 12px;border-bottom:1px solid #f3f4f6;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#111827;white-space:nowrap;vertical-align:top;">
                    {_price_html(offer, price, per_m2)}
                  </td>
                  <td align="right" style="padding:14px 12px;border-bottom:1px solid #f3f4f6;vertical-align:top;white-space:nowrap;">
                    <a href="{url}" style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:{link_color};text-decoration:underline;">Otvori oglas</a>
                  </td>
                </tr>"""


def _bits(listing: Listing) -> tuple[str, str, str, str, str, str]:
    return (
        html.escape(listing.title),
        html.escape(listing.url, quote=True),
        html.escape(format_location(listing)),
        html.escape(format_price(listing.price)),
        html.escape(format_price(listing.price_m2)),
        html.escape(seller_label(listing.legal_entity)),
    )


def _as_offer(item: Listing | Offer) -> Offer:
    if isinstance(item, Offer):
        return item
    return Offer(item)


def _ordered(listings: list[Listing | Offer]) -> list[Offer]:
    return sorted(
        [_as_offer(item) for item in listings],
        key=lambda item: (
            item.listing.price_m2 is None,
            item.listing.price_m2 or 0,
            item.listing.code,
        ),
    )


def _copy(offers: list[Offer], source: str) -> tuple[dict[str, str], str, str]:
    theme = dict(_theme(source))
    fresh = sum(1 for item in offers if item.previous_price is None)
    reduced = len(offers) - fresh
    if fresh and reduced:
        theme["title"] = (
            "Oglasi – Plavi oglasnik" if source == "plavi" else "Oglasi – Index nekretnine"
        )
        return theme, "Nove objave i niže cijene", _mixed_label(fresh, reduced)
    if reduced:
        theme["title"] = (
            "Obnovljeni oglasi – Plavi oglasnik"
            if source == "plavi"
            else "Obnovljeni oglasi – Index nekretnine"
        )
        return theme, "Niža cijena", _reduced_label(reduced)
    return theme, "Nove objave", found_label(fresh)


def _plain_price(offer: Offer) -> list[str]:
    listing = offer.listing
    if offer.previous_price is None:
        per_m2 = format_price(listing.price_m2)
        price = format_price(listing.price)
        line = price if per_m2 == "—" else f"{price} ({per_m2}/m²)"
        return [line]
    old = format_price(offer.previous_price)
    new = format_price(listing.price)
    line = f"{old} → {new}"
    if offer.previous_price_m2 is not None or listing.price_m2 is not None:
        line += (
            f" ({format_price(offer.previous_price_m2)}/m² → {format_price(listing.price_m2)}/m²)"
        )
    return ["Obnova, niža cijena", line]


def _price_html(offer: Offer, price: str, per_m2: str) -> str:
    if offer.previous_price is None:
        extra = ""
        if per_m2 != "—":
            extra = (
                '<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;'
                f'color:#9ca3af;padding-top:2px;">{per_m2}/m²</div>'
            )
        return f"{price}{extra}"
    old = html.escape(format_price(offer.previous_price))
    lines = [
        (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#9ca3af;'
            f'text-decoration:line-through;">{old}</div>'
        ),
        (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:700;'
            f'color:#111827;">{price}</div>'
        ),
    ]
    if offer.previous_price_m2 is not None:
        old_m2 = html.escape(format_price(offer.previous_price_m2))
        lines.append(
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#9ca3af;'
            f'text-decoration:line-through;">{old_m2}/m²</div>'
        )
    if per_m2 != "—":
        lines.append(
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700;'
            f'color:#6b7280;">{per_m2}/m²</div>'
        )
    return "".join(lines)


def _reduced_label(count: int) -> str:
    if count == 1:
        return "Pronađen 1 obnovljen oglas s nižom cijenom."
    return f"Pronađeno {count} obnovljenih oglasa s nižom cijenom."


def _mixed_label(fresh: int, reduced: int) -> str:
    if fresh == 1 and reduced == 1:
        return "Pronađen 1 novi oglas i 1 obnovljen s nižom cijenom."
    return f"Pronađeno {fresh} novih oglasa i {reduced} obnovljenih s nižom cijenom."


def format_location(listing: Listing) -> str:
    parts = [part for part in (listing.settlement, listing.city, listing.county) if part]
    return ", ".join(parts) if parts else "Lokacija nije navedena"


def seller_label(legal_entity: bool) -> str:
    return "Agencija" if legal_entity else "Privatni prodavač"


def found_label(count: int) -> str:
    if count == 1:
        return "Pronađen 1 novi oglas."
    return f"Pronađeno {count} novih oglasa."


def format_clock(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZAGREB)
    return value.astimezone(ZAGREB).strftime("%H:%M")


def format_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZAGREB)
    local = value.astimezone(ZAGREB)
    return local.strftime("%d.%m.%Y. %H:%M")


def format_price(value: float | None) -> str:
    if value is None:
        return "—"
    rounded = round(value)
    if abs(value - rounded) < 0.001:
        return f"{int(rounded):,}".replace(",", ".") + " €"
    whole, frac = f"{value:.2f}".split(".")
    return f"{int(whole):,}".replace(",", ".") + "," + frac + " €"


def connect(config):
    smtp = None
    try:
        if int(config.smtp_port) == 465:
            smtp = smtplib.SMTP_SSL(config.smtp_host, int(config.smtp_port), timeout=30)
        else:
            smtp = smtplib.SMTP(config.smtp_host, int(config.smtp_port), timeout=30)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        smtp.login(config.smtp_user, config.smtp_password)
        return _SmtpGuard(smtp)
    except smtplib.SMTPAuthenticationError as exc:
        _close_quietly(smtp)
        raise MailError(
            "SMTP prijava nije uspjela. Za Gmail treba app password, ne obična lozinka računa."
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        _close_quietly(smtp)
        raise MailError(f"Spajanje na SMTP nije uspjelo: {exc}") from exc


def _close_quietly(smtp: smtplib.SMTP | None) -> None:
    if smtp is None:
        return
    try:
        smtp.close()
    except smtplib.SMTPException:
        pass


class _SmtpGuard:
    def __init__(self, smtp: smtplib.SMTP) -> None:
        self.smtp = smtp

    def __enter__(self) -> smtplib.SMTP:
        return self.smtp

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.smtp.quit()
        except smtplib.SMTPException:
            self.smtp.close()


def send_digest(config, rows: list[dict], start: date, end: date, sent_at: datetime, stats_since: date | None) -> None:
    subject = digest_subject(sent_at)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.mail_from
    message["To"] = DIGEST_TO
    message.set_content(_digest_plain(config, rows, start, end, stats_since), charset="utf-8")
    message.add_alternative(
        _digest_html(config, rows, start, end, sent_at, stats_since),
        subtype="html",
    )
    try:
        with connect(config) as smtp:
            smtp.send_message(message)
    except MailError:
        raise
    except smtplib.SMTPException as exc:
        raise MailError(f"Slanje dnevnog izvještaja nije uspjelo: {exc}") from exc


def digest_subject(sent_at: datetime) -> str:
    local = _as_zagreb(sent_at)
    return f"Dnevni izvještaj (30 dana) - {local:%d.%m.%Y} {local:%H:%M}"


def index_search_url() -> str:
    counties = "&".join(f"includeCountyIds={county}" for county in COUNTY_IDS)
    return (
        "https://www.index.hr/oglasi/nekretnine/prodaja-stanova/pretraga"
        "?category=flats-for-sale&module=real-estate&sortOption=4"
        f"&useRelevancyScoreSort=false&{counties}"
    )


def plavi_search_url() -> str:
    return f"{PLAVI_SEARCH_BASE}?f[4][7442]=true&f[4][7760]=true"


def _digest_html(config, rows: list[dict], start: date, end: date, sent_at: datetime, stats_since: date | None) -> str:
    totals = _digest_totals(rows)
    both_sent = totals["index_sent"] + totals["plavi_sent"]
    both_skipped = totals["index_skipped"] + totals["plavi_skipped"]
    table_rows = "\n".join(_digest_day_row(row, index) for index, row in enumerate(rows))
    since_html = _since_note(stats_since, start)
    local = _as_zagreb(sent_at)
    return f"""<!DOCTYPE html>
<html lang="hr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dnevni izvještaj</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:100%;max-width:680px;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;">
          <tr>
            <td bgcolor="#111827" style="background:#111827;padding:22px 28px;border-radius:16px 16px 0 0;">
              <div style="font-family:Arial,Helvetica,sans-serif;font-size:22px;line-height:1.3;font-weight:700;color:#ffffff;">Dnevni izvještaj</div>
              <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.4;color:#d1d5db;padding-top:6px;">{_day_span(start, end)} · poslano u {local:%H:%M}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 20px 4px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  {_digest_stat("Index poslano", totals["index_sent"], "#e30613")}
                  {_digest_stat("Index odbačeno", totals["index_skipped"], "#9ca3af")}
                  {_digest_stat("Plavi poslano", totals["plavi_sent"], "#0E6EB8")}
                  {_digest_stat("Plavi odbačeno", totals["plavi_skipped"], "#9ca3af")}
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 4px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.45;color:#6b7280;">
              Kumulativ 30 dana, oba izvora: <strong style="color:#111827;">{both_sent} poslano</strong>, {both_skipped} odbačeno.
              Poslano su novi privatni oglasi i obnove s nižom cijenom. Odbačeno su obnove i agencije, zajedno.
              {since_html}
            </td>
          </tr>
          <tr>
            <td style="padding:12px 16px 8px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td rowspan="2" style="padding:8px 10px;border-bottom:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#9ca3af;">Dan</td>
                  <td colspan="2" style="padding:8px 8px 2px;font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700;color:#e30613;">Index</td>
                  <td colspan="2" style="padding:8px 8px 2px;font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700;color:#0E6EB8;">Plavi oglasnik</td>
                </tr>
                <tr>
                  <td style="padding:2px 8px 8px;border-bottom:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#9ca3af;">Poslano</td>
                  <td style="padding:2px 8px 8px;border-bottom:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#9ca3af;">Odbačeno</td>
                  <td style="padding:2px 8px 8px;border-bottom:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#9ca3af;">Poslano</td>
                  <td style="padding:2px 8px 8px;border-bottom:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#9ca3af;">Odbačeno</td>
                </tr>
                {table_rows}
                <tr>
                  <td style="padding:10px;border-top:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#111827;background:#f9fafb;">Kumulativ</td>
                  <td style="padding:10px 8px;border-top:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#111827;background:#f9fafb;">{totals["index_sent"]}</td>
                  <td style="padding:10px 8px;border-top:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#111827;background:#f9fafb;">{totals["index_skipped"]}</td>
                  <td style="padding:10px 8px;border-top:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#111827;background:#f9fafb;">{totals["plavi_sent"]}</td>
                  <td style="padding:10px 8px;border-top:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#111827;background:#f9fafb;">{totals["plavi_skipped"]}</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 6px;font-family:Arial,Helvetica,sans-serif;font-size:16px;font-weight:700;color:#111827;">Postavke</td>
          </tr>
          <tr>
            <td style="padding:0 28px 18px;">
              {_digest_settings(config)}
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 18px;border-top:1px solid #f3f4f6;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#9ca3af;">
              Dnevni izvještaj ide samo na {html.escape(DIGEST_TO)}. Mailovi s oglasima idu na zasebnu listu.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _digest_plain(config, rows: list[dict], start: date, end: date, stats_since: date | None) -> str:
    totals = _digest_totals(rows)
    lines = [
        "Dnevni izvještaj",
        _day_span(start, end),
        f"Samo na {DIGEST_TO}",
        "",
        f"Kumulativ Index: {totals['index_sent']} poslano, {totals['index_skipped']} odbačeno",
        f"Kumulativ Plavi: {totals['plavi_sent']} poslano, {totals['plavi_skipped']} odbačeno",
    ]
    note = _since_text(stats_since, start)
    if note:
        lines.extend(["", note])
    lines.extend(["", "Dan | Index poslano | Index odbačeno | Plavi poslano | Plavi odbačeno"])
    for row in rows:
        day = row["day"]
        lines.append(
            f"{_WEEKDAYS[day.weekday()]} {day:%d.%m.} | {row['index_sent']} | "
            f"{row['index_skipped']} | {row['plavi_sent']} | {row['plavi_skipped']}"
        )
    lines.extend(
        [
            "",
            f"Interval: {config.interval_minutes} min",
            "Noćna pauza: 00:00–08:00, zadnja provjera u 00:00.",
            f"Mailovi s oglasima: {config.mail_to}",
            f"Index: {index_search_url()}",
            f"Plavi: {plavi_search_url()}",
            "Agencije se ne šalju. Obnove se ne šalju, osim privatne obnove s nižom cijenom.",
        ]
    )
    return "\n".join(lines) + "\n"


def _digest_totals(rows: list[dict]) -> dict[str, int]:
    keys = ("index_sent", "index_skipped", "plavi_sent", "plavi_skipped")
    return {key: sum(row[key] for row in rows) for key in keys}


def _digest_stat(label: str, value: int, color: str) -> str:
    return (
        '<td width="25%" valign="top" style="padding:0 6px;">'
        f'<div style="border-top:3px solid {color};background:#f9fafb;padding:12px 10px;">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#6b7280;">{html.escape(label)}</div>'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:26px;line-height:1.2;font-weight:700;color:#111827;padding-top:4px;">{value}</div>'
        "</div></td>"
    )


def _digest_day_row(row: dict, index: int) -> str:
    day = row["day"]
    background = "#ffffff" if index % 2 == 0 else "#f9fafb"
    label = f"{_WEEKDAYS[day.weekday()]} {day:%d.%m.}"
    cells = (
        _digest_count(row["index_sent"], background, emphasize=True),
        _digest_count(row["index_skipped"], background, emphasize=False),
        _digest_count(row["plavi_sent"], background, emphasize=True),
        _digest_count(row["plavi_skipped"], background, emphasize=False),
    )
    return (
        "<tr>"
        f'<td style="padding:6px 10px;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#111827;background:{background};">{label}</td>'
        + "".join(cells)
        + "</tr>"
    )


def _digest_count(value: int, background: str, emphasize: bool) -> str:
    color = "#111827" if value else "#d1d5db"
    weight = "700" if emphasize and value else "400"
    return (
        f'<td style="padding:6px 8px;font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:{weight};color:{color};background:{background};">{value}</td>'
    )


def _digest_settings(config) -> str:
    rows = [
        ("Interval", f"{config.interval_minutes} min"),
        ("Noćna pauza", "Nema provjera od 00:00 do 08:00. Zadnja provjera je u 00:00."),
        ("Ovaj izvještaj", f"Svaki dan u 00:00, zadnjih 30 dana, samo na {DIGEST_TO}."),
        ("Mailovi s oglasima", config.mail_to),
        ("Vrijeme", "Europe/Zagreb"),
        ("Pauza stranica", f"{config.page_delay_seconds:g} s"),
        ("Index dohvat", index_search_url()),
        ("Index područje", "Zagrebačka i Grad Zagreb, samo prodaja stanova, najnovije."),
        ("Index novo", "Kod nije viđen i oglas nema vrijeme obnove."),
        ("Index obnova", "Pamti se i ne šalje se. Iznimka je privatni oglas s nižom cijenom."),
        ("Index agencija", "legalEntity = da. Ne šalje se."),
        ("Plavi dohvat", plavi_search_url()),
        ("Plavi područje", "Grad Zagreb (7442) i Zagrebačka (7760), samo prodaja stanova."),
        ("Plavi novo", "Kod je veći od najvećeg dosad viđenog."),
        ("Plavi obnova", "Manji nepoznati kod se pamti i ne šalje se, osim niže cijene kod privatnog."),
        ("Plavi agencija", "user.type = trgovina. Ne šalje se."),
        ("Redoslijed u mailu", "Po €/m², od najniže. Jedan mail po izvoru. Prazan ciklus ne šalje mail."),
    ]
    body = []
    for label, value in rows:
        shown = html.escape(value)
        if value.startswith("http"):
            shown = (
                f'<a href="{html.escape(value, quote=True)}" '
                f'style="color:#1d4ed8;text-decoration:underline;">{shown}</a>'
            )
        body.append(
            "<tr>"
            f'<td valign="top" style="padding:7px 12px 7px 0;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#6b7280;white-space:nowrap;">{html.escape(label)}</td>'
            f'<td valign="top" style="padding:7px 0;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.4;color:#111827;word-break:break-all;">{shown}</td>'
            "</tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + "".join(body)
        + "</table>"
    )


def _since_note(stats_since: date | None, start: date) -> str:
    text = _since_text(stats_since, start)
    if not text:
        return ""
    return f"<div style=\"padding-top:6px;\">{html.escape(text)}</div>"


def _since_text(stats_since: date | None, start: date) -> str:
    if stats_since is None or stats_since <= start:
        return ""
    return f"Brojanje je počelo {stats_since:%d.%m.%Y.}. Dani prije toga su 0."


def _day_span(start: date, end: date) -> str:
    return f"{start:%d.%m.%Y.} – {end:%d.%m.%Y.}"
