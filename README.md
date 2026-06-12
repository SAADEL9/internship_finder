# Internship Finder

A robust Python scraper that finds fresh Software Engineering internships in Casablanca/Morocco and sends new matches to Discord via GitHub Actions twice daily.

## What It Does

- **Multi-Source Scraping**: Searches public job pages, RSS feeds, and company career pages.
- **Improved Relevancy**: Uses a relaxed scoring-based approach. Jobs must match a **Location** AND either an **Internship Term** (e.g., PFE, Stage) OR a **Skill** (e.g., React, Java).
- **Intelligent Location Fallback**: If a job's specific location is missing from structured data, it falls back to a source-specific or company-specific default (e.g., Casablanca).
- **Deduplication**: Stores seen jobs in `seen_jobs.json` and commits them back to the repository to avoid duplicate alerts.
- **Discord Notifications**: Sends rich embeds to Discord with retry logic and rate-limit handling.
- **Robustness**: Respects `robots.txt`, logs HTTP status codes, and provides a per-run summary.

## Files

- `scraper.py`: Core logic for scraping, filtering, scoring, deduplication, and Discord notifications.
- `config.yml`: Keywords, filters, sources, and company career pages.
- `requirements.txt`: Python dependencies.
- `seen_jobs.json`: Durable deduplication state.
- `.github/workflows/internship-finder.yml`: Twice-daily and manual GitHub Actions workflow.

## Discord Webhook Setup

1. Open the Discord server and channel where you want internship alerts.
2. Click the channel settings gear.
3. Open `Integrations` -> `Webhooks` -> `New Webhook`.
4. Copy the webhook URL.
5. In your GitHub repository settings:
   - Go to `Settings` -> `Secrets and variables` -> `Actions`.
   - Add a `New repository secret` named `DISCORD_WEBHOOK_URL` with your URL.

## GitHub Actions Schedule

The workflow runs twice daily:
- **16:00 Morocco Time** (`0 15 * * *` UTC)
- **20:00 Morocco Time** (`0 19 * * *` UTC)

*Note: Times may shift by 1 hour during Morocco's DST transitions (e.g., Ramadan).*

You can also run it manually from the **Actions** tab in GitHub.

## Local Development & Debug Mode

### Setup
```bash
pip install -r requirements.txt
```

### Regular Run
```bash
# Set your webhook URL in a .env file or environment variable
export DISCORD_WEBHOOK_URL="your_url_here"
python scraper.py
```

### Debug Mode
If you want to see exactly what is being scraped before filtering, use the `DEBUG` flag. This will dump all raw extracted jobs to a `debug_jobs.json` file.

```bash
export DEBUG=1
python scraper.py
```

## Customization

Edit `config.yml` to tune the search:
- **`filters`**: Add new skills, locations, or internship terms.
- **`queries`**: Add new search strings for the scrapers.
- **`sources`**: Enable/disable job boards.
- **`company_career_pages`**: Add direct links to company hiring pages.

## Known Limitations
The scraper respects `robots.txt`. Some major sites (LinkedIn, Indeed, Glassdoor) are heavily protected against scraping and have been disabled to ensure the workflow remains efficient. We prioritize reliable Morocco-specific sources and direct career pages.
