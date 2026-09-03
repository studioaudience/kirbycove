#!/usr/bin/env python3
"""
Kirby Cove Campground availability watcher.

Polls recreation.gov's campground-availability API for Kirby Cove
(Golden Gate NRA, facility ID 232491) and emails / texts you the moment
a site opens up on one of the dates you care about.

--------------------------------------------------------------------
SETUP
--------------------------------------------------------------------
1. pip install requests

2. Fill in the CONFIG block below:
   - DATES_WANTED: the specific check-in dates you'd take (Kirby Cove
     is tent-only, 3-night-per-season limit, so most people watch a
     handful of Fri/Sat nights rather than a big range).
   - EMAIL_* settings for sending mail via Gmail SMTP (or your own
     provider).
   - SMS_TO_EMAIL: your phone number's email-to-SMS gateway address
     (see the carrier list below) if you want a text too. Leave blank
     to skip texting.

3. Run it:
     python3 kirby_cove_watcher.py
   It checks immediately, then every CHECK_INTERVAL_MINUTES, and keeps
   running until you stop it (Ctrl+C) or it finds something.

--------------------------------------------------------------------
CARRIER EMAIL-TO-SMS GATEWAYS (for SMS_TO_EMAIL)
--------------------------------------------------------------------
   AT&T:      10digitnumber@txt.att.net
   Verizon:   10digitnumber@vtext.com
   T-Mobile:  10digitnumber@tmomail.net
(Kirby Cove itself gets signal on all three, so any of these should
reach you while you're refreshing your phone in the canyon.)

--------------------------------------------------------------------
ON THE POLLING INTERVAL
--------------------------------------------------------------------
recreation.gov doesn't publish a rate limit for this endpoint, but the
common convention among camping-scanner scripts is every 5-15 minutes.
That's frequent enough to catch a cancellation same-day without
hammering the API or risking a block. This script defaults to 10.
"""

import os
import sys
import time
import argparse
import smtplib
import ssl
import logging
from datetime import date, datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

# ============================== CONFIG ===============================

CAMPGROUND_ID = "232491"  # Kirby Cove Campground, GGNRA
CAMPGROUND_NAME = "Kirby Cove Campground"

# Check-in dates you want (each date checked as a 1-night stay).
# Add as many as you like, format YYYY-MM-DD.
DATES_WANTED = [
    "2026-09-19",
    "2026-09-26",
    "2026-10-03",
]

# How often to poll, in minutes.
CHECK_INTERVAL_MINUTES = 10

# Stop after finding a hit? (True = alert once and exit; False = keep
# watching and re-alert on every check while sites remain open, useful
# since Kirby Cove availabilities can vanish within minutes)
STOP_ON_FIRST_HIT = True

# ---- Email (sender) settings — Gmail SMTP example ----
# Values are read from environment variables first (so you can use
# GitHub Actions / Render / etc. "Secrets" instead of hardcoding
# credentials in this file), falling back to the literals below for
# local testing.
EMAIL_ENABLED = True
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
EMAIL_FROM = os.environ.get("KIRBY_EMAIL_FROM", "your_email@gmail.com")
EMAIL_PASSWORD = os.environ.get("KIRBY_EMAIL_PASSWORD", "your_16_char_gmail_app_password")
EMAIL_TO = os.environ.get("KIRBY_EMAIL_TO", EMAIL_FROM)

# ---- SMS via carrier email-to-SMS gateway (optional) ----
SMS_ENABLED = True
SMS_TO_EMAIL = os.environ.get("KIRBY_SMS_TO_EMAIL", "5551234567@vtext.com")

# =======================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kirby-cove-watcher")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; personal-availability-check/1.0)",
    "Accept": "application/json",
}


def month_start(d: date) -> date:
    return d.replace(day=1)


def months_between(dates_wanted):
    """Return the distinct first-of-month dates covering all wanted dates."""
    months = set()
    for ds in dates_wanted:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        months.add(month_start(d))
    return sorted(months)


def fetch_month_availability(campground_id: str, month_date: date) -> dict:
    """Calls recreation.gov's month-availability endpoint for one month."""
    url = f"https://www.recreation.gov/api/camps/availability/campground/{campground_id}/month"
    params = {"start_date": month_date.strftime("%Y-%m-01T00:00:00.000Z")}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def find_open_sites(data: dict, dates_wanted):
    """
    Given the API's campsite-availability payload, return a dict of
    {date_str: [site_names]} for every wanted date that has >=1 site
    with status 'Available'.
    """
    hits = {}
    campsites = data.get("campsites", {})
    for site_id, site_info in campsites.items():
        site_name = site_info.get("site", site_id)
        availabilities = site_info.get("availabilities", {})
        for date_str, status in availabilities.items():
            # API returns keys like "2026-09-19T00:00:00Z"
            day = date_str[:10]
            if day in dates_wanted and status == "Available":
                hits.setdefault(day, []).append(site_name)
    return hits


def check_availability(dates_wanted):
    all_hits = {}
    for m in months_between(dates_wanted):
        try:
            data = fetch_month_availability(CAMPGROUND_ID, m)
        except requests.RequestException as e:
            log.warning(f"Request failed for {m.strftime('%Y-%m')}: {e}")
            continue
        hits = find_open_sites(data, dates_wanted)
        for day, sites in hits.items():
            all_hits.setdefault(day, []).extend(sites)
    return all_hits


