# HR Salary Scraper

AI-powered job data extraction from 200+ law firm career sites using **Browser-Use** + **LLM agents**.

Replaces 200 individual Playwright scraper files with **5 strategy templates** + **1 site config JSON**.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   main.py (CLI)                 │
│         --role "Associate" --sites all           │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              config/sites.json                   │
│   200 entries: {name, url, strategy, hints}      │
└──────────────────────┬──────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ Workday  │ │Greenhouse│ │  Direct  │  ... (5 strategies)
   │ Strategy │ │ Strategy │ │ Strategy │
   └────┬─────┘ └────┬─────┘ └────┬─────┘
        │             │             │
        └─────────────┼─────────────┘
                      ▼
┌─────────────────────────────────────────────────┐
│              Browser-Use Agent                   │
│  initial_actions (Playwright, FREE) ──►          │
│  extraction_task (LLM, structured output) ──►    │
│  Pydantic JobExtraction model                    │
└──────────────────────┬──────────────────────────┘
                       │
              ┌────────┼────────┐
              ▼                 ▼
    ┌──────────────┐   ┌──────────────┐
    │ Local JSON   │   │  Cosmos DB   │
    │ results.json │   │  job_cache   │
    └──────────────┘   └──────────────┘
```

## Setup

### 1. Clone & create virtualenv

```bash
cd hr-salary-scraper
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and add your LLM key:

```env
# For OpenAI direct (GPT-5.4 or whatever model you have):
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o-mini

# For Azure OpenAI (uncomment in .env):
# USE_AZURE=true
# AZURE_OPENAI_API_KEY=your-key
# AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
# AZURE_OPENAI_DEPLOYMENT=your-deployment-name
# AZURE_OPENAI_API_VERSION=2024-12-01-preview
```

## Run Commands

### Scrape all 5 test sites for a role
```bash
python main.py --role "Associate Attorney"
```

### Scrape a specific firm
```bash
python main.py --role "Paralegal" --sites "Kirkland"
```

### Run sequentially (easier to debug)
```bash
python main.py --role "Corporate Associate" --single
```

### Run with visible browser (not headless)
```bash
# Set in .env: HEADLESS=false
python main.py --role "Associate Attorney" --single
```

### Save to Cosmos DB instead of local JSON
```bash
python main.py --role "Associate Attorney" --storage cosmos
```

## How It Works

### The Strategy Pattern

Instead of 200 separate scraper files, each site has a `strategy` type in `config/sites.json`:

| Strategy    | Used For                        | Navigation          | Extraction       |
|-------------|--------------------------------|---------------------|------------------|
| `workday`   | Workday ATS sites              | Search box → Click  | LLM reads detail |
| `greenhouse`| Greenhouse boards              | Scan list → Click   | LLM reads detail |
| `icims`     | iCIMS portals                  | Keyword search      | LLM reads detail |
| `lever`     | Lever job boards               | Scan by dept        | LLM reads detail |
| `direct`    | Custom career pages            | Full LLM explore    | LLM reads detail |

### Cost Optimization

- **Navigation**: Done via `initial_actions` (plain Playwright) = **FREE**
- **Extraction**: Done via LLM agent = costs tokens but only for the final page
- **Result**: ~80% cheaper than sending every page to the LLM

### Adding New Sites

Edit `config/sites.json`:

```json
{
  "name": "Skadden Arps",
  "careers_url": "https://www.skadden.com/careers",
  "strategy": "direct",
  "navigation_hints": "Has separate Lawyer and Staff sections"
}
```

That's it. No new Python file needed.

### Adding a New Strategy

1. Create `app/strategies/my_new_ats.py` (copy from `direct.py`)
2. Add to `ATSStrategy` enum in `app/models.py`
3. Register in `app/strategies/__init__.py`

## Output

### Console Output
```
✅  Kirkland & Ellis (direct)
   Role searched: Associate Attorney
   Duration: 45.2s
   Title: Corporate Associate
   Salary: $215,000 — $235,000
   Experience: 3-5 years
   Location: New York, NY
   URL: https://www.kirkland.com/careers/associate-12345
```

### results.json
```json
{
  "firm_name": "Kirkland & Ellis",
  "strategy_used": "direct",
  "role_searched": "Associate Attorney",
  "extraction": {
    "role_title": "Corporate Associate",
    "salary_min": "$215,000",
    "salary_max": "$235,000",
    "salary_raw": null,
    "experience_years": "3-5 years",
    "location": "New York, NY",
    "job_url": "https://www.kirkland.com/careers/associate-12345",
    "practice_area": "Corporate"
  },
  "status": "success",
  "scrape_duration_sec": 45.2
}
```

## Scaling to 200 Sites

Once the 5 test sites work:

1. Add all 200 entries to `config/sites.json`
2. Identify each site's ATS type (most AmLaw firms use Workday/Greenhouse/iCIMS)
3. Set `MAX_CONCURRENT=5` in `.env` for parallel runs
4. Use `--storage cosmos` to push results to your existing Cosmos DB
5. Wrap `main.py` in your Azure Function App timer trigger for scheduled runs

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `playwright install` fails | Run `python -m playwright install chromium` |
| Timeout on a site | Increase `SCRAPE_TIMEOUT` in `.env` |
| LLM returns garbage | Set `HEADLESS=false` and run `--single` to watch the browser |
| Cosmos DB auth error | Check `COSMOS_ENDPOINT` and `COSMOS_KEY` in `.env` |
| Rate limited by site | Lower `MAX_CONCURRENT` to 1-2 |
