# HR Salary Scraper -- Complete Code Understanding

## Table of Contents
1. [What This Project Does](#1-what-this-project-does)
2. [Project Structure](#2-project-structure)
3. [Architecture Overview](#3-architecture-overview)
4. [Data Flow -- End to End](#4-data-flow--end-to-end)
5. [Models -- app/models.py](#5-models--appmodelspy)
6. [Strategies -- app/strategies/](#6-strategies--appstrategies)
7. [Scraper Engine -- app/scraper.py](#7-scraper-engine--appscraperpy)
8. [Main Orchestrator -- main.py](#8-main-orchestrator--mainpy)
9. [Storage Layer -- app/storage.py](#9-storage-layer--appstoragepy)
10. [Azure Functions Entry Point -- function_app.py](#10-azure-functions-entry-point--function_apppy)
11. [How the Agent Is Built](#11-how-the-agent-is-built)
12. [Function Call Map](#12-function-call-map)
13. [Config Files](#13-config-files)
14. [Key Design Decisions](#14-key-design-decisions)

---

## 1. What This Project Does

This scraper visits 193 law firm career portals, searches for specific job roles (paralegal,
litigation, business development, IP, etc.), extracts salary/experience/location from each
job posting using an AI agent (Browser-Use + Azure OpenAI GPT), and saves the results to
Azure Cosmos DB.

**Key idea:** Instead of writing CSS selectors for each site, we give the AI a plain-English
task and let it navigate the website like a human -- clicking buttons, filling search boxes,
reading page content.

**Processing model:** FIRM-FIRST. For each firm, all roles are searched sequentially before
moving to the next firm. This prevents role starvation, keeps each browser session focused,
and limits open browsers to the concurrency limit at all times.

**Storage model:** Roles and their search terms are read from the `analyses` Cosmos container.
Crawl results are written to the `agent_job_results` Cosmos container -- one document per
unique role, upserted incrementally every N jobs during the run.

---

## 2. Project Structure

```
hr-salary-scraper/
|
+-- function_app.py          <- Azure Functions entry point (HTTP + Timer + Stop + Status)
+-- main.py                  <- Core orchestrator + CLI entry point
|
+-- app/
|   +-- models.py            <- Pydantic data models (SiteConfig, JobExtraction, ScrapeResult)
|   +-- scraper.py           <- Browser-Use agent setup + execution
|   +-- storage.py           <- CosmosStorage, LocalStorage, CrawlStorage
|   +-- strategies/
|       +-- base.py          <- Abstract base class for all strategies
|       +-- videsktop.py     <- viDesktop/VI Recruit portal strategy (most detailed)
|       +-- workday.py       <- Workday ATS strategy
|       +-- icims.py         <- iCIMS ATS strategy
|       +-- ultipro.py       <- UltiPro/UKG ATS strategy
|       +-- greenhouse.py    <- Greenhouse ATS strategy
|       +-- florecruit.py    <- FloRecruit ATS strategy
|       +-- direct.py        <- Direct/custom career page strategy
|       +-- lever.py         <- Lever ATS strategy
|       +-- __init__.py      <- Strategy registry (get_strategy function)
|
+-- config/
|   +-- all_firms_complete.json  <- 193 law firm definitions (name, URL, strategy)
|   +-- all_firms_test.json      <- 10-firm subset for local testing
|   +-- roles.json               <- Fallback roles list (used in local/non-cosmos mode)
|
+-- Dockerfile               <- Docker image (Azure Functions + Playwright + Chromium)
+-- host.json                <- Azure Functions host config (2-hour function timeout)
+-- local.settings.json      <- Local env vars for func start (not committed)
+-- requirements.txt         <- Python dependencies
+-- .env                     <- Local env vars for python main.py runs
+-- AZURE_DEPLOYMENT.md      <- Deployment guide (Docker build, push, App Settings)
+-- CODE_UNDERSTANDING.md    <- This file
```

---

## 3. Architecture Overview

### Cosmos DB containers

| Container | Purpose | Partition key |
|---|---|---|
| `analyses` | Source of truth -- one doc per role, holds `similar_roles` search terms | `/role` |
| `agent_job_results` | Crawl output -- one doc per unique role, holds all jobs found | `/role` |

### analyses doc shape
```json
{
  "id": "4a5b38ad-...",
  "role": "Litigation Paralegal Specialist or Senior Paralegal Specialist",
  "similar_roles": ["Senior Litigation Paralegal", "Litigation Legal Assistant Specialist", ...],
  "is_crawled": "agent",
  "cache_id": "crawl_<md5>"
}
```

### agent_job_results doc shape
```json
{
  "id": "crawl_<md5 of role name>",
  "role": "litigation paralegal specialist or senior paralegal specialist",
  "job_count": 4,
  "jobs": [
    {
      "id": "job_<uuid>",
      "firm": "Jones Day",
      "title": "Senior Paralegal",
      "location": "Dallas, TX",
      "salary_raw": "$75,000 - $95,000 annually",
      "salary_min": "$75,000",
      "salary_max": "$95,000",
      "url": "https://...",
      "cached_at": "2025-06-08T..."
    }
  ],
  "crawled_at": "2025-06-08T..."
}
```

### Deduplication logic (analyses -> roles)
The `analyses` container may have multiple docs for the same role name (e.g. 6x
"In-House Legal Writing Coach" with slightly different `similar_roles`). Before crawling,
`run_scraper()` merges these into one unique role entry per name, unioning all search
terms across all copies. It also removes cross-role duplicate terms (same term appearing
under two different head roles -- kept only under the first one).

Result: 36 analyses docs -> 16 unique head roles -> 80 unique search terms, zero overlap.

---

## 4. Data Flow -- End to End

```
Azure Timer (12 AM IST daily)  OR  POST /api/trigger
        |
        v
function_app.py -> _execute_scrape()   [background task, returns 202 immediately]
        |
        v
main.py -> run_scraper()
        |
        +-- connect CrawlStorage (analyses + agent_job_results containers)
        +-- read_analyses()           -> 36 raw docs from analyses container
        +-- deduplicate by role name  -> 16 unique roles, merged similar_roles terms
        +-- remove cross-role dup terms -> 80 unique search terms total
        |
        +-- load_sites()              -> 193 SiteConfig objects from all_firms_complete.json
        |
        v
run_batch_firm_first(sites, roles, concurrency=10)
        |
        +-- asyncio.Semaphore(10)     -> max 10 browsers open at once
        |
        +-- for each firm (10 at a time):
                |
                +-- for each role (sequentially within the firm):
                        |
                        v
                   scraper.py -> scrape_site(site, role, search_terms)
                        |
                        +-- get_strategy(site.strategy)
                        +-- strategy.get_navigation_task(role, url, hints + search terms)
                        +-- BrowserProfile(unique_temp_dir, headless=True, --no-sandbox)
                        +-- Agent(task, llm, profile, ...)
                        +-- asyncio.wait_for(agent.run(max_steps=60), timeout=600)
                        +-- parse_multi_extraction(result)
                        |
                        v
                   list[ScrapeResult]
                        |
                        v
                firm done -> accumulate jobs into role_jobs_buffer[role]
                        |
                        v
                flush_crawl_jobs(role) if buffer >= SAVE_EVERY_JOBS (default 20)
                        |
                        v
                CrawlStorage.save_crawl_result(crawl_id, role, jobs)
                        |   [upsert to agent_job_results -- retries on 429/503]
                        v
                Cosmos DB: agent_job_results upserted incrementally
        |
        +-- final flush (force=True) for remaining jobs after all firms done
        +-- update_analysis() -- mark each analyses doc is_crawled='agent'
        +-- return summary dict {total_jobs, firms_done, aborted, ...}
```

---

## 5. Models -- app/models.py

### ATSStrategy (Enum)
```python
class ATSStrategy(str, Enum):
    VIDESKTOP  = "videsktop"
    WORKDAY    = "workday"
    ICIMS      = "icims"
    ULTIPRO    = "ultipro"
    GREENHOUSE = "greenhouse"
    FLORECRUIT = "florecruit"
    DIRECT     = "direct"
    LEVER      = "lever"
```
Defines the career portal type. Every firm in `all_firms_complete.json` has one of these.
Used in `scraper.py` to pick the right AI prompt and browser settings.

---

### SiteConfig
```python
class SiteConfig(BaseModel):
    name:             str           # "Jones Day"
    careers_url:      str           # "https://jonesdaystaffrecruitselfapply.viglobalcloud.com/..."
    strategy:         ATSStrategy   # ATSStrategy.VIDESKTOP
    navigation_hints: Optional[str] # Extra firm-specific instructions for the AI
```
Loaded from `config/all_firms_complete.json` in `main.py -> load_sites()`.

---

### JobExtraction
```python
class JobExtraction(BaseModel):
    role_title:       str            # "Paralegal - Litigation"
    description:      Optional[str]  # 2-4 sentence summary of the role
    salary_min:       Optional[str]  # "$75,000"
    salary_max:       Optional[str]  # "$90,000"
    salary_raw:       Optional[str]  # "The salary range is $75,000-$90,000 annually."
    is_hourly:        Optional[bool] # True only if page says "per hour" / "/hr"
    experience_years: Optional[str]  # "3-5 years"
    experience_raw:   Optional[str]  # Full raw experience text from the page
    location:         Optional[str]  # "Chicago, IL"
    job_url:          str            # "https://...RecApplicantEmail.aspx?Tag=..."
    practice_area:    Optional[str]  # "Litigation"
```
What the AI extracts from each job detail page.
`parse_multi_extraction()` in `scraper.py` converts the agent's raw text into this model.

---

### ScrapeResult
```python
class ScrapeResult(BaseModel):
    firm_name:           str
    strategy_used:       str
    role_searched:       str                    # the head role name from analyses
    extraction:          Optional[JobExtraction]
    status:              str                    # "success" | "no_results" | "error"
    error_message:       Optional[str]
    scrape_duration_sec: Optional[float]
```
`scrape_site()` returns `list[ScrapeResult]`.
`_build_crawl_job()` in `storage.py` converts a successful result into a job dict for
the `agent_job_results` crawl doc.

---

## 6. Strategies -- app/strategies/

### Why strategies exist
Different firms use different Applicant Tracking Systems. A viDesktop portal has a completely
different HTML structure, URL pattern, and navigation flow than Workday or iCIMS. Each
strategy holds the AI prompt tailored for that portal type.

---

### BaseStrategy (base.py)
```python
class BaseStrategy(ABC):
    def get_initial_actions(self, role, url) -> list[dict]:  ...  # abstract
    def get_extraction_task(self, role, url) -> str:         ...  # abstract
    def get_navigation_task(self, role, url, hints) -> str:       # concrete
```
- `get_initial_actions` -- Playwright actions run BEFORE the AI starts (e.g. go_to_url).
  No AI, no tokens, deterministic navigation to get the browser to the right page.
- `get_extraction_task` -- The main AI prompt. Tells the agent how to navigate this portal
  type and what JSON schema to return.
- `get_navigation_task` -- Combines extraction task + firm hints + universal hard rules
  into the final prompt string passed to the Agent.

---

### ViDesktopStrategy (videsktop.py) -- most detailed
Covers 35 firms on the viDesktop/VI Recruit platform. The prompt has 10 sections:

| Section | What it tells the AI |
|---|---|
| A | How to identify a viDesktop portal (URL pattern, grid, search box) |
| B | How to dismiss cookie banners and popups |
| C | Wrong page detection and recovery |
| D | Phase 1: Navigate from firm's careers page to the viDesktop portal |
| E | Phase 2: Wait for and verify the job listing grid |
| F | Phase 3: Search ALL provided terms (every similar_roles term, mandatory) |
| G | Phase 4: Scan all matching grid rows, build candidate list with pre-click URLs |
| H | Phase 5: Open each job, record job URL, extract data |
| I | Extraction schema -- exact fields, salary patterns, location validation |
| J | Fallback -- return empty jobs array if nothing found |

**Key technical detail -- viDesktop postback problem:**
viDesktop uses ASP.NET WebForms. Clicking a job row fires `__doPostBack()` which replaces
page content IN PLACE. The URL stays at `RecDefault.aspx` forever -- even after the job
detail loads. The AI must never use `page.url` as `job_url`. Instead it extracts
`RecApplicantEmail.aspx?Tag=...` or `RecJobView.aspx?FilterJobID=...` from the HTML.

---

### Other strategies
Each follows the same BaseStrategy interface but with prompts tuned for their portal:
- `workday.py` -- Workday ATS (15 firms) -- handles Workday's faceted search and job detail pages
- `icims.py` -- iCIMS ATS (7 firms)
- `ultipro.py` -- UltiPro/UKG ATS (18 firms)
- `greenhouse.py` -- Greenhouse ATS (4 firms)
- `florecruit.py` -- FloRecruit ATS (10 firms)
- `direct.py` -- Custom career pages (63 firms) -- most variable, requires broadest instructions
- `lever.py` -- Lever ATS

---

### Strategy Registry -- __init__.py
```python
def get_strategy(strategy: ATSStrategy) -> BaseStrategy:
    mapping = {
        ATSStrategy.VIDESKTOP:  ViDesktopStrategy,
        ATSStrategy.WORKDAY:    WorkdayStrategy,
        ...
    }
    return mapping[strategy]()
```
Called from `scraper.py -> scrape_site()` to get the right strategy instance per firm.

---

## 7. Scraper Engine -- app/scraper.py

### get_llm()
```python
def get_llm():
    if os.getenv("USE_AZURE") == "true":
        return AzureChatOpenAI(
            azure_endpoint   = os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key          = os.getenv("AZURE_OPENAI_API_KEY"),
            azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            api_version      = os.getenv("AZURE_OPENAI_API_VERSION"),
        )
```
Returns a LangChain LLM. One instance is created per browser session in `scrape_site()`.

---

### generate_search_terms(role)
```python
async def generate_search_terms(role: str) -> list[str]:
```
Used only in LOCAL/non-cosmos mode. In cosmos mode, search terms come from `similar_roles`
in the analyses container and are passed directly -- no LLM call needed here.

---

### _NoiseFilter
```python
class _NoiseFilter:
    def write(self, text):
        stripped = text.strip()
        if len(stripped) > 10 and all(c in "=-*" for c in stripped):
            return   # drop Browser-Use separator lines
        self._wrapped.write(text)
```
Browser-Use internally prints 80-character separator lines. This filter wraps sys.stdout
during `agent.run()` and drops those lines to keep Azure Functions logs readable.

---

### scrape_site(site, role, search_terms) -- core function
```python
async def scrape_site(site: SiteConfig, role: str, search_terms: list) -> list[ScrapeResult]:
```

**Step 1 -- Build the final prompt:**
```
task = strategy.get_navigation_task(
    role,
    site.careers_url,
    hints = (site.navigation_hints or "") + search_terms_block
)
```
The `search_terms_block` lists every `similar_roles` term for this role. The AI must
search ALL of them on this site, deduplicate results by title, and return the combined list.

**Step 2 -- Unique Chrome profile per firm:**
```python
unique_profile_dir = os.path.join(tempfile.gettempdir(), f"bu_{uuid.uuid4().hex[:12]}")
```
With 10 concurrent firms, each gets its own temp directory. Chrome's `SingletonLock` file
would crash all but the first browser if they shared a profile directory.

**Step 3 -- BrowserProfile:**
```python
profile = BrowserProfile(
    user_data_dir = unique_profile_dir,
    headless      = True,
    disable_security = True,
    extra_chromium_args = ["--no-sandbox", "--disable-setuid-sandbox"],
    viewport = {"width": 1280, "height": 900},
)
```
- `--no-sandbox` required inside Docker (Linux kernel sandbox not available in containers)
- `disable_security=True` allows cross-origin requests (career portals use iframes)

**Step 4 -- Agent settings per strategy:**
```python
is_videsktop     = (site.strategy.value == "videsktop")
max_steps        = 60 if is_videsktop else 20
actions_per_step = 1  if is_videsktop else 3
```
viDesktop needs 60 steps (navigate + search all terms + open each job).
`actions_per_step=1` for viDesktop because ASP.NET postbacks reload the page -- batching
3 actions causes the 2nd and 3rd to click stale elements from the pre-reload page.

**Step 5 -- Run with timeout:**
```python
result = await asyncio.wait_for(
    agent.run(max_steps=60),
    timeout = int(os.getenv("SCRAPE_TIMEOUT", "600"))   # default 10 minutes per site
)
```
`SCRAPE_TIMEOUT` is configurable. Default 600s (10 min). If a site hangs, the scrape
aborts cleanly and moves on to the next firm.

**Step 6 -- Parse and return:**
```python
extractions = parse_multi_extraction(final_result, role, last_url[0])
# returns list[JobExtraction]

return [
    ScrapeResult(firm_name=site.name, role_searched=role, extraction=e, status="success", ...)
    for e in extractions
]
```

**Step 7 -- Cleanup (always runs):**
```python
finally:
    shutil.rmtree(unique_profile_dir, ignore_errors=True)
```

---

### parse_multi_extraction(raw_result, fallback_role, fallback_url)
Robustly extracts job objects from the agent's raw text output. The LLM doesn't always
return clean JSON -- it may wrap output in markdown, embed JSON in prose, or return a
single job instead of a `{"jobs": [...]}` array.

Parse order:
1. Direct `json.loads()` on raw string
2. Extract from markdown code block (` ```json ... ``` `)
3. Bracket-matching -- find `{...}` objects embedded in prose text
4. Try as single-job format via `parse_extraction()`

Returns: `list[JobExtraction]`

---

## 8. Main Orchestrator -- main.py

### load_sites(strategy, site_filter, firms_config)
```python
def load_sites(strategy, site_filter, firms_config=None) -> list[SiteConfig]:
    config_path = firms_config or ALL_FIRMS_CONFIG
    raw   = json.load(open(config_path))
    sites = [SiteConfig(**s) for s in raw]
    # optional filters
    if strategy != "all":
        sites = [s for s in sites if s.strategy.value == strategy]
    if site_filter != "all":
        sites = [s for s in sites if site_filter.lower() in s.name.lower()]
    return sites
```
Default config is `config/all_firms_complete.json` (193 firms).
Override via `FIRMS_CONFIG` env var or `--firms-config` CLI flag.
Use `config/all_firms_test.json` (10 firms) for local testing.

---

### run_batch_firm_first(sites, roles, concurrency, ...)
```python
async def run_batch_firm_first(...) -> tuple[list[ScrapeResult], bool]:
```

**Processing model:**
```
Slot 1: [Firm A]  role_1 -> role_2 -> role_3 -> ... -> role_16  (all roles done)
Slot 2: [Firm B]  role_1 -> role_2 -> role_3 -> ... -> role_16
...
Slot 10:[Firm J]  role_1 -> role_2 -> ...
(Firm K starts once any slot frees up)
```
Up to 10 firms run concurrently (one browser each). Within each firm, all 16 roles are
searched sequentially before the slot is released.

**Stop check:**
Before starting each new firm, checks `stop_check()`. If True (set by POST /api/stop),
no new firms start. Already-running firms finish their current role, then the batch exits.
All collected jobs are saved before exit.

**Incremental saving (flush_crawl_jobs):**
After each firm completes, new jobs are added to `role_jobs_buffer[role]`. When the
buffer for any role reaches `SAVE_EVERY_JOBS` (default 20), that role's crawl doc is
upserted to Cosmos DB immediately. This means jobs are never lost even if the run
crashes or is aborted mid-way.

**Returns:** `(all_results, was_aborted)` -- tuple so callers know if run was stopped early.

---

### run_scraper(strategy, site_filter, ...) -- main entry point
```python
async def run_scraper(...) -> dict:
```

**Analyses deduplication (Step A + B):**
```python
# Step A: merge duplicate role docs
merged = {}
for doc in role_docs:
    role_name = doc["role"].strip()
    terms = set(doc.get("similar_roles") or [role_name])
    if role_name not in merged:
        merged[role_name] = {"doc": doc, "terms": terms}
    else:
        merged[role_name]["terms"].update(terms)   # union all terms

# Step B: remove cross-role duplicate terms
globally_claimed = set()
for role_name, data in merged.items():
    unique_terms = []
    for t in sorted(data["terms"]):
        t_key = t.lower().strip()
        if t_key in globally_claimed:
            continue    # already owned by an earlier role
        if t_key in head_role_keys and t_key != role_name.lower().strip():
            continue    # this term IS another head role -- skip
        unique_terms.append(t)
        globally_claimed.add(t_key)
    data["terms"] = set(unique_terms)
```
Result: 36 analyses docs -> 16 unique roles, each with only its own unique search terms.
No term is ever searched twice across different roles.

**After the batch:**
```python
# Update analyses docs to mark them as crawled
for role in roles:
    crawl_id = _generate_crawl_id(role)   # crawl_<md5 of role name>
    doc_id   = role_to_doc_id.get(role)
    await crawl_storage.update_analysis(doc_id, crawl_id)
    # sets is_crawled='agent', cache_id=crawl_id on the analyses doc
```

---

## 9. Storage Layer -- app/storage.py

### CrawlStorage -- primary storage class
Reads roles from `analyses` container, writes job results to `agent_job_results` container.

```python
class CrawlStorage:
    async def connect(self):
        # Opens both containers; verifies both are reachable
        self.analyses_container = db.get_container_client("analyses")
        self.results_container  = await db.create_container_if_not_exists(
            id="agent_job_results",
            partition_key=PartitionKey(path="/role"),
        )

    async def read_analyses(self) -> list[dict]:
        # Reads all role docs and caches them in memory by id
        # Used by run_scraper() to get roles + similar_roles

    async def save_crawl_result(self, crawl_id: str, role: str, jobs: list[dict]):
        # Upserts one crawl doc -- retries up to 3 times with exponential backoff
        # Handles: 429 RU throttling, 503 unavailable, timeout, connection errors

    async def update_analysis(self, doc_id: str, crawl_id: str):
        # Sets is_crawled='agent' and cache_id=crawl_id on the analyses doc
        # Uses upsert (not patch) to avoid needing the partition key path
```

**Retry logic in save_crawl_result:**
```python
max_retries = 3
for attempt in range(1, max_retries + 1):
    try:
        await self.results_container.upsert_item(doc)
        return
    except Exception as e:
        is_transient = "429" in str(e) or "503" in str(e) or "timeout" in str(e).lower()
        if attempt < max_retries and is_transient:
            await asyncio.sleep(2 ** attempt)   # 2s, 4s, 8s
        else:
            raise RuntimeError(f"Could not save after {attempt} attempt(s): {e}")
```

---

### _generate_crawl_id(role)
```python
def _generate_crawl_id(role: str) -> str:
    h = hashlib.md5(role.lower().strip().encode()).hexdigest()
    return f"crawl_{h}"
```
Deterministic ID from role name. All analyses docs for the same role (case-insensitive)
produce the same crawl_id, so upserts converge to one doc in `agent_job_results`.

---

### _build_crawl_job(result, role)
Converts a `ScrapeResult` with a successful `JobExtraction` into the flat job dict
stored inside the crawl doc's `jobs[]` array:
```python
{
    "id":           "job_<uuid>",
    "job_role":     role.lower().strip(),
    "firm":         result.firm_name,
    "title":        e.role_title,
    "location":     e.location,
    "salary_raw":   e.salary_raw or "N/A",
    "salary_min":   e.salary_min,
    "salary_max":   e.salary_max,
    "url":          e.job_url,
    "cached_at":    "<ISO UTC timestamp>",
}
```

---

### CosmosStorage -- legacy flat storage
Still used when `STORAGE=local` is not set and no analyses container is available.
Writes one flat document per job to a single container with partition key `/role_title`.

### LocalStorage
Writes to `results.json` on disk. Used when `STORAGE=local`.

---

## 10. Azure Functions Entry Point -- function_app.py

### _run_state -- in-memory run tracker
```python
_run_state = {
    "status":      "idle",   # idle | running | aborted | completed | failed
    "started_at":  None,
    "finished_at": None,
    "strategy":    None,
    "filter":      None,
    "roles":       [],
    "progress": {
        "current_role":      None,
        "role_index":        0,
        "total_roles":       0,
        "current_firm":      None,
        "firms_done":        0,
        "firms_total":       0,
        "jobs_found_so_far": 0,
    },
    "summary": None,
    "error":   None,
}
```
Updated live during the crawl via the `on_progress` callback.
`GET /api/status` returns this dict -- poll it to watch progress in real time.

---

### _stop_requested -- abort flag
```python
_stop_requested: bool = False
```
Set to True by `POST /api/stop`. Checked before each new firm starts in
`run_batch_firm_first`. When True, no new firms start; active firms finish their current
role then the batch exits cleanly. All jobs collected so far are saved before exit.

---

### Timer Trigger -- scheduled_scrape
```python
@app.timer_trigger(schedule="0 30 18 * * *", run_on_startup=False)
async def scheduled_scrape(timer: func.TimerRequest):
    global _stop_requested
    _stop_requested = False
    await _execute_scrape(strategy="all", site_filter="all", roles=None, firms_config=None)
```
Fires at 18:30 UTC = 12:00 AM IST daily.
Azure Functions CRON has 6 fields: `second minute hour day month weekday`.

---

### HTTP Trigger -- http_trigger (POST /api/trigger)
```python
@app.route(route="trigger", methods=["GET","POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def http_trigger(req: func.HttpRequest) -> func.HttpResponse:

    if _run_state["status"] == "running":
        return 409 Conflict

    # Parse optional body: { "strategy": "all", "filter": "Jones Day", "roles": [...] }
    strategy, site_filter, roles, firms_config = parse_body(req)

    _stop_requested = False
    _run_state["status"] = "running"

    # Fire as background task -- return 202 immediately
    asyncio.get_event_loop().create_task(
        _execute_scrape(strategy, site_filter, roles, firms_config)
    )

    return 202 Accepted
```
**Why background task, not await:**
`await _execute_scrape()` blocks the HTTP response until the scrape finishes (hours).
Azure Functions kills the worker after 230 seconds of HTTP response timeout, silently
aborting the crawl mid-run. `create_task()` detaches the scrape from the HTTP lifecycle --
the response returns immediately and the scrape runs until completion or abort.

---

### Stop Trigger -- stop_trigger (POST /api/stop)
```python
@app.route(route="stop", methods=["GET","POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def stop_trigger(req: func.HttpRequest) -> func.HttpResponse:
    global _stop_requested
    _stop_requested = True
    return 200 {
        "status": "stop_requested",
        "firms_done": ...,
        "firms_remaining": ...,
        "jobs_found_so_far": ...,
        "current_firm": ...,
    }
```
Graceful abort. Active firms finish their current role, then the batch stops.
All jobs collected so far are saved to Cosmos DB before the run ends.

---

### Status Trigger -- status_trigger (GET /api/status)
```python
@app.route(route="status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def status_trigger(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(_run_state, indent=2))
```
Poll this endpoint to watch live progress during a crawl run.

---

### _execute_scrape -- core runner
```python
async def _execute_scrape(strategy, site_filter, roles, firms_config):
    try:
        from main import run_scraper
        summary = await run_scraper(
            strategy=strategy, site_filter=site_filter,
            roles=roles, firms_config=firms_config,
            on_progress=on_progress, stop_check=stop_check,
        )
        final_status = "aborted" if summary.get("aborted") else "completed"
        _run_state.update({"status": final_status, "summary": summary})
    except Exception as e:
        _run_state.update({"status": "failed", "error": str(e)[:500]})
```
Called by both timer and HTTP triggers. Updates `_run_state` on completion or failure.

---

## 11. How the Agent Is Built

```python
agent = Agent(
    task                       = task,            # full AI prompt
    llm                        = llm,             # Azure OpenAI GPT
    browser_profile            = profile,         # isolated Chrome instance
    use_vision                 = False,           # text-only (no screenshots to AI)
    max_actions_per_step       = 1,               # 1 for viDesktop, 3 for others
    max_failures               = 5,
    generate_gif               = False,
    include_attributes         = include_attrs,
    initial_actions            = [{"go_to_url": {"url": site.careers_url}}],
    register_new_step_callback = make_step_callback(site.name, last_url, verbose),
)
```

| Parameter | Why |
|---|---|
| `task` | Complete AI prompt: portal instructions + search terms for this role |
| `llm` | Azure OpenAI GPT -- decides every browser action |
| `browser_profile` | Isolated Chrome -- unique temp dir prevents SingletonLock crashes |
| `use_vision=False` | HTML element listing is sufficient; skipping screenshots saves tokens |
| `max_actions_per_step=1` | viDesktop: ASP.NET postbacks reload the page between every click |
| `max_failures=5` | Retries on transient errors (network blip, stale element) |
| `include_attributes` | Gives AI `href`, `target`, `aria-label` so it can detect URLs and new-tab links |
| `initial_actions` | Navigate to careers URL before AI takes over -- avoids blank page waste |
| `register_new_step_callback` | Tracks current URL (fallback job_url) and prints step logs |

**include_attributes list:**
```python
include_attrs = [
    'title', 'type', 'name', 'role', 'aria-label', 'placeholder', 'value',
    'alt', 'aria-expanded', 'href', 'target',
]
```
Without `href`, the AI sees `[23] <a>Apply Now</a>` and cannot extract the job URL.
With `href`, it sees `[23] <a href="RecApplicantEmail.aspx?Tag=abc123">Apply Now</a>`.

---

## 12. Function Call Map

```
function_app.py
|
+-- scheduled_scrape()      -----------> _execute_scrape()
+-- http_trigger()   -(create_task)---> _execute_scrape()  [202 returned immediately]
+-- stop_trigger()   -(sets flag)---> _stop_requested = True
+-- status_trigger() -(reads)-----> _run_state dict
                                          |
                                          v
                                  main.run_scraper()
                                          |
                            +-- CrawlStorage.connect()
                            +-- read_analyses()  -> 36 docs
                            +-- deduplicate -> 16 roles, 80 terms
                            +-- load_sites() -> 193 SiteConfig
                                          |
                                          v
                              run_batch_firm_first()
                                          |
                            asyncio.Semaphore(10)
                                          |
                            +-- process_firm(site) x193
                                    |
                                    +-- for role in 16 roles:
                                            |
                                            v
                                    scrape_site(site, role, terms)
                                            |
                                +-- get_strategy()
                                +-- get_navigation_task()
                                +-- BrowserProfile(unique_dir)
                                +-- Agent(task, llm, profile)
                                +-- wait_for(agent.run(), timeout=600)
                                +-- parse_multi_extraction()
                                            |
                                            v
                                    list[ScrapeResult]
                                            |
                            +-- role_jobs_buffer[role].append(jobs)
                            +-- flush_crawl_jobs() if buffer >= SAVE_EVERY_JOBS
                                            |
                                            v
                              CrawlStorage.save_crawl_result()   [retries on 429/503]
                                            |
                                            v
                              Cosmos DB: agent_job_results upserted
```

---

## 13. Config Files

### config/all_firms_complete.json (193 firms)
```json
[
  {
    "name": "Jones Day",
    "careers_url": "https://jonesdaystaffrecruitselfapply.viglobalcloud.com/...",
    "strategy": "videsktop",
    "navigation_hints": "Click 'Search Openings' button first."
  },
  ...
]
```
Full production firm list. Set `FIRMS_CONFIG=config/all_firms_complete.json`.

### config/all_firms_test.json (10 firms)
A 10-firm subset used for local testing and validating changes before a full run.
Set `FIRMS_CONFIG=config/all_firms_test.json` for test runs.

### config/roles.json
```json
["paralegal", "litigation", "business development"]
```
Fallback roles list. Used only in LOCAL storage mode (when `STORAGE=local`).
In cosmos mode, roles come from the `analyses` container instead.

### Cosmos DB: analyses container
The live source of truth for roles. Each doc has a `role` name and a `similar_roles` array
of search terms. Multiple docs may share the same role name -- `run_scraper()` deduplicates
and merges them before crawling.

---

## 14. Key Design Decisions

| Decision | Why |
|---|---|
| AI agent instead of CSS selectors | 193 firms on 8 ATS platforms -- each has different HTML; AI adapts without per-firm code |
| Firm-first processing (not role-first) | All roles done per firm before moving on -- prevents role starvation, one browser per firm |
| CrawlStorage reads from analyses container | Roles and search terms are managed in Cosmos, not code -- easy to add/update roles without redeploy |
| Dedup analyses before crawling | Analyses container has duplicate role docs; dedup prevents redundant crawls and term overwrites |
| Merge similar_roles across duplicates | Unique terms from each copy are unioned -- more coverage, nothing lost |
| Cross-role term dedup (Step B) | Same term under two head roles would crawl it twice -- one keeps it, the other skips |
| Incremental save (SAVE_EVERY_JOBS=20) | Jobs saved mid-run; abort or crash loses at most 20 jobs, not the whole run |
| Cosmos retry with backoff | RU throttling (429) is transient; 3 retries at 2s/4s/8s recover without data loss |
| asyncio.create_task() in HTTP trigger | Azure kills the worker after 230s HTTP timeout; background task detaches from that clock |
| POST /api/stop with graceful drain | Running firms finish current role before stopping -- clean state, no partial firm results |
| _stop_requested global flag | Checked before each new firm; already-running firms complete naturally |
| SCRAPE_TIMEOUT=600 per site | Sites that hang (infinite loading, broken portals) get aborted after 10 min, not stuck forever |
| CONCURRENCY=10 on EP3 plan | EP3 Elastic Premium gives enough vCPU/RAM for 10 concurrent Chromium instances |
| Unique Chrome profile per firm | Prevents SingletonLock crash when 10 browsers run in parallel |
| --no-sandbox flag | Chrome's kernel sandbox is unavailable inside Docker containers |
| actions_per_step=1 for viDesktop | ASP.NET postbacks reload the page -- batching 3 actions causes wrong clicks on stale elements |
| upsert_item not create_item | Re-running the scraper updates existing docs instead of throwing duplicate ID errors |
| COSMOS_KEY connection string parsing | Azure Portal gives a full connection string; the Cosmos SDK needs only the bare account key |
| FIRMS_CONFIG env var | Switch between 10-firm test and 193-firm production without code changes |