def build_message(hits: dict) -> str:
    lines = [f"{CAMPGROUND_NAME} has openings:"]
    for day in sorted(hits):
        sites = ", ".join(sorted(set(hits[day])))
        lines.append(f"  {day}: site(s) {sites}")
    lines.append("\nBook now: https://www.recreation.gov/camping/campgrounds/232491")
    return "\n".join(lines)


def send_email(subject: str, body: str):
    if not EMAIL_ENABLED:
        return
    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        log.info("Email sent.")
    except Exception as e:
        log.error(f"Failed to send email: {e}")


def send_sms(body: str):
    if not SMS_ENABLED or not SMS_TO_EMAIL:
        return
    # SMS gateways want short plain text, no subject line clutter
    msg = MIMEText(body[:300])
    msg["From"] = EMAIL_FROM
    msg["To"] = SMS_TO_EMAIL
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, SMS_TO_EMAIL, msg.as_string())
        log.info("SMS sent.")
    except Exception as e:
        log.error(f"Failed to send SMS: {e}")


def notify(hits: dict):
    body = build_message(hits)
    log.info("Sending notifications:\n" + body)
    send_email(f"Kirby Cove opening found! ({len(hits)} date(s))", body)
    send_sms(body)


def test_notify():
    """
    Sends a dummy email/text right now, independent of any real
    availability data. Confirms your SMTP/App-Password/gateway config
    actually delivers, without waiting for a real opening.
    """
    log.info("Sending a TEST notification (no real availability involved)...")
    fake_hits = {"2099-01-01": ["TEST-SITE"]}
    body = build_message(fake_hits) + "\n\n(This is a test message — not a real opening.)"
    send_email("[TEST] Kirby Cove watcher notification test", body)
    send_sms(body)
    log.info("Done. Check your inbox/phone. If nothing arrived, check the "
              "error above, your App Password, and (for SMS) that the "
              "gateway address matches your carrier.")


def test_fetch():
    """
    Hits the real recreation.gov API for the current month and reports
    what it sees for EVERY date (not just DATES_WANTED), so you can
    confirm the request/parsing logic works end-to-end even if none of
    your wanted dates happen to be open right now.
    """
    today = date.today()
    log.info(f"Fetching real Kirby Cove data for {today.strftime('%Y-%m')} "
              f"(campground {CAMPGROUND_ID})...")
    try:
        data = fetch_month_availability(CAMPGROUND_ID, month_start(today))
    except requests.RequestException as e:
        log.error(f"API request failed: {e}")
        return

    campsites = data.get("campsites", {})
    log.info(f"API responded OK. {len(campsites)} site(s) returned for this campground.")

    any_available = {}
    for site_id, site_info in campsites.items():
        site_name = site_info.get("site", site_id)
        for date_str, status in site_info.get("availabilities", {}).items():
            if status == "Available":
                any_available.setdefault(date_str[:10], []).append(site_name)

    if any_available:
        log.info("Real availability found this month (regardless of your wanted dates):")
        for day in sorted(any_available):
            log.info(f"  {day}: {', '.join(sorted(set(any_available[day])))}")
    else:
        log.info("No 'Available' status found anywhere this month — that's normal for "
                  "Kirby Cove, it's usually booked solid. This still confirms the "
                  "request/parsing pipeline is working correctly.")

    # Also show whether your specific wanted dates are covered by this month's data
    hits = find_open_sites(data, DATES_WANTED)
    wanted_this_month = [d for d in DATES_WANTED if d.startswith(today.strftime("%Y-%m"))]
    if wanted_this_month:
        log.info(f"Your wanted dates in this month: {wanted_this_month} -> "
                  f"{'OPEN: ' + str(hits) if hits else 'none open right now'}")


def main():
    parser = argparse.ArgumentParser(description="Kirby Cove availability watcher")
    parser.add_argument("--test-notify", action="store_true",
                         help="Send a dummy email/SMS immediately, then exit.")
    parser.add_argument("--test-fetch", action="store_true",
                         help="Fetch real Kirby Cove data once and print what's available, then exit.")
    parser.add_argument("--once", action="store_true",
                         help="Run a single real check against DATES_WANTED and exit (no loop).")
    args = parser.parse_args()

    if args.test_notify:
        test_notify()
        return

    if args.test_fetch:
        test_fetch()
        return

    if args.once:
        hits = check_availability(DATES_WANTED)
        if hits:
            log.info(f"Found availability: {hits}")
            notify(hits)
        else:
            log.info("No openings found for your wanted dates right now.")
        return

    log.info(f"Watching Kirby Cove for: {', '.join(DATES_WANTED)}")
    log.info(f"Checking every {CHECK_INTERVAL_MINUTES} minute(s). Ctrl+C to stop.")
    while True:
        try:
            hits = check_availability(DATES_WANTED)
            if hits:
                log.info(f"Found availability: {hits}")
                notify(hits)
                if STOP_ON_FIRST_HIT:
                    log.info("STOP_ON_FIRST_HIT is True — exiting.")
                    break
            else:
                log.info("No openings yet.")
        except Exception as e:
            log.error(f"Unexpected error during check: {e}")

        time.sleep(CHECK_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
