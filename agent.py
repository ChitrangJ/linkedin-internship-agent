"""Fetch recent LinkedIn internship / part-time / contract roles in chosen
domains and locations, filter them, and email a digest.

Uses LinkedIn's public (logged-out) job pages. No LinkedIn account or API key.
LinkedIn ignores its experience-level, job-type and remote filters for
logged-out searches, so this script checks every candidate job itself:

  1. Search: (role words) AND (domain words), per location and domain.
  2. Title check: must match a domain; senior / non-office titles dropped.
  3. Job page: LinkedIn "Employment type" must be an allowed type.
  4. Description: drop jobs requiring >= max_years_experience years.
  5. remote_only locations: title/location/description must say remote.

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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

import extract

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
INTERN_TITLE_RE = re.compile(
    r"intern|stage|stagiaire|praktik|werkstudent|working student|stagiair|alternan|trainee",
    re.I,
)


@dataclass
class Job:
    job_id: str
    title: str
    company: str
    location: str
    posted: str
    url: str
    search: str
    domains: list[str] = field(default_factory=list)
    employment_type: str = ""
    seniority: str = ""
    min_years: int | None = None
    remote: bool = False
    posted_ago: str = ""
    minutes_ago: float = 10 ** 6
    start_date: str = ""
    requirements: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get(session: requests.Session, url: str, params: dict | None = None,
             retries: int = 6) -> str | None:
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"  network error: {e}", file=sys.stderr)
            r = None
        if r is not None and r.status_code == 200:
            return r.text
        if r is not None and r.status_code in (400, 404, 410):
            return None
        code = r.status_code if r is not None else "n/a"
        base = 10 if code == 429 else 5
        wait = min(base * (2 ** attempt), 180) + random.uniform(0, 4)
        print(f"  HTTP {code}, retrying in {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)
    print(f"  gave up on {url} {params or ''}", file=sys.stderr)
    return None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _txt(el) -> str:
    return " ".join(el.get_text(" ").split()) if el else ""


def parse_cards(page_html: str, search_label: str) -> list[Job]:
    soup = BeautifulSoup(page_html, "html.parser")
    jobs = []
    for li in soup.find_all("li"):
        card = li.find(attrs={"data-entity-urn": True})
        link = li.find("a", class_=re.compile(r"base-card__full-link")) or li.find("a", href=True)
        url = link["href"].split("?")[0] if link else ""
        urn = card.get("data-entity-urn", "") if card else ""
        m = re.search(r"(\d{6,})", urn) or re.search(r"-(\d{6,})(?:$|/)", url)
        if not m:
            continue
        time_el = li.find("time")
        jobs.append(Job(
            job_id=m.group(1),
            title=_txt(li.find(class_=re.compile(r"base-search-card__title"))),
            company=_txt(li.find(class_=re.compile(r"base-search-card__subtitle"))),
            location=_txt(li.find(class_=re.compile(r"job-search-card__location"))),
            posted=(time_el.get("datetime") or "") if time_el else "",
            posted_ago=_txt(time_el) if time_el else "",
            minutes_ago=extract.minutes_ago(_txt(time_el)) if time_el else 10 ** 6,
            url=url or f"https://www.linkedin.com/jobs/view/{m.group(1)}",
            search=search_label,
        ))
    return jobs


def parse_detail(page_html: str) -> tuple[dict[str, str], str]:
    """Return ({criterion: value}, description text)."""
    soup = BeautifulSoup(page_html, "html.parser")
    criteria = {}
    for item in soup.select(".description__job-criteria-item"):
        k, v = item.find("h3"), item.find("span")
        if k and v:
            criteria[_txt(k)] = _txt(v)
    desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    return criteria, _txt(desc)


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------
def term_regex(terms: list[str]) -> re.Pattern:
    """Short terms (<=3 chars) must be whole words; longer ones match anywhere.
    A term starting with "re:" is used as a raw regular expression."""
    parts = []
    for t in terms:
        if t.startswith("re:"):
            parts.append(t[3:])
            continue
        t = t.strip().lower()
        if not t:
            continue
        e = re.escape(t)
        parts.append(rf"(?<![a-z0-9]){e}(?![a-z0-9])" if len(t) <= 3 else e)
    return re.compile("|".join(parts) or r"(?!x)x", re.I)


NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15,
    "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8, "dix": 10,
    "ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7,
    "acht": 8, "zehn": 10,
    "een": 1, "twee": 2, "drie": 3, "vijf": 5, "zes": 6, "zeven": 7, "tien": 10,
}
_NUMW_RE = re.compile(r"\b(" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r")\b")
_YEARS_RE = re.compile(
    r"(?<![\d+])(\d{1,2})\s*(?:\+|plus)?\s*"
    r"(?:(?:-|–|—|to|à|a|bis|tot|until)\s*(\d{1,2})\s*\+?\s*)?"
    r"(?:years?|yrs?|ans|années|année|jahre|jahren|jahr|jaar|jaren)\b"
)
_EXP_WORDS = re.compile(
    r"experience|expérience|experiencia|esperienza|erfahrung|ervaring|"
    r"track record|background in|working in|work experience|in a similar|in a .{0,20}role"
)
_MIN_WORDS = re.compile(
    r"at least|minimum|min\.|mindestens|au moins|minimaal|minstens"
)


def required_years(description: str) -> int | None:
    """Smallest 'N years of experience' requirement mentioned, or None.

    For a range like '3-5 years' the lower bound counts. Several mentions →
    the largest lower bound (the job's strictest requirement).
    """
    text = description.lower()
    text = text.replace("ten minste", "minimaal")
    text = re.sub(r"bac\s*\+\s*\d", " ", text)  # French degree level, not years
    text = _NUMW_RE.sub(lambda m: str(NUM_WORDS[m.group(1)]), text)
    found = []
    for m in _YEARS_RE.finditer(text):
        lo = int(m.group(1))
        if lo == 0 or lo > 25:
            continue
        before = text[max(0, m.start() - 60): m.start()]
        before = re.split(r"[.;!?\n•]", before)[-1]           # same clause only
        after = re.split(r"[.;!?\n•]", text[m.end(): m.end() + 70])[0]
        exp_before = re.search(
            r"(experience|expérience|erfahrung|berufserfahrung|ervaring)\s*"
            r":?\s*(of|von|de|d'|van)?\s*(at least|minimum|min\.|mindestens|au moins|minimaal|minstens)?\s*$",
            before)
        if extract._MAX_WORDS.search(before):
            continue  # "max 4 years" is a ceiling, not a requirement
        if _EXP_WORDS.search(after) or exp_before or _MIN_WORDS.search(before[-25:]):
            if re.search(r"years? old|ans? et plus|jahre alt|jaar oud|founded|gegründet|fondée|since|seit|depuis", before[-25:] + after[:25]):
                continue
            found.append(lo)
    return max(found) if found else None


def is_remote(job: Job, description: str, remote_re: re.Pattern) -> bool:
    head = f"{job.title} {job.location}".lower()
    if re.search(r"remote|home ?office|homeoffice|télétravail|thuiswerk", head):
        return True
    return bool(remote_re.search(description.lower()))


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
def quote(term: str) -> str:
    return f'"{term}"' if " " in term or "-" in term else term


def build_query(role_terms: list[str], domain_terms: list[str], remote: bool) -> str:
    q = "(" + " OR ".join(quote(t) for t in role_terms) + ") AND (" + \
        " OR ".join(quote(t) for t in domain_terms) + ")"
    if remote:
        q += ' AND (remote OR homeoffice OR "home office" OR ortsunabhängig)'
    return q


def search(session, location: str, keywords: str, seconds: int, max_pages: int,
           label: str) -> list[Job]:
    found: dict[str, Job] = {}
    start = 0
    for _ in range(max_pages):
        page = http_get(session, SEARCH_URL, {
            "keywords": keywords, "location": location,
            "f_TPR": f"r{seconds}", "start": start,
        })
        if not page:
            break
        jobs = parse_cards(page, label)
        if not jobs:
            break
        new = [j for j in jobs if j.job_id not in found]
        for j in new:
            found[j.job_id] = j
        if not new:
            break
        start += len(jobs)  # LinkedIn returns ~10 per page
        time.sleep(random.uniform(2, 4))
    return list(found.values())


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
_EMAIL_CSS = (
    "<style>"
    "body,.w{font-family:Arial,sans-serif}"
    ".w{max-width:780px}"
    "li{margin-bottom:12px}"
    ".t{font-size:15px;font-weight:bold}"
    ".m{color:#555}"
    ".s{background:#fff3b0;padding:1px 6px;border-radius:3px;font-weight:bold}"
    ".n{color:#999}"
    ".r{color:#a33}"
    "h3{border-bottom:1px solid #ccc;padding-bottom:4px}"
    "</style>"
)


def _job_html(j: Job) -> str:
    e = html.escape
    meta = [e(j.company), e(j.location), e(j.employment_type)]
    if j.remote:
        meta.append("Remote")
    if j.posted_ago:
        meta.append(f"posted {e(j.posted_ago)}")
    meta.append(f"<i>{e(', '.join(j.domains) or 'Others')}</i>")
    start = (f"<span class=s>📅 Start: {e(j.start_date)}</span>" if j.start_date
             else "<span class=n>📅 Start: n/a</span>")
    reqs = ("<br><span class=r>⚠️ <b>Requirements:</b> " + " · ".join(e(r) for r in j.requirements) + "</span>"
            if j.requirements else "")
    link = f"https://www.linkedin.com/jobs/view/{j.job_id}"  # short form of the posting URL
    return (f"<li><a class=t href={link}>{e(j.title)}</a><br>"
            f"<span class=m>{' · '.join(meta)}</span><br>{start}{reqs}</li>")


def build_email_html(by_loc: dict[str, list[Job]], when: str, hours: int, stats: dict) -> str:
    total = sum(len(v) for v in by_loc.values())
    p = [
        f"<html><head><meta charset=utf-8>{_EMAIL_CSS}</head><body><div class=w>",
        f"<h2 style='margin-bottom:4px'>{total} internships / part-time / contract roles</h2>",
        f"<p class=m style='margin-top:0'>Posted in the last {hours}h · {html.escape(when)}<br>"
        "Sorted by location, then newest first, then domain.</p>",
        "<p>" + " · ".join(f"{html.escape(k)}: <b>{len(v)}</b>" for k, v in by_loc.items()) + "</p>",
    ]
    for loc, jobs in by_loc.items():
        p.append(f"<h3>{html.escape(loc)} ({len(jobs)})</h3>")
        if not jobs:
            p.append("<p class=n>Nothing new.</p>")
            continue
        p.append("<ul style='padding-left:18px'>")
        p.extend(_job_html(j) for j in jobs)  # already sorted: newest first, then domain
        p.append("</ul>")
    p.append(
        "<p class=n style='font-size:12px'>Start dates and requirements are read automatically from the "
        "job description; check the posting before applying. Checked "
        f"{stats.get('candidates', 0)} candidates; dropped {stats.get('wrong_type', 0)} full-time, "
        f"{stats.get('too_senior', 0)} needing {stats.get('max_years')}+ years, "
        f"{stats.get('not_remote', 0)} not remote. Full list attached as CSV.</p></div></body></html>"
    )
    return "\n".join(p)


def _smtp_credentials() -> tuple[str, str, list[str]]:
    user = "".join(os.environ.get("SMTP_USER", "").split())
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
        problems.append(f"SMTP_PASSWORD is {len(password)} characters; a Gmail App Password is 16 letters")
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
            s.ehlo(); s.starttls(); s.ehlo()
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


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="output", help="folder for CSV + HTML results")
    ap.add_argument("--no-email", action="store_true", help="skip sending email")
    ap.add_argument("--test-email", action="store_true",
                    help="only send a short test email (no LinkedIn search)")
    ap.add_argument("--only", help="comma-separated locations to run (for testing)")
    args = ap.parse_args()

    if args.test_email:
        send_email("Internship agent: test email",
                   "<p>Your LinkedIn internship agent can send email. 🎉</p>")
        print("Test email sent.")
        return 0

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    locations = cfg["locations"]
    if args.only:
        keep = {x.strip().lower() for x in args.only.split(",")}
        locations = [l for l in locations if l["location"].lower() in keep]
    seconds = int(cfg.get("posted_within_seconds", 43200))
    allowed_types = {t.lower() for t in cfg.get("allowed_employment_types", ["Internship"])}
    max_years = int(cfg.get("max_years_experience", 5))
    role_terms = cfg["role_search_terms"]
    domains = cfg["domains"]
    domain_title_re = {name: term_regex(d["title"]) for name, d in domains.items()}
    order = list(domains)  # config order = email sort order; untagged jobs go last ("Others")

    def domain_rank(job: Job) -> int:
        return min((order.index(d) for d in job.domains if d in order), default=len(order))
    exclude_re = term_regex(cfg.get("exclude_title_terms", []))
    remote_re = term_regex(cfg.get("remote_description_terms", []))
    max_pages = int(cfg.get("max_pages_per_search", 30))
    marker_re = term_regex(cfg.get("role_title_markers", [])) if cfg.get("role_title_markers") else INTERN_TITLE_RE
    weak_re = term_regex(cfg.get("role_title_markers_need_label", []))
    check_unmarked = bool(cfg.get("check_titles_without_marker", False))

    session = requests.Session()
    stats = {"candidates": 0, "no_role_word": 0, "wrong_type": 0, "too_senior": 0,
             "not_remote": 0, "max_years": max_years}
    seen: set[str] = set()
    by_loc: dict[str, list[Job]] = {}

    for loc_cfg in locations:
        loc = loc_cfg["location"]
        remote_only = bool(loc_cfg.get("remote_only", False))
        label = f"{loc} (remote)" if remote_only else loc

        # 1. search each domain, 2. title filter
        candidates: dict[str, Job] = {}
        for dname, d in domains.items():
            q = build_query(role_terms, d["search"], remote_only)
            results = search(session, loc, q, seconds, max_pages, label)
            kept = 0
            for j in results:
                if j.job_id in seen:
                    continue
                if exclude_re.search(j.title):
                    continue
                tags = [n for n, rx in domain_title_re.items() if rx.search(j.title)]
                if not tags:
                    continue
                if j.job_id in candidates:
                    continue
                if not check_unmarked and not (marker_re.search(j.title) or weak_re.search(j.title)):
                    seen.add(j.job_id)
                    stats["no_role_word"] += 1
                    continue
                j.domains = tags  # already in config (sort) order
                candidates[j.job_id] = j
                kept += 1
            print(f"[{label}] {dname}: {len(results)} results, {kept} new title matches", flush=True)

        # 3-5. job page checks
        accepted = []
        for j in candidates.values():
            seen.add(j.job_id)
            stats["candidates"] += 1
            page = http_get(session, DETAIL_URL.format(j.job_id))
            time.sleep(random.uniform(1, 2.5))
            if not page:
                continue
            criteria, desc = parse_detail(page)
            j.employment_type = criteria.get("Employment type", "")
            j.seniority = criteria.get("Seniority level", "")
            etype = j.employment_type.lower()
            if marker_re.search(j.title):
                if etype not in allowed_types:
                    # Title says intern/Werkstudent/part-time; trust it over the label.
                    t = j.title.lower()
                    kind = ("Internship" if INTERN_TITLE_RE.search(t) else
                            "Part-time" if re.search(r"part[- ]?time|teilzeit|deeltijd|temps partiel|minijob|aushilfe", t) else
                            "Freelance" if "freelanc" in t else "Internship / part-time")
                    j.employment_type = f"{kind} (LinkedIn says {j.employment_type or 'n/a'})"
            elif etype not in allowed_types:
                stats["wrong_type"] += 1
                continue
            j.min_years = required_years(desc)
            if j.min_years is not None and j.min_years >= max_years:
                stats["too_senior"] += 1
                continue
            j.remote = is_remote(j, desc, remote_re)
            if remote_only and not j.remote:
                stats["not_remote"] += 1
                continue
            try:
                j.start_date = extract.start_date(j.title, desc)
            except Exception as e:  # never let a parsing edge case kill the run
                print(f"  start-date parse failed for {j.job_id}: {e}", file=sys.stderr)
            try:
                j.requirements = extract.requirements(desc, _YEARS_RE, _EXP_WORDS)
            except Exception as e:
                print(f"  requirements parse failed for {j.job_id}: {e}", file=sys.stderr)
            accepted.append(j)
        # Same job posted twice under different IDs -> keep one.
        uniq: dict[tuple, Job] = {}
        for j in accepted:
            key = (re.sub(r"\W+", " ", j.title.lower()).strip(), j.company.lower().strip())
            uniq.setdefault(key, j)
        accepted = list(uniq.values())
        accepted.sort(key=lambda x: (x.minutes_ago, domain_rank(x)))
        by_loc[label] = accepted
        print(f"[{label}] {len(candidates)} candidates -> {len(accepted)} kept", flush=True)

    now = datetime.now(timezone.utc)
    when = now.strftime("%Y-%m-%d %H:%M UTC")
    stamp = now.strftime("%Y-%m-%d-%H%M")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    all_jobs = [j for jobs in by_loc.values() for j in jobs]
    csv_path = out / f"internships-{stamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(Job.__dataclass_fields__))
        w.writeheader()
        for j in all_jobs:
            row = asdict(j)
            row["domains"] = "; ".join(j.domains)
            row["requirements"] = "; ".join(j.requirements)
            w.writerow(row)
    body = build_email_html(by_loc, when, round(seconds / 3600), stats)
    (out / f"internships-{stamp}.html").write_text(body, encoding="utf-8")
    print(f"Saved {len(all_jobs)} jobs to {out}/  stats={stats}")

    if not args.no_email:
        prefix = f"[Preview: {args.only}] " if args.only else ""
        send_email(f"{prefix}{len(all_jobs)} new internships & part-time roles ({when})", body,
                   attachment=csv_path)
        print("Email sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
