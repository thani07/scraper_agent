# HR Salary Scraper — Complete Code Understanding

## Table of Contents
1. [What This Project Does](#1-what-this-project-does)
2. [Project Structure](#2-project-structure)
3. [Data Flow — End to End](#3-data-flow--end-to-end)
4. [Models — app/models.py](#4-models--appmodelspy)
5. [Strategies — app/strategies/](#5-strategies--appstrategies)
6. [Scraper Engine — app/scraper.py](#6-scraper-engine--appscraperpy)
7. [Main Orchestrator — main.py](#7-main-orchestrator--mainpy)
8. [Storage Layer — app/storage.py](#8-storage-layer--appstoragepy)
9. [Azure Functions Entry Point — function_app.py](#9-azure-functions-entry-point--function_apppy)
10. [How the Agent Is Built](#10-how-the-agent-is-built)
11. [Function Call Map](#11-function-call-map)
12. [Config Files](#12-config-files)

---

## 1. What This Project Does

This scraper visits law firm career portals, searches for job roles (paralegal, litigation, business development), extracts salary/experience/location from each job posting using an AI agent (Browser-Use + Azure OpenAI GPT), and saves the results to Azure Cosmos DB.

**Key idea:** Instead of writing CSS selectors for each site, we give the AI a plain-English task and let it navigate the website like a human would — clicking buttons, filling search boxes, reading page content.

---

## 2. Project Structure

```
hr-salary-scraper/
│
├── function_app.py          ← Azure Functions entry point (HTTP + Timer triggers)
├── main.py                  ← Core logic + CLI entry point
│
├── app/
│   ├── models.py            ← Pydantic data models (SiteConfig, JobExtraction, ScrapeResult)
│   ├── scraper.py           ← Browser-Use agent setup + execution
│   ├── storage.py           ← Cosmos DB + local JSON storage
│   └── strategies/
│       ├── base.py          ← Abstract base class for all strategies
│       ├── videsktop.py     ← Strategy for viDesktop/VI Recruit portals
│       └── __init__.py      ← Strategy registry (get_strategy function)
│
├── config/
│   ├── all_firms.json       ← 56 law firm definitions (name, URL, strategy)
│   └── roles.json           ← Roles to scrape ["paralegal", "litigation", ...]
│
├── Dockerfile               ← Docker image with Azure Functions + Playwright
├── host.json                ← Azure Functions config (2-hour timeout)
└── local.settings.json      ← Local env vars (credentials, not committed)
```

---

## 3. Data Flow — End to End

```
Azure Timer (12 AM IST daily)
        │
        ▼
function_app.py → scheduled_scrape()
        │
        ▼
main.py → run_scraper()
        │
        ├── load_roles()         reads config/roles.json   → ["paralegal", "litigation", ...]
        ├── load_sites()         reads config/all_firms.json → 56 SiteConfig objects
        ├── storage.connect()    connects to Cosmos DB
        │
        └── for each role:
               │
               ├── generate_search_terms(role)   LLM generates 4 alternative titles
               │
               └── run_batch(sites, role, ...)
                       │
                       └── for each firm (up to 5 parallel):
                               │
                               ▼
                          scraper.py → scrape_site(site, role, search_terms)
                               │
                               ├── BrowserProfile (unique temp dir, --no-sandbox)
                               ├── Agent(task, llm, browser_profile, ...)
                               ├── agent.run(max_steps=60)
                               │       ↕ AI navigates the website
                               └── parse_multi_extraction(result)
                                       │
                                       ▼
                                  list[ScrapeResult]
                                       │
                                       ▼
                               storage.save_batch()
                                       │
                                       ▼
                               Cosmos DB: agent_job_results
```

---

## 4. Models — app/models.py

These are Pydantic models — they define the shape/type of data throughout the project.

### `ATSStrategy` (Enum)
```python
class ATSStrategy(str, Enum):
    VIDESKTOP = "videsktop"
    WORKDAY   = "workday"
    ...
```
Defines the type of career portal. Every firm in `all_firms.json` has one of these values.
Used in `scraper.py` to decide which strategy to apply: `is_videsktop = (site.strategy.value == "videsktop")`.

---

### `SiteConfig`
```python
class SiteConfig(BaseModel):
    name:             str           # "Jones Day"
    careers_url:      str           # "https://jonesdaystaffrecruitselfapply.viglobalcloud.com/..."
    strategy:         ATSStrategy   # ATSStrategy.VIDESKTOP
    navigation_hints: Optional[str] # Extra instructions for this specific firm
```
**Where used:** Loaded from `config/all_firms.json` in `main.py → load_sites()`.
Passed to `scrape_site(site, role)` in `scraper.py`.

---

### `JobExtraction`
```python
class JobExtraction(BaseModel):
    role_title:       str            # "Paralegal – Litigation"
    description:      Optional[str]  # "2-4 sentence summary..."
    salary_min:       Optional[str]  # "$75,000"
    salary_max:       Optional[str]  # "$90,000"
    salary_raw:       Optional[str]  # "The salary range is $75,000–$90,000 annually."
    is_hourly:        Optional[bool] # True only if page says "per hour" / "/hr"
    experience_years: Optional[str]  # "3-5 years"
    experience_raw:   Optional[str]  # Full raw experience text
    location:         Optional[str]  # "Chicago, IL"
    job_url:          str            # "https://...RecApplicantEmail.aspx?Tag=..."
    practice_area:    Optional[str]  # "Litigation"
```
**Where used:** This is what the AI agent extracts from each job page.
`parse_multi_extraction()` in `scraper.py` parses the agent's text output into this model.
It becomes the `extraction` field inside `ScrapeResult`.

---

### `ScrapeResult`
```python
class ScrapeResult(BaseModel):
    firm_name:          str                    # "Jones Day"
    strategy_used:      str                    # "videsktop"
    role_searched:      str                    # "paralegal"
    extraction:         Optional[JobExtraction] # The job data (None if error/no results)
    status:             str                    # "success" | "no_results" | "error"
    error_message:      Optional[str]          # Only present on error
    scrape_duration_sec: Optional[float]       # How long this scrape took
```
**Where used:** `scrape_site()` returns `list[ScrapeResult]`.
`_build_document()` in `storage.py` flattens this into a Cosmos DB document.

---

## 5. Strategies — app/strategies/

### Why strategies exist
Different law firms use different Applicant Tracking Systems (ATS).
A viDesktop portal looks completely different from a Workday portal.
Each strategy holds the AI prompt tailored for that specific portal type.

---

### `BaseStrategy` (base.py)
```python
class BaseStrategy(ABC):
    def get_initial_actions(self, role, url) → List[dict]: ...  # abstract
    def get_extraction_task(self, role, url) → str:         ...  # abstract
    def get_navigation_task(self, role, url, hints) → str:      # concrete
```
- `get_initial_actions` — Playwright actions to run BEFORE the agent starts (e.g. go_to_url).
  These run deterministically — no AI, no tokens used.
- `get_extraction_task` — The main AI prompt. Tells the agent exactly how to navigate
  this type of portal and what JSON to return.
- `get_navigation_task` — Combines `get_extraction_task` + hints + universal hard rules
  (no extra tabs, wrong-page recovery, etc.). This is the final prompt passed to the agent.

**Called from:** `scraper.py → scrape_site()`:
```python
strategy = get_strategy(site.strategy)       # returns ViDesktopStrategy instance
task = strategy.get_navigation_task(role, site.careers_url, effective_hints)
```

---

### `ViDesktopStrategy` (videsktop.py)
This is the most detailed strategy — 400+ lines of prompt instructions covering:

| Section | What it tells the AI |
|---------|---------------------|
| A | How to identify a viDesktop portal (URL pattern, grid, search box) |
| B | How to dismiss cookie banners / popups |
| C | Wrong page detection and recovery |
| D | Phase 1: Navigate from firm's careers page to the viDesktop portal |
| E | Phase 2: Wait for and verify the job listing grid |
| F | Phase 3: Search ALL provided terms (paralegal, Legal Assistant, Legal Support...) |
| G | Phase 4: Scan all matching grid rows, build candidate list |
| H | Phase 5: Open each job, extract the job URL, extract data |
| I | Extraction schema — exactly what fields to extract and how |
| J | Fallback — return `{"jobs": []}` if nothing found |

**Key technical detail in the prompt — viDesktop postback problem:**
viDesktop uses ASP.NET WebForms. When you click a job row, `__doPostBack()` fires and replaces the page content IN PLACE. The URL stays at `RecDefault.aspx` forever. This means you CANNOT use `page.url` as the job URL — you must extract `RecApplicantEmail.aspx?Tag=...` from the HTML. The prompt explicitly warns the AI about this.

---

### Strategy Registry — `__init__.py`
```python
def get_strategy(strategy: ATSStrategy) -> BaseStrategy:
    if strategy == ATSStrategy.VIDESKTOP:
        return ViDesktopStrategy()
    ...
```
**Called from:** `scraper.py → scrape_site()` to get the right strategy for each firm.

---

## 6. Scraper Engine — app/scraper.py

This is the most complex file. It sets up and runs the Browser-Use AI agent.

---

### `get_llm()`
```python
def get_llm():
    if os.getenv("USE_AZURE") == "true":
        return AzureChatOpenAI(
            azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key        = os.getenv("AZURE_OPENAI_API_KEY"),
            azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            ...
        )
```
Returns a LangChain LLM object — either Azure OpenAI or plain OpenAI.
**Called from:**
- `scrape_site()` — one LLM instance per browser agent
- `generate_search_terms()` — one LLM call to get alternative job titles

---

### `generate_search_terms(role)`
```python
async def generate_search_terms(role: str) -> list[str]:
```
Calls the LLM with a prompt like:
> "Generate 4 alternative job titles for 'paralegal' used on law firm career portals."

Returns: `["paralegal", "Paralegal Specialist", "Legal Assistant", "Legal Support", "Litigation Support"]`

**Why needed:** Different firms title the same role differently. Searching only "paralegal" misses firms that post it as "Legal Assistant". By searching all alternatives, we find more jobs.

**Called from:** `main.py → run_scraper()` once per role, before running the batch.

---

### `_expand_search_terms(role)` (static fallback)
If the LLM call fails, this function generates terms with hard-coded rules:
- "paralegal" → adds "Legal Assistant", "Legal Support", "Litigation Support"
- "attorney" → adds "Counsel", "Lawyer", "Associate"
- etc.

**Called from:** `generate_search_terms()` as fallback.

---

### `_NoiseFilter`
```python
class _NoiseFilter:
    def write(self, text):
        stripped = text.strip()
        if len(stripped) > 10 and all(c in "=-*" for c in stripped):
            return   # Drop this line
        self._wrapped.write(text)
```
Browser-Use internally prints 80-character separator lines like `================`.
This filter wraps `sys.stdout` and silently drops those lines so Azure Functions logs stay clean.

**Used in:** `scrape_site()` around `agent.run()`:
```python
sys.stdout = _NoiseFilter(sys.stdout)
result = await agent.run(max_steps=60)
sys.stdout = original_stdout   # always restored in finally
```

---

### `make_step_callback(firm_name, last_url_container, verbose)`
Returns an async function that gets called after EVERY agent step.

```python
async def on_step(browser_state_summary, model_output, step_number):
    # 1. Track the URL the agent is currently on
    last_url_container[0] = browser_state_summary.url

    # 2. Print one line per step:
    # [Jones Day] Step  3 | type_text(paralegal)  ->  "Type role in search box"
    print(f"  [{firm_name}] Step {step_number:>2} | {action_part}{goal_part}")
```

**Why `last_url_container` is a list:** Python closures can't reassign outer variables directly.
Using a list `[url]` lets the callback update `last_url_container[0]` and the caller sees the change.

**Why track the URL:** The agent's final result sometimes has a null `job_url`.
The last real URL the agent visited is a better fallback than the starting `careers_url`.

**Called from:** `scrape_site()` as the `register_new_step_callback` parameter to the Agent.

---

### `scrape_site(site, role, search_terms)` — the core function

This is where the Browser-Use agent is built and run.

```python
async def scrape_site(site: SiteConfig, role: str, search_terms: list) -> list[ScrapeResult]:
```

**Step 1 — Build the search terms hint:**
```python
terms_hint = (
    "SEARCH ALL TERMS — MANDATORY:\n"
    "Terms to search:\n"
    "  1. paralegal\n"
    "  2. Legal Assistant\n"
    "  3. Legal Support\n"
    ...
)
effective_hints = (site.navigation_hints or "") + terms_hint
task = strategy.get_navigation_task(role, site.careers_url, effective_hints)
```
The final `task` string is the complete AI prompt — strategy instructions + firm-specific hints + search terms list.

**Step 2 — Create a unique Chrome profile directory:**
```python
unique_profile_dir = os.path.join(tempfile.gettempdir(), f"bu_{uuid.uuid4().hex[:12]}")
```
**Why:** When 5 browsers run concurrently, they all try to use the same Chrome profile directory by default. Chrome uses a `SingletonLock` file to prevent two instances from sharing a profile. This would crash all but the first browser. By giving each a unique temp directory, they never conflict.

**Step 3 — Create BrowserProfile:**
```python
profile = BrowserProfile(
    user_data_dir=unique_profile_dir,
    headless=True,
    disable_security=True,
    extra_chromium_args=["--no-sandbox", "--disable-setuid-sandbox"],
    viewport={"width": 1280, "height": 900},
)
```
- `headless=True` — no visible browser window (required in production)
- `disable_security=True` — allows cross-origin requests (career portals often use iframes)
- `--no-sandbox` — required inside Docker containers (Linux kernel sandbox not available)
- `viewport` — page width/height; affects what elements the AI sees

**Step 4 — Set agent parameters:**
```python
is_videsktop = (site.strategy.value == "videsktop")
max_steps        = 60 if is_videsktop else 20
actions_per_step = 1  if is_videsktop else 3
```
- `max_steps=60` — viDesktop needs more steps: navigate (10) + search all terms (15) + open each job (35)
- `actions_per_step=1` — viDesktop uses ASP.NET postbacks. If the AI does 3 actions in one step,
  action 1 fires a postback, the page reloads, but actions 2 and 3 still reference old element indices
  from the pre-reload page → wrong clicks. Forcing 1 action per step makes the AI re-read the page
  after every action.

**Step 5 — Set initial_actions:**
```python
initial_actions = [{"go_to_url": {"url": site.careers_url}}]
```
Before the AI takes over, Playwright navigates directly to the careers URL.
**Why:** Without this, the agent starts at `about:blank` and wastes a step (or gives up immediately).

**Step 6 — Build the Agent:**
```python
agent = Agent(
    task=task,                         # The full AI prompt
    llm=llm,                           # Azure OpenAI GPT instance
    browser_profile=profile,           # Chrome settings
    use_vision=False,                  # Text-only (no screenshots sent to AI)
    max_actions_per_step=actions_per_step,
    max_failures=5,                    # Retry up to 5 times on error
    generate_gif=False,
    include_attributes=include_attrs,  # Extra HTML attributes shown to AI (href, target, aria-label)
    initial_actions=initial_actions,   # Go to careers URL first
    register_new_step_callback=make_step_callback(site.name, last_url, verbose),
)
```

**Step 7 — Run the agent:**
```python
result = await asyncio.wait_for(
    agent.run(max_steps=60),
    timeout=600    # 10 minute hard timeout
)
```
`agent.run()` is the Browser-Use library call. It:
1. Runs `initial_actions` (go to careers URL)
2. Sends the current page state (HTML elements, URL) to the LLM
3. LLM decides what to do next (click, type, scroll, extract...)
4. Browser executes the action
5. Repeat until `done()` is called or max_steps reached

**Step 8 — Parse the agent's output:**
```python
final = result.final_result()   # The text the agent returned at done()

if is_videsktop:
    extractions = parse_multi_extraction(final, role, last_url[0])
```
The agent returns raw text — usually a JSON string like:
```json
{"jobs": [{"role_title": "Paralegal", "salary_min": "$75,000", ...}]}
```
`parse_multi_extraction()` extracts all job objects from this text.

**Step 9 — Build ScrapeResults:**
```python
for extraction in extractions:
    site_results.append(ScrapeResult(
        firm_name=site.name,
        strategy_used="videsktop",
        role_searched=role,
        extraction=extraction,
        status="success",
        scrape_duration_sec=duration,
    ))
return site_results
```

**Step 10 — Cleanup:**
```python
finally:
    shutil.rmtree(unique_profile_dir, ignore_errors=True)
```
Always delete the temp Chrome profile directory after the scrape, whether it succeeded or failed.

---

### `parse_multi_extraction(raw_result, fallback_role, fallback_url)`

The agent returns a text string. This function robustly extracts job objects from it.

**Why robust parsing is needed:** The LLM doesn't always return perfectly clean JSON.
Sometimes it wraps the output in markdown code blocks (` ```json ... ``` `),
sometimes it embeds JSON in prose text, sometimes it returns a single job instead of the `{"jobs":[...]}` format.

Parse order:
1. Direct `json.loads()` on the raw string
2. Extract from markdown code block
3. Bracket-matching — find `{...}` objects embedded in prose
4. Try as single-job format (fallback to `parse_extraction`)

Returns: `list[JobExtraction]`

---

## 7. Main Orchestrator — main.py

This file ties everything together. It's called both from the CLI and from Azure Functions.

---

### `load_roles()`
```python
def load_roles() -> list[str]:
    with open("config/roles.json") as f:
        return json.load(f)   # ["paralegal", "litigation", "business development"]
```
Reads the roles to scrape. The scheduled run processes all roles.
A manual HTTP trigger can override this with a specific role list.

---

### `load_sites(strategy, site_filter)`
```python
def load_sites(strategy, site_filter) -> list[SiteConfig]:
    raw = json.load(open("config/all_firms.json"))
    sites = [SiteConfig(**s) for s in raw]

    if strategy != "all":
        sites = [s for s in sites if s.strategy.value == strategy]
    if site_filter != "all":
        sites = [s for s in sites if site_filter.lower() in s.name.lower()]
    return sites
```
Loads all 56 firms. Optionally filters by strategy type or firm name.
Example: `load_sites("videsktop", "Jones Day")` returns only Jones Day.

---

### `run_batch(sites, role, concurrency, output_file, search_terms)`
```python
async def run_batch(sites, role, concurrency=5, ...) -> list[ScrapeResult]:
    semaphore = asyncio.Semaphore(concurrency)   # max 5 browsers at once

    async def run_one(site):
        async with semaphore:
            results = await scrape_site(site, role, search_terms)
            ...

    tasks = [asyncio.create_task(run_one(site)) for site in sites]
    results_list = await asyncio.gather(*tasks)
```
**Why asyncio.Semaphore:** Creates all tasks at once but limits how many run simultaneously.
With 56 firms and concurrency=5, at most 5 Chromium browsers are open at any moment.
Without this limit: 56 browsers would open simultaneously → system runs out of RAM.

---

### `run_scraper(strategy, site_filter, concurrency, output_file, storage_type, roles)`
```python
async def run_scraper(...) -> dict:
```
The main entry point — called by both CLI and Azure Functions.

**Flow:**
```
1. Set environment variables (headless, telemetry, etc.)
2. Load roles from config/roles.json (or use provided roles)
3. Load sites from config/all_firms.json
4. Connect to storage (Cosmos DB or local JSON)
5. Print run header
6. For each role:
   a. generate_search_terms(role)  ← LLM generates 4 alternatives
   b. run_batch(sites, role, ...)  ← scrape all 56 firms concurrently
   c. storage.save_batch(results)  ← save to Cosmos DB
   d. Print role summary
7. Print final summary
8. Return summary dict
```

**Why `run_scraper()` is a separate function from `main()`:**
`main()` reads from `argparse` + `.env`. But Azure Functions can't use argparse —
it calls `run_scraper()` directly with explicit parameters.
Both paths share the same core logic.

---

### `main()` — CLI entry point
```python
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", ...)
    parser.add_argument("--filter", ...)
    ...
    args = parser.parse_args()
    await run_scraper(strategy=args.strategy, site_filter=args.filter, ...)

if __name__ == "__main__":
    asyncio.run(main())
```
Used when running locally: `python main.py --filter "Jones Day"`.

---

## 8. Storage Layer — app/storage.py

---

### `_build_document(result, run_id)`
```python
def _build_document(result: ScrapeResult, run_id: str) -> dict:
    doc = {
        "id":       str(uuid.uuid4()),   # unique document ID
        "role":     result.role_searched, # partition key — all paralegal jobs together
        "run_id":   run_id,              # same UUID for all docs in one execution
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "firm_name": result.firm_name,
        "status":   result.status,
        ...
    }
    if result.extraction:               # only if job was found
        doc["salary_min"] = result.extraction.salary_min
        doc["salary_max"] = result.extraction.salary_max
        ...
    return doc
```
Flattens the nested `ScrapeResult → JobExtraction` structure into a flat dict for Cosmos DB.
**Why flat:** Cosmos DB queries on nested fields are awkward. Flat documents are easier to query.

---

### `CosmosStorage`
```python
class CosmosStorage:
    def __init__(self):
        self.run_id = str(uuid.uuid4())   # shared by all saves in this run

    async def connect(self):
        from azure.cosmos.aio import CosmosClient
        from azure.cosmos import PartitionKey

        # Parse connection string to extract bare key
        if "AccountKey=" in key:
            key = extract AccountKey value

        self.client = CosmosClient(endpoint, credential=key)

        # Auto-create database and container if they don't exist
        db = await self.client.create_database_if_not_exists(id="hrsalarydb")
        self.container = await db.create_container_if_not_exists(
            id="agent_job_results",
            partition_key=PartitionKey(path="/role"),
        )

    async def save(self, result: ScrapeResult):
        doc = _build_document(result, self.run_id)
        await self.container.upsert_item(doc)

    async def save_batch(self, results: list[ScrapeResult]):
        for result in results:
            await self.save(result)
```

**Why partition key `/role`:**
Cosmos DB splits data across physical partitions. All documents with the same `role` value go to the same partition. This makes queries like `SELECT * FROM c WHERE c.role = "paralegal"` very fast — Cosmos reads only one partition instead of scanning everything.

**Why `upsert_item` not `create_item`:**
If the same job is scraped twice (e.g. you trigger the scraper manually then it also runs on schedule), upsert updates the existing document instead of throwing a duplicate error.

**COSMOS_KEY parsing — why needed:**
The connection string looks like:
`AccountEndpoint=https://hrsalarydb.documents.azure.com:443/;AccountKey=abc123==;`
The SDK needs only the bare key (`abc123==`), not the full connection string.
The code splits on `;` and finds the part starting with `AccountKey=`.

---

### `LocalStorage`
Identical interface to `CosmosStorage` but writes to `results.json` on disk.
Used when `STORAGE=local` in env vars, or when Cosmos DB connection fails.

---

## 9. Azure Functions Entry Point — function_app.py

---

### `_run_state` — in-memory run tracker
```python
_run_state = {
    "status":     "idle",    # idle | running | completed | failed
    "started_at": None,
    "summary":    None,
    "error":      None,
}
```
Stores the state of the current (or last) run in memory.
The `/api/status` endpoint reads this dict and returns it as JSON.
**Limitation:** If the Function App restarts, this resets to `idle`. In production this is fine
because you have Cosmos DB as the persistent record.

---

### Timer Trigger — `scheduled_scrape`
```python
@app.timer_trigger(
    schedule="0 30 18 * * *",    # 6-part CRON: seconds minutes hours day month weekday
    run_on_startup=False,
)
async def scheduled_scrape(timer: func.TimerRequest):
    await _execute_scrape(strategy="videsktop", site_filter="all", roles=None)
```
`0 30 18 * * *` = fire at second=0, minute=30, hour=18 (UTC) every day = **12:00 AM IST**.

**Why Azure Functions needs 6-part CRON:** Standard CRON is 5 parts (no seconds field).
Azure Functions adds a seconds field as the first field.

---

### HTTP Trigger — `http_trigger`
```python
@app.route(route="trigger", methods=["GET","POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def http_trigger(req: func.HttpRequest) -> func.HttpResponse:

    if _run_state["status"] == "running":
        return 409 Conflict    # already running

    body = req.get_json()
    strategy    = body.get("strategy") or env STRATEGY
    site_filter = body.get("filter")   or env SITE_FILTER
    roles       = body.get("roles")    or None

    _run_state["status"] = "running"

    # Start scrape in background — does NOT block HTTP response
    _background_task = asyncio.create_task(_execute_scrape(...))

    return 202 Accepted   # return immediately
```

**Why `asyncio.create_task()` instead of `await`:**
If you `await _execute_scrape(...)`, the HTTP response doesn't return until the scrape finishes (60+ minutes). Azure Functions would time out and kill the worker. By using `create_task()`, the scrape runs in the background and the HTTP response returns immediately (202).

**Why `auth_level=ANONYMOUS`:**
By default Azure Functions HTTP triggers require a `?code=<key>` in the URL.
We removed this requirement so you can call the endpoint directly without managing API keys.
The endpoint is not sensitive (it only starts a scrape) and access is controlled at the Azure level.

---

### Status Endpoint — `status_trigger`
```python
@app.route(route="status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def status_trigger(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(_run_state))
```
Returns the current `_run_state` dict. Poll this after triggering to track progress.

---

### `_execute_scrape(strategy, site_filter, roles)`
```python
async def _execute_scrape(strategy, site_filter, roles):
    try:
        from main import run_scraper
        summary = await run_scraper(strategy=strategy, ...)
        _run_state["status"] = "completed"
        _run_state["summary"] = summary
    except Exception as e:
        _run_state["status"] = "failed"
        _run_state["error"] = str(e)[:500]
```
The actual scrape runner. Both the timer trigger and HTTP trigger call this.
It imports `run_scraper` from `main.py` and updates `_run_state` when done.

---

## 10. How the Agent Is Built

This is the most important section — understanding what parameters the Browser-Use agent receives and why.

```python
agent = Agent(
    task                       = task,
    llm                        = llm,
    browser_profile            = profile,
    use_vision                 = False,
    max_actions_per_step       = 1,       # videsktop: 1 (postback safety), others: 3
    max_failures               = 5,
    generate_gif               = False,
    include_attributes         = include_attrs,
    initial_actions            = [{"go_to_url": {"url": site.careers_url}}],
    register_new_step_callback = make_step_callback(site.name, last_url, verbose),
)
```

| Parameter | What it is | Why we set it this way |
|-----------|-----------|----------------------|
| `task` | The full AI prompt (strategy instructions + search terms) | Tells the AI exactly what to do on this specific portal |
| `llm` | Azure OpenAI GPT instance | The brain — decides every action |
| `browser_profile` | Chrome settings (headless, temp dir, --no-sandbox) | Each firm gets an isolated Chrome instance; --no-sandbox for Docker |
| `use_vision=False` | Don't send screenshots to the AI | Saves tokens; HTML element listing is sufficient for these portals |
| `max_actions_per_step=1` | How many browser actions per LLM call | viDesktop: 1 (postback reloads page); others: 3 (faster) |
| `max_failures=5` | Retry count before giving up | Handles temporary network errors or stale element references |
| `include_attributes` | Extra HTML attributes shown to AI | `href` and `target` let AI detect target="_blank" links; `aria-label` for accessibility text |
| `initial_actions` | Actions run before the AI starts | Navigate to careers URL so AI doesn't start at blank page |
| `register_new_step_callback` | Called after every AI step | Tracks current URL + prints progress logs |

**What `include_attributes` contains and why:**
```python
include_attrs = [
    'title', 'type', 'name', 'role', 'aria-label', 'placeholder', 'value',
    'alt', 'aria-expanded', 'href', 'target',
]
```
Browser-Use shows the AI a simplified element listing like:
```
[23] <a href="RecApplicantEmail.aspx?Tag=abc123" target="_self">Apply Now</a>
```
Without `href` in `include_attributes`, the AI would only see `[23] <a>Apply Now</a>` and wouldn't know the URL to navigate to. Without `target`, it couldn't detect `target="_blank"` links that would open new tabs.

---

## 11. Function Call Map

```
function_app.py
│
├── scheduled_scrape()  ──────────────────────────────────────────────┐
├── http_trigger()  ──→  asyncio.create_task(_execute_scrape())  ─────┤
└── status_trigger()                                                   │
                                                                       ▼
                                                    _execute_scrape()
                                                           │
                                                           ▼
main.py ← from main import run_scraper
│
└── run_scraper()
        │
        ├── load_roles()                    reads config/roles.json
        ├── load_sites()                    reads config/all_firms.json → list[SiteConfig]
        ├── CosmosStorage().connect()       app/storage.py
        │
        └── for each role:
                │
                ├── generate_search_terms(role)          app/scraper.py
                │       └── get_llm().ainvoke(prompt)
                │
                └── run_batch(sites, role, ...)
                        │
                        └── asyncio.gather (concurrency=5)
                                │
                                └── scrape_site(site, role, terms)    app/scraper.py
                                        │
                                        ├── get_strategy(site.strategy) app/strategies/__init__.py
                                        │       └── ViDesktopStrategy()
                                        │
                                        ├── strategy.get_navigation_task(role, url, hints)
                                        │       └── get_extraction_task() + hints + hard rules
                                        │
                                        ├── BrowserProfile(unique_dir, headless, --no-sandbox)
                                        ├── get_llm()
                                        │
                                        ├── Agent(task, llm, profile, ...)
                                        ├── agent.run(max_steps=60)
                                        │       ↕ AI navigates, extracts
                                        │
                                        ├── parse_multi_extraction(result)
                                        │       └── list[JobExtraction]
                                        │
                                        └── list[ScrapeResult]
                                                │
                                                ▼
                                    storage.save_batch()        app/storage.py
                                        └── CosmosClient.upsert_item(doc)
```

---

## 12. Config Files

### `config/all_firms.json`
```json
[
  {
    "name": "Jones Day",
    "careers_url": "https://jonesdaystaffrecruitselfapply.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx?Tag=...",
    "strategy": "videsktop"
  },
  ...
]
```
56 law firms. Each entry becomes a `SiteConfig` object in `load_sites()`.

### `config/roles.json`
```json
["paralegal", "litigation", "business development"]
```
The three roles scraped in every run. To add more roles, add strings to this array and rebuild the Docker image.

---

## Key Design Decisions — Summary

| Decision | Why |
|----------|-----|
| AI agent instead of CSS selectors | Each firm's portal has different HTML structure; AI adapts without code changes |
| One Chrome profile per firm | Prevents `SingletonLock` crashes when running 5 browsers concurrently |
| `--no-sandbox` flag | Required inside Docker; Chrome's sandbox needs kernel features not available in containers |
| `actions_per_step=1` for viDesktop | ASP.NET postbacks reload the page; batching 3 actions causes wrong clicks on stale elements |
| Cosmos DB partition key `/role` | Groups all paralegal jobs together → fast cross-firm queries per role |
| `asyncio.create_task()` in HTTP trigger | Returns 202 immediately; prevents Azure Functions worker timeout on 60-min scrapes |
| `run_scraper()` as importable function | Shared by CLI (`main()`) and Azure Functions (`_execute_scrape()`) without code duplication |
| COSMOS_KEY connection string parsing | Azure Portal gives full connection string; SDK needs only the bare key |
| `upsert_item` not `create_item` | Idempotent — re-running the scraper doesn't fail with duplicate ID errors |
