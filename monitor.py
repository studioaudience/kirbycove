#!/usr/bin/env python3
"""Monitor Recreation.gov for overnight openings at Kirby Cove."""

from __future__ import annotations

import argparse
import base64
import json
import os
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


FACILITY_ID = "232491"
FACILITY_NAME = "Kirby Cove Campground"
BOOKING_URL = f"https://www.recreation.gov/camping/campgrounds/{FACILITY_ID}"
API_URL = (
    "https://www.recreation.gov/api/camps/availability/campground/"
    f"{FACILITY_ID}/month"
)
DEFAULT_STATE_PATH = Path("state.json")
AVAILABLE_STATUS = "Available"


@dataclass(frozen=True, order=True)
class Opening:
    night: date
    site: str
    campsite_id: str

    @property
    def key(self) -> str:
        return f"{self.night.isoformat()}|{self.campsite_id}"

    @classmethod
    def from_key(cls, value: str) -> "Opening":
        night_text, campsite_id = value.split("|", 1)
        return cls(date.fromisoformat(night_text), "", campsite_id)


def add_months(day: date, months: int) -> date:
    """Add whole calendar months, clamping to the target month's final day."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    first_after = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (first_after - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


def month_starts(start: date, end: date) -> list[date]:
    current = start.replace(day=1)
    result: list[date] = []
    while current <= end:
        result.append(current)
        current = add_months(current, 1)
    return result


def fetch_json(url: str, *, timeout: int = 25) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "kirby-cove-availability-monitor/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read Recreation.gov availability: {error}") from error


def fetch_openings(today: date) -> list[Opening]:
    # Recreation.gov currently opens Kirby Cove reservations six months ahead
    # and may allow a reservation to extend two additional days.
    final_day = add_months(today, 6) + timedelta(days=2)
    openings: dict[str, Opening] = {}

    for month in month_starts(today, final_day):
        start = f"{month.isoformat()}T00:00:00.000Z"
        url = f"{API_URL}?{urllib.parse.urlencode({'start_date': start})}"
        payload = fetch_json(url)
        campsites = payload.get("campsites")
        if not isinstance(campsites, dict):
            raise RuntimeError("Recreation.gov returned an unexpected response (no campsites map)")

        for campsite_id, campsite in campsites.items():
            # This deliberately excludes Kirby Cove's separate Day Use site.
            if str(campsite.get("type_of_use", "")).lower() != "overnight":
                continue
            site = str(campsite.get("site", campsite_id))
            availabilities = campsite.get("availabilities", {})
            if not isinstance(availabilities, dict):
                continue
            for timestamp, status in availabilities.items():
                try:
                    night = date.fromisoformat(str(timestamp)[:10])
                except ValueError:
                    continue
                if today <= night <= final_day and status == AVAILABLE_STATUS:
                    opening = Opening(night=night, site=site, campsite_id=str(campsite_id))
                    openings[opening.key] = opening

    return sorted(openings.values())


def load_previous_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("available", [])
        if not isinstance(values, list):
            raise ValueError("available must be a list")
        return {str(value) for value in values}
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read {path}: {error}") from error


def save_state(path: Path, openings: list[Opening]) -> None:
    payload = {
        "facility_id": FACILITY_ID,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "available": [opening.key for opening in openings],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def format_messages(openings: list[Opening]) -> tuple[str, str, str]:
    subject = f"Kirby Cove opening: {len(openings)} new campsite night(s)"
    lines = [
        f"New overnight availability at {FACILITY_NAME}:",
        "",
        *[f"- {opening.night:%a, %b %-d, %Y}: site {opening.site}" for opening in openings],
        "",
        f"Book now: {BOOKING_URL}",
        "Availability can disappear quickly and is not held by this alert.",
    ]
    email_body = "\n".join(lines)

    sms_entries = [f"{opening.night:%b %-d} site {opening.site}" for opening in openings]
    sms_body = "Kirby Cove OPEN: " + "; ".join(sms_entries) + f". Book: {BOOKING_URL}"
    if len(sms_body) > 1500:
        shown = "; ".join(sms_entries[:25])
        sms_body = (
            f"Kirby Cove OPEN ({len(openings)} nights): {shown}; and more. "
            f"Book: {BOOKING_URL}"
        )
    return subject, email_body, sms_body


def require_env(names: list[str]) -> dict[str, str]:
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    return values


def send_email(subject: str, body: str) -> None:
    env = require_env(["GMAIL_USERNAME", "GMAIL_APP_PASSWORD", "ALERT_EMAIL_TO"])
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = env["GMAIL_USERNAME"]
    message["To"] = env["ALERT_EMAIL_TO"]
    message.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(env["GMAIL_USERNAME"], env["GMAIL_APP_PASSWORD"])
        smtp.send_message(message)


def send_sms(body: str) -> None:
    env = require_env(
        [
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_FROM_NUMBER",
            "ALERT_PHONE_TO",
        ]
    )
    sid = env["TWILIO_ACCOUNT_SID"]
    token = env["TWILIO_AUTH_TOKEN"]
    form = urllib.parse.urlencode(
        {
            "From": env["TWILIO_FROM_NUMBER"],
            "To": env["ALERT_PHONE_TO"],
            "Body": body,
        }
    ).encode("utf-8")
    authorization = base64.b64encode(f"{sid}:{token}".encode()).decode()
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=form,
        headers={
            "Authorization": f"Basic {authorization}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"Twilio returned HTTP {response.status}")
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Twilio rejected the SMS (HTTP {error.code}): {details}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not contact Twilio: {error}") from error


def notify(subject: str, email_body: str, sms_body: str) -> None:
    errors: list[str] = []
    try:
        send_email(subject, email_body)
        print("Email alert sent.")
    except Exception as error:  # Report both channel failures in one run.
        errors.append(f"email: {error}")
    try:
        send_sms(sms_body)
        print("SMS alert sent.")
    except Exception as error:
        errors.append(f"SMS: {error}")
    if errors:
        raise RuntimeError("Notification failure(s): " + " | ".join(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check and print openings without sending alerts or changing state",
    )
    parser.add_argument(
        "--test-notifications",
        action="store_true",
        help="send a sample email and SMS without checking availability",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.test_notifications:
        sample = [Opening(date.today() + timedelta(days=7), "TEST", "test")]
        notify(*format_messages(sample))
        return 0

    local_timezone = ZoneInfo(os.environ.get("TIMEZONE", "America/Los_Angeles"))
    today = datetime.now(local_timezone).date()
    openings = fetch_openings(today)
    previous_keys = load_previous_keys(args.state)
    new_openings = [opening for opening in openings if opening.key not in previous_keys]

    print(f"Checked {FACILITY_NAME}: {len(openings)} available overnight site-night(s).")
    if args.dry_run:
        for opening in openings:
            print(f"{opening.night.isoformat()} site {opening.site} ({opening.campsite_id})")
        return 0

    # Save only after notifications succeed; failed alerts will be retried next run.
    if new_openings:
        print(f"Found {len(new_openings)} newly available site-night(s).")
        notify(*format_messages(new_openings))
    else:
        print("No new openings; no alert sent.")
    current_keys = {opening.key for opening in openings}
    if current_keys != previous_keys:
        save_state(args.state, openings)
        print("Availability state updated.")
    else:
        print("Availability state unchanged.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
