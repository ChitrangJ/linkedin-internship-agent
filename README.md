# LinkedIn Internship Agent

Twice a day (**6 AM and 6 PM IST**) emails you new LinkedIn roles posted in the
**last 12 hours** that are:

- **Internship, part-time or contract** (LinkedIn's "Employment type", checked on each job page)
- **Not requiring 5+ years of experience** (read from the job description, EN/FR/DE/NL)
- In these **domains** (job title must match): Supply Chain & Operations, Data &
  Analytics, Product Management, Project Management, Strategy & Consulting,
  Marketing & Sales, HR, Finance
- In these **locations**: Lyon, Paris, Luxembourg, Belgium, Netherlands,
  Switzerland (all roles) and Germany (remote roles only)

Each email is sorted by **location** (order above), then **newest posting first**,
then **domain** (order above). Every job shows a highlighted **📅 start date** and
**⚠️ mandatory requirements** (languages and level, degree such as Bac+5, years of
experience, enrolment / convention de stage, work permit), read from the job
description.

It uses LinkedIn's public (logged-out) job pages; no LinkedIn account or API key.
LinkedIn ignores its own experience-level, job-type and remote filters for
logged-out searches, so the agent checks each job itself.

## Setup

1. **Gmail App Password** for the sending account: Google Account → Security →
   2-Step Verification on → https://myaccount.google.com/apppasswords → create one (16 letters).
2. **Repo secrets**: Settings → Secrets and variables → Actions → *Repository secrets*:
   | Secret          | Value                                                       |
   |-----------------|-------------------------------------------------------------|
   | `SMTP_USER`     | the sending Gmail address                                    |
   | `SMTP_PASSWORD` | the App Password                                             |
   | `EMAIL_TO`      | recipients, comma separated: `a@x.com, b@y.com`              |
3. **Test**: Actions → *Daily LinkedIn internships* → *Run workflow*. Tick
   *Only send a test email* to check email settings in under a minute.

Each run's CSV and HTML are also kept as a workflow artifact for 30 days.

## Customize (`config.yaml`)

- `locations`: any LinkedIn location (their order = email order); `remote_only: true` keeps only remote roles
- `allowed_employment_types`: e.g. add `Temporary`
- `max_years_experience`: drop jobs requiring this many years or more
- `domains`: search terms and title words per domain (their order = email sort order)
- `exclude_title_terms`: titles to always drop (Senior, Director, driver, …)
- Schedule: the `cron` line in `.github/workflows/daily.yml` (in UTC; IST = UTC+5:30).
  If you change it, set `posted_within_seconds` to the gap between runs.

## Run locally

```bash
pip install -r requirements.txt
python agent.py --no-email                    # all locations
python agent.py --no-email --only Luxembourg  # one location, for testing
```

## Notes
- Automated access goes against LinkedIn's User Agreement. This tool makes
  polite, delayed requests to public pages for personal use; use at your own risk.
- Filters are text-based, so expect a few misses or false positives.
