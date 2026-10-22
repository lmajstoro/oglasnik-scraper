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
) -> None:
    if not listings:
        return
    count = len(listings)
    note = test_note(count) if test else None
    if test and count == 1:
        subject = "Test: novi oglas za stanove"
    elif test:
        subject = f"Test: novi oglasi za stanove ({count})"
    else:
        subject = f"Novi oglasi za stanove ({count})"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.mail_from
    message["To"] = config.mail_to
    message.set_content(render_plain(listings, checked_at, note), charset="utf-8")
    message.add_alternative(render_html(listings, checked_at, note), subtype="html")
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


def render_plain(listings: list[Listing], checked_at: datetime, note: str | None = None) -> str:
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
    header = f"Novi oglasi · {count_label(len(ordered))}\nProvjera: {format_dt(checked_at)}"
    if note:
        header = f"{header}\n{note}"
    return header + "\n\n" + "\n\n".join(blocks) + "\n"


def render_html(listings: list[Listing], checked_at: datetime, note: str | None = None) -> str:
    ordered = _ordered(listings)
    compact = len(ordered) > 3
    row = _compact_row if compact else _card
    cards = "\n".join(row(listing) for listing in ordered)
    note_html = ""
    if note:
        note_html = (
            '<div style="font-family:\'Segoe UI\',Helvetica,Arial,sans-serif;font-size:14px;'
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
<body style="margin:0;padding:0;background:#ffffff;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;">
    <tr>
      <td align="center" style="padding:28px 12px;">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="width:100%;max-width:560px;">
          <tr>
            <td style="padding:0 8px 18px;">
              <div style="font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:1.2;color:#1c1917;">Novi oglasi</div>
              <div style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.4;color:#78716c;padding-top:6px;">{html.escape(count_label(len(ordered)))} · {html.escape(format_dt(checked_at))}</div>
              {note_html}
            </td>
          </tr>
          {cards}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


CARD_FRAME = (
    "background:#ffffff;border:1px solid #d6d3d1;border-radius:12px;"
    "box-shadow:0 1px 2px rgba(28,25,23,0.08),0 8px 18px rgba(28,25,23,0.08);"
)


def _card(listing: Listing) -> str:
    title, url, location, price, per_m2_html, label = _bits(listing)
    return f"""<tr>
            <td style="padding:0 0 16px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="{CARD_FRAME}">
                <tr>
                  <td style="padding:18px 20px 8px;font-family:Georgia,'Times New Roman',serif;font-size:20px;line-height:1.3;color:#1c1917;">
                    <a href="{url}" style="color:#1c1917;text-decoration:none;">{title}</a>
                    <span style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;color:#78716c;"> ({label})</span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:0 20px 8px;">
                    <span style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:22px;font-weight:600;color:#9a3412;">{price}</span>{per_m2_html}
                  </td>
                </tr>
                <tr>
                  <td style="padding:0 20px 16px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.4;color:#44403c;">{location}</td>
                </tr>
                <tr>
                  <td style="padding:0 20px 18px;">
                    <a href="{url}" style="display:inline-block;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;font-weight:600;color:#ffffff;background:#1c1917;text-decoration:none;border-radius:8px;padding:10px 16px;">Otvori oglas</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""


def _compact_row(listing: Listing) -> str:
    title, url, location, price, per_m2_html, label = _bits(listing, size="13px")
    return f"""<tr>
            <td style="padding:0 0 8px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="{CARD_FRAME}">
                <tr>
                  <td style="padding:10px 14px 2px;font-family:Georgia,'Times New Roman',serif;font-size:16px;line-height:1.3;color:#1c1917;">
                    <a href="{url}" style="color:#1c1917;text-decoration:none;">{title}</a>
                    <span style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:13px;color:#78716c;"> ({label})</span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:2px 14px 0;">
                    <span style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:16px;font-weight:600;color:#9a3412;">{price}</span>{per_m2_html}
                  </td>
                </tr>
                <tr>
                  <td style="padding:4px 14px 10px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:13px;line-height:1.4;color:#44403c;">{location}</td>
                        <td align="right" style="padding-left:12px;white-space:nowrap;">
                          <a href="{url}" style="display:inline-block;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:13px;font-weight:600;color:#ffffff;background:#1c1917;text-decoration:none;border-radius:8px;padding:6px 10px;">Otvori oglas</a>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""


def _bits(listing: Listing, size: str = "14px") -> tuple[str, str, str, str, str, str]:
    per_m2 = format_price(listing.price_m2)
    per_m2_html = ""
    if per_m2 != "—":
        per_m2_html = (
            f'<span style="font-family:\'Segoe UI\',Helvetica,Arial,sans-serif;font-size:{size};'
            f'color:#78716c;"> {html.escape(per_m2)}/m²</span>'
        )
    return (
        html.escape(listing.title),
        html.escape(listing.url, quote=True),
        html.escape(format_location(listing)),
        html.escape(format_price(listing.price)),
        per_m2_html,
        html.escape(seller_label(listing.legal_entity)),
    )


def _ordered(listings: list[Listing]) -> list[Listing]:
    return sorted(listings, key=lambda item: (item.activity, item.code), reverse=True)


def format_location(listing: Listing) -> str:
    parts = [part for part in (listing.settlement, listing.city, listing.county) if part]
    return ", ".join(parts) if parts else "Lokacija nije navedena"


def seller_label(legal_entity: bool) -> str:
    return "Agencija" if legal_entity else "Privatni prodavač"


def count_label(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} oglas"
    return f"{count} oglasa"


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
