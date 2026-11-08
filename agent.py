"""Fetch LinkedIn internships posted in the last 24h for configured locations
and email a digest.

Uses LinkedIn's public (logged-out) job search endpoint. No LinkedIn account
or API key is needed.

Environment variables for email (skip with --no-email):
  SMTP_USER      sender address, e.g. you@gmail.com
  SMTP_PASSWORD  SMTP password (for Gmail: an App Password)
  EMAIL_TO       recipient(s), comma separated (defaults to SMTP_USER)
  SMTP_HOST      default smtp.gmail.com
  SMTP_PORT      default 465 (SSL)
"""

from __future__ import annotations

import argparse
import csv
import html
import os
import random
import re
import smtplib
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGE_SIZE = 25
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Two ways LinkedIn marks internships; we query both and merge.
INTERNSHIP_FILTERS = [
    {"f_E": "1"},   # Experience level = Internship
    {"f_JT": "I"},  # Job type = Internship
]


@dataclass
class Job:
    job_id: str
    title: str
    company: str
    location: str
    posted: str
    url: str
    search: str


def fetch_page(session: requests.Session, params: dict, retries: int = 6) -> str | None:
    for attempt in range(retries):
        try:
            r = session.get(SEARCH_URL, params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"  network error: {e}", file=sys.stderr)
            r = None
        if r is not None and r.status_code == 200:
            return r.text
        if r is not None and r.status_code in (400, 404):
            return None  # past the last page
        code = r.status_code if r is not None else "n/a"
        # Rate limited: back off much longer so the IP cools down.
        base = 20 if code == 429 else 5
        wait = min(base * (2 ** attempt), 180) + random.uniform(0, 5)
        print(f"  HTTP {code} at start={params.get('start')}, retrying in {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)
    print(f"  gave up on start={params.get('start')} after {retries} tries", file=sys.stderr)
    return None


def parse_cards(page_html: str, country: str) -> list[Job]:
    soup = BeautifulSoup(page_html, "html.parser")
    jobs = []
    for li in soup.find_all("li"):
        card = li.find(attrs={"data-entity-urn": True}) or li.find(
            class_=re.compile(r"base-card|job-search-card")
        )
        if card is None:
            continue
        urn = card.get("data-entity-urn", "")
        link = li.find("a", class_=re.compile(r"base-card__full-link")) or li.find("a", href=True)
        url = link["href"].split("?")[0] if link else ""
        m = re.search(r"(\d{6,})", urn) or re.search(r"-(\d{6,})", url)
        if not m:
            continue

        def text(cls):
            el = li.find(class_=re.compile(cls))
            return " ".join(el.get_text().split()) if el else ""

        time_el = li.find("time")
        posted = (time_el.get("datetime") or time_el.get_text(strip=True)) if time_el else ""
        jobs.append(
            Job(
                job_id=m.group(1),
                title=text(r"base-search-card__title"),
                company=text(r"base-search-card__subtitle"),
                location=text(r"job-search-card__location"),
                posted=posted,
                url=url or f"https://www.linkedin.com/jobs/view/{m.group(1)}",
                search=country,
            )
        )
    return jobs


def search_country(session, country, keyword, seconds, cap, remote_only=False, label=None) -> list[Job]:
    found: dict[str, Job] = {}
    for filt in INTERNSHIP_FILTERS:
        start = 0
        while start < cap:
            params = {
                "keywords": keyword,
                "location": country,
                "f_TPR": f"r{seconds}",
                "start": start,
                **filt,
            }
            if remote_only:
                params["f_WT"] = "2"  # LinkedIn "Remote" workplace filter
            page = fetch_page(session, params)
            if not page:
                break
            jobs = parse_cards(page, label or country)
            if not jobs:
                break
            new = 0
            for j in jobs:
                if j.job_id not in found:
                    found[j.job_id] = j
                    new += 1
            if new == 0 and len(jobs) < PAGE_SIZE:
                break
            start += PAGE_SIZE
            time.sleep(random.uniform(3, 6))  # be polite
    return list(found.values())


def build_email_html(by_country: dict[str, list[Job]], date_str: str) -> str:
    total = sum(len(v) for v in by_country.values())
    parts = [
        "<div style='font-family:Arial,sans-serif;max-width:760px'>",
        f"<h2>LinkedIn internships, last 24h ({date_str})</h2>",
        f"<p><b>{total}</b> new postings: "
        + ", ".join(f"{c} {len(j)}" for c, j in by_country.items())
        + "</p>",
    ]
    for country, jobs in by_country.items():
        parts.append(f"<h3 style='border-bottom:1px solid #ccc'>{html.escape(country)} ({len(jobs)})</h3>")
        if not jobs:
            parts.append("<p style='color:#777'>No new internships.</p>")
            continue
        parts.append("<ul style='padding-left:18px'>")
        for j in sorted(jobs, key=lambda x: (x.company.lower(), x.title.lower())):
            parts.append(
                f"<li style='margin-bottom:6px'><a href='{html.escape(j.url)}'>{html.escape(j.title)}</a>"
                f" &mdash; <b>{html.escape(j.company)}</b>"
                f" <span style='color:#666'>· {html.escape(j.location)}</span></li>"
            )
        parts.append("</ul>")
    parts.append("</div>")
    return "\n".join(parts)


def _smtp_credentials() -> tuple[str, str, list[str]]:
    user = "".join(os.environ.get("SMTP_USER", "").split())
    # App Passwords are shown as "abcd efgh ijkl mnop"; copying can also pick up
    # non-breaking spaces or newlines. Remove all whitespace.
    password = "".join(os.environ.get("SMTP_PASSWORD", "").split())
    to_raw = os.environ.get("EMAIL_TO", "").strip() or user
    to = [a.strip() for a in to_raw.split(",") if a.strip()]

    problems = []
    if not user:
        problems.append("SMTP_USER secret is empty or missing")
    elif "@" not in user:
        problems.append("SMTP_USER doesn't look like an email address")
    if not password:
        problems.append("SMTP_PASSWORD secret is empty or missing")
    elif not password.isascii():
        problems.append("SMTP_PASSWORD contains non-ASCII characters (bad copy/paste?)")
    elif "gmail" in user.lower() and len(password) != 16:
        problems.append(
            f"SMTP_PASSWORD is {len(password)} characters; a Gmail App Password is 16 letters"
        )
    if problems:
        raise SystemExit("Email config problem:\n  - " + "\n  - ".join(problems))
    return user, password, to


def send_email(subject: str, body_html: str, attachment: Path | None = None) -> None:
    user, password, to = _smtp_credentials()
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    if attachment and attachment.exists():
        part = MIMEApplication(attachment.read_bytes(), Name=attachment.name)
        part["Content-Disposition"] = f'attachment; filename="{attachment.name}"'
        msg.attach(part)

    def via_ssl(p):
        with smtplib.SMTP_SSL(host, p, timeout=60) as s:
            s.login(user, password)
            s.sendmail(user, to, msg.as_string())

    def via_starttls(p):
        with smtplib.SMTP(host, p, timeout=60) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, password)
            s.sendmail(user, to, msg.as_string())

    attempts = [(via_ssl, port)] if port == 465 else [(via_starttls, port)]
    if host == "smtp.gmail.com":
        attempts.append((via_starttls, 587) if port == 465 else (via_ssl, 465))

    last = None
    for fn, p in attempts:
        try:
            fn(p)
            return
        except smtplib.SMTPAuthenticationError as e:
            raise SystemExit(
                f"Gmail rejected the login for {user}: {e.smtp_code} {e.smtp_error!r}.\n"
                "Use a 16-letter App Password (myaccount.google.com/apppasswords) for "
                "this same Gmail account, with 2-Step Verification on."
            )
        except (smtplib.SMTPException, OSError) as e:
            print(f"  SMTP via port {p} failed: {type(e).__name__}: {e}", file=sys.stderr)
            last = e
    raise SystemExit(f"Could not send email: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="output", help="folder for CSV + HTML results")
    ap.add_argument("--no-email", action="store_true", help="skip sending email")
    ap.add_argument("--test-email", action="store_true",
                    help="only send a short test email (no LinkedIn search)")
    args = ap.parse_args()

    if args.test_email:
        send_email("Internship agent: test email",
                   "<p>Your LinkedIn internship agent can send email. 🎉</p>")
        print("Test email sent.")
        return 0

    cfg = yaml.safe_load(Path(args.config).read_text())
    searches = cfg.get("searches") or [{"location": c} for c in cfg.get("countries", [])]
    keywords = cfg.get("keywords") or [""]
    seconds = int(cfg.get("posted_within_seconds", 86400))
    cap = int(cfg.get("max_results_per_country", 1000))

    session = requests.Session()
    by_country: dict[str, list[Job]] = {}
    seen: set[str] = set()  # list each job once, under the first search that found it
    for s in searches:
        loc = s["location"]
        remote = bool(s.get("remote_only", False))
        label = f"{loc} (remote)" if remote else loc
        merged: dict[str, Job] = {}
        for kw in keywords:
            print(f"Searching {label!r} keyword={kw!r} ...")
            for j in search_country(session, loc, kw, seconds, cap, remote, label):
                if j.job_id not in seen:
                    seen.add(j.job_id)
                    merged[j.job_id] = j
        by_country[label] = list(merged.values())
        print(f"  -> {len(merged)} internships")

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    all_jobs = [j for jobs in by_country.values() for j in jobs]
    csv_path = out / f"internships-{date_str}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(Job.__dataclass_fields__))
        w.writeheader()
        w.writerows(asdict(j) for j in all_jobs)
    body = build_email_html(by_country, date_str)
    (out / f"internships-{date_str}.html").write_text(body, encoding="utf-8")
    print(f"Saved {len(all_jobs)} jobs to {out}/")

    if not args.no_email:
        send_email(f"{len(all_jobs)} new internships ({date_str})", body, attachment=csv_path)
        print("Email sent.")

    if not all_jobs:
        print("WARNING: 0 results. LinkedIn may be rate-limiting or changed its HTML.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
