# Internship Finder

Daily Python scraper that finds fresh Software Engineering internships in Casablanca, Morocco and sends new matches to Telegram from GitHub Actions, so your computer does not need to stay on.

## What It Does

- Searches public job pages, RSS feeds, and company career pages.
- Keeps only relevant `Stage`, `Stage PFE`, and internship offers for Casablanca, hybrid Casablanca, or remote Morocco.
- Extracts publication dates from structured data, page metadata, RSS timestamps, visible date labels, and relative date text.
- Ignores dated offers older than 14 days.
- Prioritizes offers posted in the last 24 hours.
- Marks uncertain freshness as `Unknown Date` and ranks those below fresh dated offers.
- Scores each offer from 0 to 100 using location, internship terms, skills, and freshness.
- Stores seen jobs in `seen_jobs.json` to avoid duplicate Telegram alerts.
- Continues when one source fails and writes logs to `internship_finder.log`.

## Files

- `scraper.py`: scraper, filtering, scoring, deduplication, and Telegram sender.
- `config.yml`: keywords, filters, sources, and company career pages.
- `requirements.txt`: Python dependencies.
- `seen_jobs.json`: durable deduplication state.
- `.github/workflows/internship-finder.yml`: daily and manual GitHub Actions workflow.

## Telegram Setup

1. In Telegram, message `@BotFather`.
2. Create a bot with `/newbot`.
3. Copy the bot token.
4. Send any message to your new bot.
5. Get your chat id by opening this URL in a browser:

```text
https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
```

6. In GitHub, open your repository settings:

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

7. Add these secrets:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

## GitHub Actions Schedule

The workflow runs every day with:

```yaml
cron: "0 7 * * *"
```

GitHub Actions cron is UTC-only. This corresponds to 08:00 in Morocco during UTC+1 periods. If Morocco switches to UTC, change it to:

```yaml
cron: "0 8 * * *"
```

You can also run it manually from:

```text
Actions -> Internship Finder -> Run workflow
```

## Local Run

Create a `.env` file if you want to test Telegram locally:

```env
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

Install and run:

```bash
pip install -r requirements.txt
python scraper.py
```

If Telegram secrets are not configured, the script prints the summary instead of sending it.

## Customization

Edit `config.yml` to add:

- More skills.
- More internship terms.
- More search queries.
- More company career pages.
- More source URLs.

The crawler respects `robots.txt` when it can be fetched. It does not log in, bypass CAPTCHAs, use stolen accounts, or evade site security.

## Current Source Coverage

Configured sources include LinkedIn public guest jobs, Indeed, Glassdoor, Welcome To The Jungle, ReKrute, Emploi.ma, MarocAnnonces, Novojob, Talent.com, Jooble, Jobrapido, Monster, Bayt, selected RSS feeds, and career pages for Capgemini, CGI, DXC, Oracle, IBM, Deloitte, PwC, EY, Orange, Inwi, Maroc Telecom, Attijariwafa Bank, OCP, SQLI, Inetum, and Sopra Steria.

Some websites change markup frequently or block automated access. The app logs those failures and continues processing other sources.
