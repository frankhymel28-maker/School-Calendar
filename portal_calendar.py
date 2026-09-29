#!/usr/bin/env python3
"""
Reads the "Upcoming Events" table from a PlusPortals parent page and writes
an .ics calendar feed that Google Calendar and Apple Calendar can subscribe to.

Environment variables (keep your password out of the file):
    PORTAL_LOGIN_URL   the page where you sign in
    PORTAL_HOME_URL    the page that shows "Upcoming Events" after login
    PORTAL_USER        your parent username
    PORTAL_PASS        your parent password
    PORTAL_USER_FIELD  (optional) name of the username form field
    PORTAL_PASS_FIELD  (optional) name of the password form field
    OUTPUT_ICS         where to write the feed (default: school_events.ics)
"""
import os
import re
import sys
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# Grade -> child label. Events that mention no grade are labeled "All".
GRADE_LABELS = {"3rd": "Fisher", "5th": "Spencer", "7th": "Tucker"}
ALARM_HOUR = 19  # 7 PM the evening before

SKIP_TYPES = {"submit", "button", "image", "reset", "file"}


def build_login_payload(form, user, password):
    """Fill in a login form: keep hidden fields, set username and password."""
    data = {}
    user_field = os.environ.get("PORTAL_USER_FIELD")
    pass_field = os.environ.get("PORTAL_PASS_FIELD")
    user_set = False
    pass_set = False
    described = []
    for inp in form.find_all(["input", "select", "textarea"]):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in SKIP_TYPES:
            continue
        described.append(f"{name}({itype})")
        if itype in ("checkbox", "radio"):
            if inp.has_attr("checked"):
                data[name] = inp.get("value", "on")
            continue
        if name == pass_field or (itype == "password" and not pass_set and not pass_field):
            data[name] = password
            pass_set = True
        elif name == user_field or (
            itype in ("text", "email") and not user_set and not user_field
        ):
            data[name] = user
            user_set = True
        else:
            data[name] = inp.get("value", "")
    print("DEBUG: login form fields:", ", ".join(described))
    return data


def find_login_form(soup):
    for form in soup.find_all("form"):
        if form.find("input", {"type": "password"}):
            return form
    return None


def describe_page(html, label):
    """Print safe facts about a page (no event names, no personal data)."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    print(f"DEBUG [{label}]: title={title!r}")
    print(f"DEBUG [{label}]: has 'Upcoming Events' text: {'Upcoming Events' in html}")
    print(f"DEBUG [{label}]: has password box (looks like login page): "
          f"{bool(soup.find('input', {'type': 'password'}))}")
    print(f"DEBUG [{label}]: tables={len(soup.find_all('table'))}, "
          f"iframes={len(soup.find_all('iframe'))}")


def fetch_html():
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (school-calendar-sync)"
    login_url = os.environ["PORTAL_LOGIN_URL"].strip()
    home_url = os.environ["PORTAL_HOME_URL"].strip()
    user = os.environ["PORTAL_USER"].strip()
    password = os.environ["PORTAL_PASS"]

    page = session.get(login_url, timeout=30)
    print(f"DEBUG: login page HTTP status {page.status_code}")
    soup = BeautifulSoup(page.text, "html.parser")
    form = find_login_form(soup)
    if form is None:
        describe_page(page.text, "login page")
        sys.exit("Could not find a sign-in form on the login page.")

    data = build_login_payload(form, user, password)
    action = urljoin(login_url, form.get("action") or "")
    method = (form.get("method") or "post").lower()
    if method == "get":
        resp = session.get(action, params=data, timeout=30)
    else:
        resp = session.post(action, data=data, timeout=30)
    print(f"DEBUG: sign-in HTTP status {resp.status_code}")

    home = session.get(home_url, timeout=30)
    print(f"DEBUG: events page HTTP status {home.status_code}")
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
        describe_page(html, "events page")
        sys.exit("No events found - the page layout may have changed.")
    out = os.environ.get("OUTPUT_ICS", "school_events.ics")
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(events))
    print(f"Wrote {len(events)} events to {out}")


if __name__ == "__main__":
    main()
