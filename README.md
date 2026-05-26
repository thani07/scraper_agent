# HR Salary Scraper

AI-powered salary and job data extraction from law firm career sites using **Browser-Use** + **Azure OpenAI**.

Supports 20+ viDesktop (VI Recruit) portals with concurrent browser sessions.

## Architecture

```
main.py  (ENV-driven entry point)
    │
    ├── generate_search_terms()   — one LLM call generates 4-5 role alternatives
    │
    └── run_batch()               — asyncio.Semaphore(CONCURRENCY) concurrent sessions
            │
            └── scrape_site()     — per-firm isolated browser (unique Chrome profile)
                    │
                    └── ViDesktopStrategy  — full prompt: search all terms, extract salary/URL
                            │
                            └── JobExtraction model
                                    role_title, description,
                                    salary_min, salary_max, salary_raw, is_hourly,
                                    experience_years, location, job_url, practice_area
```

## Quick Start (Local)

### 1. Clone and create virtualenv

```bash
git clone https://github.com/thani07/scraper_agent.git
cd scraper_agent
python -m venv venv
# Windows:  venv\Scripts\activate
# Mac/Linux: source venv/bin/activate
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

Edit `.env` — at minimum set your Azure OpenAI credentials and `ROLE`:

```env
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
ROLE=business professional
```

### 4. Run

```bash
python main.py
```

All settings are read from `.env`. CLI args are optional overrides:

```bash
python main.py --role "paralegal" --strategy videsktop
python main.py --role "analyst"   --filter "Jones Day"
```

## Key Settings (`.env`)

| Variable | Default | Description |
|---|---|---|
| `ROLE` | *(required)* | Role to search across all firms |
| `STRATEGY` | `videsktop` | `videsktop` or `all` |
| `CONCURRENCY` | `5` | Parallel browser sessions |
| `SITE_FILTER` | `all` | Narrow to one firm by name substring |
| `SCRAPE_TIMEOUT` | `300` | Seconds before a firm is aborted |
| `HEADLESS` | `true` | `false` to watch the browser locally |
| `STORAGE` | `local` | `local` (results.json) or `cosmos` |
| `OUTPUT_FILE` | `output.txt` | Plain-text results file |

## Deploy to Azure

### Option A — Azure Container Instances (recommended for on-demand runs)

```bash
# 1. Build and push image
az acr build --registry <your-acr> --image hr-scraper:latest .

# 2. Run a job
az container create \
  --resource-group <rg> \
  --name hr-scraper-run \
  --image <your-acr>.azurecr.io/hr-scraper:latest \
  --environment-variables \
      AZURE_OPENAI_API_KEY=<key> \
      AZURE_OPENAI_ENDPOINT=<endpoint> \
      AZURE_OPENAI_DEPLOYMENT=<deployment> \
      ROLE="business professional" \
      STRATEGY=videsktop \
      CONCURRENCY=5 \
      STORAGE=cosmos \
      COSMOS_ENDPOINT=<cosmos-endpoint> \
      COSMOS_KEY=<cosmos-key> \
      COSMOS_DATABASE=hr-scraper \
      COSMOS_CONTAINER=job-results \
  --restart-policy Never \
  --cpu 2 --memory 4
```

### Option B — Azure Container Apps (HTTP-triggered or scheduled)

Deploy the image to Container Apps and pass all secrets via Container Apps secrets / environment variables. Set `HEADLESS=true` (default in Dockerfile).

### Important for Azure

- `HEADLESS=true` is forced in the Dockerfile — containers have no display
- Pass all secrets as environment variables or Azure Key Vault references — never commit `.env`
- For `STORAGE=cosmos`, set all `COSMOS_*` variables
- Recommended: 2 vCPU / 4 GB RAM for `CONCURRENCY=5`

## Output

### Console (live as each firm finishes)

```
  [OK ] [ 1/21] Jones Day          --  3 job(s)  (47s)
  [---] [ 2/21] Thompson Hine      --  0 job(s)  (38s)
  [ERR] [ 3/21] Brown Rudnick      --  0 job(s)  (12s)
```

### output.txt

```
[FOUND]  Jones Day  --  3 job(s)  (47s)
    --------------------------------------------------
    Title      : Business Development Manager
    Description: Manages client relationships and BD initiatives across practice groups...
    Salary Min : $120,000
    Salary Max : $150,000
    Salary Raw : The salary range for this role is $120,000 – $150,000 annually.
    Experience : 5-7 years
    Location   : New York, NY
    Department : Business Development
    URL        : https://jonesdaystaffrecruitselfapply.viglobalcloud.com/viRecruitSelfApply/RecApplicantEmail.aspx?Tag=...
```

## Project Structure

```
hr-salary-scraper/
├── main.py                        # Entry point (ENV-driven)
├── requirements.txt
├── Dockerfile                     # Production container
├── .env.example                   # Safe config template
├── app/
│   ├── models.py                  # SiteConfig, JobExtraction, ScrapeResult
│   ├── scraper.py                 # scrape_site(), generate_search_terms()
│   ├── storage.py                 # LocalStorage, CosmosStorage
│   └── strategies/
│       ├── videsktop.py           # VI Recruit / ViDesktop portals
│       ├── workday.py
│       ├── greenhouse.py
│       ├── icims.py
│       ├── lever.py
│       ├── direct.py
│       └── base.py
└── config/
    ├── videsktop_firms.json       # 21 viDesktop law firm sites
    └── sites.json                 # All firms across all strategies
```
