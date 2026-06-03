# Azure Deployment Reference

## Resources Created

| Resource | Name |
|----------|------|
| Resource Group | `AF-Innov-AFSChat-Prd` |
| Container Registry (ACR) | `hrscraperregistry` |
| ACR Login Server | `hrscraperregistry.azurecr.io` |
| Docker Image | `hrscraperregistry.azurecr.io/hr-scraper:latest` |
| Storage Account | `hrscrapestore` |
| App Service Plan | `hr-scraper-plan` (Elastic Premium EP2, Linux) |
| Function App | `hr-salary-scraper-fn` |
| Function App URL | `https://hr-salary-scraper-fn.azurewebsites.net` |
| Cosmos DB Account | `hrsalarydb` |
| Cosmos DB Database | `hrsalarydb` |
| Cosmos DB Container | `agent_job_results` |
| Cosmos DB Partition Key | `/role` |
| Subscription | `ArentFox CSP (c06d2c30-467b-465d-a427-81a210dc4bd3)` |
| Location | `East US` |

---

## HTTP Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/trigger` | POST | Start a scrape run (returns 202 immediately) |
| `/api/status` | GET | Check current run status |

### Trigger a scrape (PowerShell)
```powershell
# All roles, all firms (default)
Invoke-RestMethod -Method POST -Uri "https://hr-salary-scraper-fn.azurewebsites.net/api/trigger" -ContentType "application/json" -Body '{}'

# Single firm test
Invoke-RestMethod -Method POST -Uri "https://hr-salary-scraper-fn.azurewebsites.net/api/trigger" -ContentType "application/json" -Body '{"strategy":"videsktop","filter":"Jones Day","roles":["paralegal"]}'
```

### Check status
```powershell
Invoke-RestMethod -Uri "https://hr-salary-scraper-fn.azurewebsites.net/api/status"
```

---

## Scheduled Trigger

- **Schedule**: Daily at **12:00 AM IST** (18:30 UTC)
- **CRON expression**: `0 30 18 * * *`
- Reads roles from `config/roles.json`: paralegal, litigation, business development
- Scrapes all firms in `config/all_firms.json` (56 firms)

---

## How to Push Code Changes

Every time you change any code, rebuild and push the Docker image, then restart the Function App.

### Step 1 — Login to ACR (only needed once per session)
```
az acr login --name hrscraperregistry
```
If it hangs, use password login:
```
az acr credential show --name hrscraperregistry --query "passwords[0].value" -o tsv
docker login hrscraperregistry.azurecr.io -u hrscraperregistry
```

### Step 2 — Build the image
```
docker build -t hr-scraper:latest .
```

### Step 3 — Tag the image
```
docker tag hr-scraper:latest hrscraperregistry.azurecr.io/hr-scraper:latest
```

### Step 4 — Push to ACR
```
docker push hrscraperregistry.azurecr.io/hr-scraper:latest
```

### Step 5 — Restart the Function App to pull the new image
```
az functionapp restart --name hr-salary-scraper-fn --resource-group AF-Innov-AFSChat-Prd
```

### Step 6 — Verify it's running
```
az functionapp show --name hr-salary-scraper-fn --resource-group AF-Innov-AFSChat-Prd --query "state" -o tsv
```

---

## How to Add / Change Roles

Edit `config/roles.json` and add or remove roles:
```json
["paralegal", "litigation", "business development"]
```
Then rebuild and push (Steps 2-5 above).

---

## How to Add / Change Firms

Edit `config/all_firms.json` — add a new entry following the existing format:
```json
{
  "name": "Firm Name",
  "url": "https://careers.firmname.com",
  "strategy": "videsktop"
}
```
Then rebuild and push (Steps 2-5 above).

---

## Environment Variables (set in Function App Configuration)

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name |
| `AZURE_OPENAI_API_VERSION` | API version |
| `STRATEGY` | Scraping strategy (`videsktop`) |
| `CONCURRENCY` | Parallel browsers (default: 5) |
| `SITE_FILTER` | Filter by firm name (`all` = no filter) |
| `HEADLESS` | Run browser headless (`true`) |
| `STORAGE` | Storage backend (`cosmos`) |
| `COSMOS_ENDPOINT` | Cosmos DB endpoint URL |
| `COSMOS_KEY` | Cosmos DB connection string |
| `COSMOS_DATABASE` | Database name (`hrsalarydb`) |
| `COSMOS_CONTAINER` | Container name (`agent_job_results`) |

To update a variable:
**Azure Portal** → `hr-salary-scraper-fn` → **Configuration** → **Application settings** → edit → **Save**

---

## View Logs

```
az functionapp logs tail --name hr-salary-scraper-fn --resource-group AF-Innov-AFSChat-Prd
```

Or in Azure Portal → `hr-salary-scraper-fn` → **Log stream**
