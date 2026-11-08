# LinkedIn Internship Agent

Every day, emails you the LinkedIn internships posted in the **last 24 hours**:
- **Germany**: remote internships only
- **Berlin, Paris, Lyon, Netherlands, Luxembourg, Switzerland**: all internships

It uses LinkedIn's public (logged-out) job search, filtered by
*Experience level = Internship* **or** *Job type = Internship*. You don't need a
LinkedIn account or API key.

## Setup (about 5 minutes)

1. **Create a GitHub repo** (private is fine) and push this folder to it.
2. **Create a Gmail App Password**: go to Google Account → Security → turn on
   2-Step Verification → App passwords → create one (16 characters).
3. **Add repo secrets**: Settings → Secrets and variables → Actions → New secret:
   | Secret          | Value                                          |
   |-----------------|------------------------------------------------|
   | `SMTP_USER`     | your Gmail address                             |
   | `SMTP_PASSWORD` | the App Password from step 2                   |
   | `EMAIL_TO`      | where to send the digest (comma separated ok)  |
4. **Test it**: Actions tab → *Daily LinkedIn internships* → *Run workflow*.
   Tick *Only send a test email* to check the email settings in under a minute.

After that it runs every day at 06:17 UTC. Each run's CSV and HTML are also kept
as a downloadable workflow artifact for 30 days.

## Customize

Edit `config.yaml`:
- `searches`: any LinkedIn location (country or city), with `remote_only: true`
  to keep only remote internships
- `keywords`: e.g. `["software", "data"]` to narrow down; `[""]` = everything
- Schedule: change the `cron` line in `.github/workflows/daily.yml` (time is UTC)

## Run locally

```bash
pip install -r requirements.txt
python agent.py --no-email     # writes output/internships-YYYY-MM-DD.{csv,html}
```

## Notes
- Automated access goes against LinkedIn's User Agreement. This tool makes a
  small number of polite, delayed requests to public pages for personal use,
  but use it at your own risk.
- If a run reports 0 results, LinkedIn may be rate-limiting GitHub's IPs or may
  have changed its page structure. Re-run later, or open an issue to update the
  parser.
