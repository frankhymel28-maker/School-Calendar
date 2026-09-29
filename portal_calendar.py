#!/usr/bin/env python3
"""
Reads the "Upcoming Events" table from a PlusPortals parent page and writes
an .ics calendar feed that Google Calendar and Apple Calendar can subscribe to.

Environment variables (keep your password out of the file):
    PORTAL_LOGIN_URL   the page where you sign in (the school's PlusPortals login)
    PORTAL_HOME_URL    the page that shows "Upcoming Events" after login
    PORTAL_USER        your parent username
    PORTAL_PASS        your parent password
    PORTAL_USER_FIELD  name of the username form field (default: username)
    PORTAL_PASS_FIELD  name of the password form field (default: password)
    OUTPUT_ICS         where to write the feed (default: school_events.ics)
"""
import os
import re
import sys
import hashlib
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

# Grade -> child label. Events that mention no grade are labeled "All".
GRADE_LABELS = {"3rd": "Fisher", "5th": "Spencer", "7th": "Tucker"}
ALARM_HOUR = 19  # 7 PM the evening before


def fetch_html():
    import requests

    session = requests.Session()
    login_url = os.environ["PORTAL_LOGIN_URL"]
    data = {
        os.environ.get("PORTAL_USER_FIELD", "username"): os.environ["PORTAL_USER"],
        os.environ.get("PORTAL_PASS_FIELD", "password"): os.environ["PORTAL_PASS"],
    }
    resp = session.post(login_url, data=data, timeout=30)
    resp.raise_for_status()
    home = session.get(os.environ["PORTAL_HOME_URL"], timeout=30)
    home.raise_for_status()
    return home.text


def parse_events(html):
    """Return a list of (title, date) from the Upcoming Events table."""
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        title = cells[0].get_text(" ", strip=True)
        date_text = cells[1].get_text(strip=True)
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", date_text)
        if not title or not m:
            continue
        month, day, year = map(int, m.groups())
        events.append((title, datetime(year, month, day).date()))
    return events


def label_for(title):
    labels = []
    for grade, name in GRADE_LABELS.items():
        if re.search(rf"\b{grade}\b", title, re.I):
            labels.append(name)
    return " & ".join(labels) if labels else "All"


def esc(text):
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def build_ics(events):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//School Events Sync//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:School Events",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for title, day in events:
        label = label_for(title)
        summary = f"[{label}] {title}"
        # Stable UID so updates replace events instead of duplicating them
        uid = hashlib.sha1(f"{title}|{day.isoformat()}".encode()).hexdigest() + "@school-sync"
        end = day + timedelta(days=1)
        # All-day event starts at midnight; alarm fires 7 PM the day before
        minutes_before = (24 - ALARM_HOUR) * 60
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
            f"SUMMARY:{esc(summary)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{esc('Tomorrow: ' + summary)}",
            f"TRIGGER:-PT{minutes_before}M",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    if len(sys.argv) > 1:  # optional: parse a saved HTML file for testing
        html = open(sys.argv[1], encoding="utf-8").read()
    else:
        html = fetch_html()
    events = parse_events(html)
    if not events:
        sys.exit("No events found - the page layout may have changed.")
    out = os.environ.get("OUTPUT_ICS", "school_events.ics")
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(events))
    print(f"Wrote {len(events)} events to {out}")


if __name__ == "__main__":
    main()
