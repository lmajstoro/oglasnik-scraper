"""Jedan mail sa svim oglasima koji još nisu poslani."""

from __future__ import annotations

import html
import smtplib
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from scraper import Listing

ZAGREB = ZoneInfo("Europe/Zagreb")
TEST_NOTE_ONE = (
    "Ovo je testni oglas. Služi samo za provjeru da mail stiže i neće se poslati ponovo."
)
TEST_NOTE_MANY = (
    "Ovo su testni oglasi. Služe samo za provjeru da mail stiže i neće se poslati ponovo."
)


class MailError(RuntimeError):
    pass


def send_listings(
    config,
    listings: list[Listing],
    checked_at: datetime,
    *,
    test: bool = False,
    source: str = "index",
) -> None:
    if not listings:
        return
    count = len(listings)
    note = test_note(count) if test else None
    subject = _subject(count, test=test, source=source)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.mail_from
    message["To"] = config.mail_to
    message.set_content(render_plain(listings, checked_at, note, source=source), charset="utf-8")
    message.add_alternative(render_html(listings, checked_at, note, source=source), subtype="html")
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
    blocks = []
    for listing in ordered:
        per_m2 = format_price(listing.price_m2)
        price = format_price(listing.price)
        price_line = price if per_m2 == "—" else f"{price} ({per_m2}/m²)"
        blocks.append(
            "\n".join(
                [
                    f"{listing.title} ({seller_label(listing.legal_entity)})",
                    price_line,
                    format_location(listing),
                    listing.url,
                ]
            )
        )
    theme = _theme(source)
    header = (
        f"{theme['title']}\n"
        f"{found_label(len(ordered))}\n"
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
    theme = _theme(source)
    rows = "\n".join(_row(listing, theme["link"]) for listing in ordered)
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
  <title>Novi oglasi</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;">
          <tr>
            <td bgcolor="{theme['header']}" style="background:{theme['header']};padding:22px 28px;border-radius:16px 16px 0 0;">
              <div style="font-family:Arial,Helvetica,sans-serif;font-size:22px;line-height:1.3;font-weight:700;color:#ffffff;">{html.escape(theme['title'])}</div>
              <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.4;color:{theme['sub']};padding-top:6px;">Nove objave</div>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 28px 6px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.4;color:#6b7280;">
              {html.escape(found_label(len(ordered)))}
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


def _subject(count: int, *, test: bool, source: str) -> str:
    if source == "plavi":
        if test and count == 1:
            return "Test: novi oglas, Plavi oglasnik"
        if test:
            return f"Test: novi oglasi, Plavi oglasnik ({count})"
        return f"Novi oglasi, Plavi oglasnik ({count})"
    if test and count == 1:
        return "Test: novi oglas za stanove"
    if test:
        return f"Test: novi oglasi za stanove ({count})"
    return f"Novi oglasi za stanove ({count})"


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


def _row(listing: Listing, link_color: str = "#1d4ed8") -> str:
    title, url, location, price, per_m2, label = _bits(listing)
    per_m2_html = ""
    if per_m2 != "—":
        per_m2_html = (
            f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#9ca3af;padding-top:2px;">'
            f"{per_m2}/m²</div>"
        )
    return f"""<tr>
                  <td style="padding:14px 12px;border-bottom:1px solid #f3f4f6;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.4;color:#111827;vertical-align:top;">
                    {title}
                    <div style="font-size:12px;color:#6b7280;padding-top:2px;">{location} ({label})</div>
                  </td>
                  <td style="padding:14px 12px;border-bottom:1px solid #f3f4f6;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#111827;white-space:nowrap;vertical-align:top;">
                    {price}{per_m2_html}
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


def _ordered(listings: list[Listing]) -> list[Listing]:
    return sorted(listings, key=lambda item: (item.activity, item.code), reverse=True)


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
