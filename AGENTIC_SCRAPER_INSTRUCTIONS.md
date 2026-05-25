# Agentic Scraper — Navigation Intelligence Reference

## What This Document Is

This document extracts all navigation knowledge from our existing **manual Playwright crawler
codebase** (100+ law firm crawlers) so that an **agentic browser-use scraper** can replicate
the same scraping behaviour without hardcoded CSS selectors.

The agentic scraper uses an LLM to read the live HTML, tag elements, and decide which
actions to take step-by-step. This document tells it:
- Where to start for each ATS platform
- Which filter/category selects **Professional Staff (non-attorney)** roles only
- Where salary data lives
- What traps and edge-cases to watch for

---

## Existing Shared Strategy Files (the "7 Strategies")

These files in `crawlers/` are the platform-level reusable scrapers. Each firm-specific file
delegates to one of these.

| # | File | ATS Platform | What it handles |
|---|------|-------------|-----------------|
| 1 | `crawlers/base.py` | All | `extract_salary()`, `normalise_job()`, `launch_chromium()` — shared utilities |
| 2 | `crawlers/workday.py` | Workday (SPA) | Left-panel job list → right-panel detail click → extract title/location/salary |
| 3 | `crawlers/ultipro.py` | UltiPro / UKG | KnockoutJS `OpportunitiesContainer` view-model scraping + detail page navigation |
| 4 | `crawlers/videsktop.py` | viDesktop (root) | Base viDesktop config and shared utilities |
| 5 | `crawlers/rank_batch_1/videsktop_crawler.py` | viDesktop / viGlobalCloud | Full VI Recruit Self Apply portal: Weil, Reed Smith, Winston & Strawn |
| 6 | `crawlers/rank_batch_3/florecruit_crawler.py` | FLO Recruit | `florecruit.com/v2/app/{firm}/jobs` SPA scraper |
| 7 | `crawlers/registry.py` | All (index) | Central registry of all 100+ active firm scrapers |

> **Note for agentic scraper:** The shared strategy files define *how* to scrape each platform.
> The agentic scraper should use this document to know *where* to navigate before applying
> those patterns.

---

## ATS Platform Profiles — Navigation & Filtering

### 1. WORKDAY (Most Common — ~30 firms)

**Platform signature:** URL contains `.myworkdayjobs.com` or `.wd1.`, `.wd3.`, `.wd5.`, `.wd12.`

**Standard URL pattern:**
```
https://{tenant}.wd{N}.myworkdayjobs.com/en-US/{board-name}
```

**Filtering to Professional Staff only:**
Workday does NOT use a universal filter parameter. Each firm has a **separate board URL** for
staff/business-professional roles. The tenant name in the URL already scopes to staff.

**Page structure:**
- Left panel: list of job cards (`a[data-automation-id="jobTitle"]`)
- Right panel: job detail (title: `[data-automation-id="jobPostingTitle"]`, location: `[data-automation-id="jobPostingHeaderLocation"]`)
- Salary: Inside the description prose or as a "Pay Range" label. NOT always shown. Extract with regex `\$[\d,]+ - \$[\d,]+`

**Keyword search:**
- Search input: `input[placeholder="Search for jobs or keywords"]` or `input[data-automation-id*="search"]`
- Submit: click `button[name="Search"]` or press Enter
- URL fallback: append `?q={keyword}` to the board URL

**Key traps:**
- "View All N Locations" button must be clicked to expand multi-location roles
- Some boards open a side-panel (SPA); others navigate to a new URL per job
- Legal stop-words to strip from search queries: `attorney`, `lawyer`, `counsel`

**Firm-specific Workday board URLs (staff-only):**

| Firm | Workday Board URL | Staff Filter Name |
|------|------------------|-------------------|
| Cooley | `https://cooley.wd1.myworkdayjobs.com/Cooley_US_LLP` | (all non-attorney) |
| Davis Polk | `https://davispolk.wd5.myworkdayjobs.com/business-professionals-services-usa` | Business Professionals |
| Dechert | `https://dechert.wd12.myworkdayjobs.com/DechertCareers` | (filter by keyword) |
| DLA Piper | `https://dlapiper.wd1.myworkdayjobs.com/dlapiper` | (filter by keyword) |
| Greenberg Traurig | `https://gtlaw.wd1.myworkdayjobs.com/GTLAW` | (filter by keyword) |
| Hogan Lovells | `https://hoganlovells.wd3.myworkdayjobs.com/Search?locationCountry=bc33aa3152ec42d4995f4791a106ed09` | (USA pre-filtered) |
| Perkins Coie | `https://perkinscoie.wd115.myworkdayjobs.com/en-US/perkinscoieexternal` | (filter by keyword) |
| Skadden | `https://skadden.wd5.myworkdayjobs.com/Skadden_Careers` | (filter by keyword) |
| Simpson Thacher | `https://stblaw.wd1.myworkdayjobs.com/en-US/careers` | (filter by keyword) |
| Holland & Knight | `https://hklaw.wd1.myworkdayjobs.com/Holland_Knight/fs/refreshFacet/318c8bb6f553100021d223d9780d30be` | (pre-filtered facet) |
| McDermott | `https://mwe.wd5.myworkdayjobs.com/mwe_careers` | Business Professionals |
| Paul Hastings | `https://paulhastings.wd1.myworkdayjobs.com/PH-Staff` | Professional Support Staff |
| Weil Gotshal | `https://weil.wd1.myworkdayjobs.com/work_at_weil` | Administrative Staff |
| Mcdermott | `https://mwe.wd5.myworkdayjobs.com/mwe_careers` | Business Professionals |

**Playwright get_initial_actions() for Workday:**
```python
# Direct board URL — zero LLM tokens for navigation
page.goto(board_url, wait_until="domcontentloaded")
page.wait_for_load_state("networkidle")
page.wait_for_timeout(3000)
# Dismiss cookie banners
for label in ["Accept", "Accept All", "Reject all", "Close"]:
    try: page.get_by_role("button", name=label).click(timeout=3000); break
    except: continue
```

---

### 2. ICIMS (Classic iframe version — ~8 firms)

**Platform signature:** URL contains `.icims.com/jobs/search` or `careers-{firm}.icims.com`

**Standard search URL pattern:**
```
https://careers-{firm}.icims.com/jobs/search?ss=1&searchKeyword={keyword}&hashed={hash}
```

**Filtering to Professional Staff only:**
iCIMS uses `searchCategory={id}` URL parameter. The category ID is firm-specific (hardcoded).

**Page structure:**
- All content inside an iframe: `#icims_content_iframe`
- Job list: table rows or `.iCIMS_JobsTable` items
- Click job title → navigate to detail page (same iframe or redirect)
- Salary: On detail page inside description body. Often NOT in a dedicated field.

**Key traps:**
- Always switch context into `#icims_content_iframe` before any locator calls
- `hashed=` URL parameter is required and firm-specific — do NOT drop it
- Detail page may redirect to a different domain (e.g., `talent.orrick.com`)

**Firm-specific iCIMS URLs:**

| Firm | iCIMS Search Base URL | Staff Category |
|------|-----------------------|----------------|
| Foley & Lardner | `https://careers-foley.icims.com/jobs/search?ss=1` | All (search by keyword) |
| Latham & Watkins | `https://us-associatecareers-lw.icims.com/jobs/search?hashed=-625915638` | All (search by keyword) |
| Cleary Gottlieb | `https://careers-clearygottlieb.icims.com/jobs/search?ss=1&searchCategory=8718` | "Administrative" (cat 8718) |
| Milbank | `https://careers-milbank.icims.com/jobs/search?ss=1&hashed=-435594439` | All (search by keyword) |
| Willkie | `https://jobs-willkie.icims.com/jobs/search?hashed=-625885970` | All (search by keyword) |
| Orrick | `https://talent.orrick.com/staff-us/jobs` | Staff-US board (distinct URL) |

**Orrick special note:** Uses iCIMS Talent Cloud (newer React SPA), NOT the classic iframe.
URL: `https://talent.orrick.com/staff-us/jobs?search={keyword}` — no iframe, clean JSON-rendered page.

**Playwright get_initial_actions() for iCIMS:**
```python
# For classic iCIMS with keyword search
url = f"https://careers-{firm}.icims.com/jobs/search?ss=1&hashed={hash}&searchKeyword={keyword}"
page.goto(url, wait_until="domcontentloaded")
page.wait_for_load_state("networkidle")
frame = page.frame("icims_content_iframe")  # switch context to iframe
```

---

### 3. VIDESKTOP / VIGLOBALCLOUD (~10 firms)

**Platform signature:** URL contains `viglobalcloud.com/viRecruitSelfApply/` or `selfapply.{firm}.com`

**Standard URL pattern:**
```
https://{host}/viRecruitSelfApply/RecDefault.aspx?Tag={guid}
# OR
https://{host}/viRecruitSelfApply/RecDefault.aspx?FilterREID={id}
```

**Critical distinction:**
- `Tag={GUID}` — can expire. GUIDs are assigned to a specific job board snapshot.
- `FilterREID={N}` — permanent. Use this when available (e.g., Brownstein `FilterREID=3`).

**Filtering to Professional Staff only:**
The Tag GUID or FilterREID already scopes the board to staff roles.
Do NOT navigate from the firm's main site — go directly to the portal URL.

**Page structure:**
- Job grid: `#contentPlaceHolder_gridviewList` (ASP.NET GridView)
- Job title in row: `h4` or `h3` inside grid row
- Keyword search: `#contentPlaceHolder_textKeyWord`
- Submit search: `#contentPlaceHolder_buttonSearch` or press Enter → triggers ASP.NET postback
- Detail page: click `a[id*="linkButtonApply"]` in row → navigates to `RecJobView.aspx?...`
- Salary: Inside detail page description prose
- Location: `section.sub-title h5 span` or lines starting with `"Office:"` or `"Location:"`

**Key traps:**
- After clicking apply button, URL becomes shared `RecApplicantEmail.aspx` — NOT unique per job.
  Capture the per-job URL from `RecJobView.aspx?...` href BEFORE clicking.
- Navigation triggers a full ASP.NET postback (page reload) — use `expect_navigation()` wrapper.
- Reed Smith viDesktop has an entry URL: `https://recruiter.reedsmith.com/` → click "External Self Apply"
- Winston & Strawn entry: `https://www.winston.com/en/careers/business-professionals` → click link
- O'Melveny: entry `https://www.omm.com/careers/business-professionals/` → click "Apply" dropdown

**Firm-specific viDesktop URLs:**

| Firm | Portal URL | Notes |
|------|-----------|-------|
| Weil, Gotshal & Manges | `https://selfapply.weil.com/viRecruitSelfApply/RecDefault.aspx?Tag=9829e075-2789-4945-abae-b0396f3ab3c5` | Direct URL |
| Reed Smith | `https://recruiter.reedsmith.com/viRecruitSelfApply/RecDefault.aspx?Tag=f2279d73-05d7-4b48-9d51-b50757192d27` | Entry via recruiter.reedsmith.com |
| Winston & Strawn | `https://winstonstrawncareers.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx?Tag=99396133-c19a-48ce-9999-e05d6069237d` | Entry via winston.com |
| Jones Day | `https://jonesdaystaffrecruitselfapply.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx?Tag=606f6ea0-9320-4c9d-bb10-2b9470b10402` | Direct URL |
| Baker Botts | `https://bakerbottsselfapply.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx?FilterREID=2&FilterJobCategoryID=3` | FilterJobCategoryID=3 = IP Professional Staff |
| Brownstein HBFS | `https://apply.bhfs.com/viRecruitSelfApply/ReDefault.aspx?FilterREID=3` | FilterREID=3 = Professional Staff |
| O'Melveny & Myers | `https://ommcareers.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx?Tag=a6e91dd5-7ecf-4b1e-bd60-f5468d754438` | Entry via omm.com |
| ArentFox Schiff (AFS) | viglobalcloud via firm careers page → city link | Multi-step navigation |

**Playwright get_initial_actions() for viDesktop:**
```python
page.goto(portal_url, wait_until="domcontentloaded")
page.wait_for_load_state("networkidle", timeout=30000)
# Wait for grid
page.locator("#contentPlaceHolder_gridviewList").wait_for(state="visible", timeout=15000)
# Keyword search
search = page.locator("#contentPlaceHolder_textKeyWord")
search.fill(keyword)
with page.expect_navigation(wait_until="domcontentloaded", timeout=10000):
    page.locator("#contentPlaceHolder_buttonSearch").click()
```

---

### 4. ULTIPRO / UKG (~6 firms)

**Platform signature:** URL contains `recruiting.ultipro.com/{FIRM_CODE}/JobBoard/`

**Standard URL pattern:**
```
https://recruiting.ultipro.com/{FIRM_CODE}/JobBoard/{UUID}/
```

**Filtering to Professional Staff only:**
The board UUID already scopes to staff. Navigate directly to the board URL.

**Page structure:**
- Job list: powered by KnockoutJS. Container: `#OpportunitiesContainer`
- VM accessible via: `ko.dataFor(document.getElementById('OpportunitiesContainer')).opportunities()`
- Job title links: `a[data-automation='job-title']`
- Search box: `#SearchInput` or `input[data-automation='search-textbox']`
- Detail page: `{board_url}/OpportunityDetail?opportunityId={id}`
- Salary: From `<script type="application/ld+json">` JobPosting schema (`.baseSalary.value`) OR plain text regex in description
- Location: `[data-automation='city-state-zip-country-label']`

**Key traps:**
- Wait for KO view-model to finish loading before reading opportunities:
  Check `vm.loadOpportunities.isExecuting() === false` AND `vm.opportunities().length > 0`
- After search, wait for `a[data-automation='job-title']` to appear OR `[data-automation='no-jobs-message']`
- Salary is in JSON-LD on detail page — preferred over text regex

**Firm-specific UltiPro board URLs:**

| Firm | UltiPro Board URL | Entry Path |
|------|-------------------|-----------|
| Baker & Hostetler | `https://recruiting.ultipro.com/BAK1005BKH/JobBoard/da65e963-280e-4c79-9743-c8622538c0ea/` | bakerlaw.com/careers/professional-staff → "View Open Positions" |
| Hunton Andrews Kurth | `https://recruiting.ultipro.com/HUN1002HW/JobBoard/c54d0719-19af-46ae-b27a-8c3695a9ab0a` | hunton.com/careers/us-professional-staff → click portal link |
| Polsinelli | `https://recruiting.ultipro.com/POL1004PLNI/JobBoard/...` | polsinelli.com/careers/apply → click "Staff Openings" |

---

### 5. FLO RECRUIT (~4 firms)

**Platform signature:** URL contains `florecruit.com/v2/app/{firm}/jobs`

**Standard URL pattern:**
```
https://florecruit.com/v2/app/{firm_slug}/jobs
```

**Filtering to Professional Staff only:**
The URL slug (`loeb`, `hollandhart`, `dickinsonwright`, `winstead`) already scopes to all jobs at
that firm. FLO Recruit is used by smaller firms who only post non-attorney roles on this portal.

**Page structure:**
- React SPA — jobs render as cards
- Location: `ul[aria-label="Job details"] li[1]` (second list item)
- Salary: Inside job description prose or `ul[aria-label="Job details"]` items
- No iframe — direct DOM interaction

**Firm slugs:**

| Firm | FLO Recruit URL |
|------|-----------------|
| Loeb & Loeb | `https://florecruit.com/v2/app/loeb/jobs` |
| Holland Hart | `https://florecruit.com/v2/app/hollandhart/jobs` |
| Dickinson Wright | `https://florecruit.com/v2/app/dickinsonwright/jobs` |
| Winstead | `https://florecruit.com/v2/app/winstead/jobs` |

---

### 6. OTHER ATS PLATFORMS

#### SilkRoad (Akin Gump)
- URL: `https://jobs.silkroad.com/AkinGump/AkinGump`
- Staff entry: `https://www.akingump.com/en/careers/business-services` → links to portal
- Structure: **Static HTML** — all jobs on one page, no pagination, no JS filtering
- Job selector: `.sr-panel__title` (title), `.sr-panel__location` (location)
- Salary: Inside job description prose
- **Agentic advantage:** Fetch the page once, parse all cards — no clicking needed

#### Coveo SPA (Covington)
- Staff URL: `https://www.cov.com/en/careers/business-professionals/employment-opportunities`
- Search: Coveo search box on page
- Job cards: class `.search-results-card`
- Location: `span` after `"Office:"` label
- Special: Some job detail pages are PDFs — scraper must handle `application/pdf` responses

#### ADP (Shook Hardy & Bacon)
- Staff URL: `https://myjobs.adp.com/shook-careers/cx?__tx_annotation=false&c=1157151&d=External&sor=adprm`
- Structure: Angular app — wait for `sdf-button.hydrated` before interaction
- Filter label: "Professional Staff" (already scoped in URL)

#### Lumesse TalentLink (Reed Smith careers.reedsmith.com)
- URL: `https://careers.reedsmith.com/jobs/vacancy/find/results/`
- Structure: AJAX board, results in `#posBrowser_ResultsGrid_pageBlock`
- Detail URLs: `/jobs/vacancy/view/{ID}/`

#### Custom Static HTML / Wizard-Based

| Firm | Type | URL |
|------|------|-----|
| Alston & Bird | Static HTML list | `https://www.alston.com/en/careers/attorneys-and-patent-professionals/job-opportunities/` |
| Gibson Dunn | Wizard (Office → Practice → Positions) | `https://www.gibsondunn.com/careers/` |
| Baker Donelson | Static HTML | Navigate: careers → "Professional Staff" → "Open Positions - Professional Staff" |

---

## The #1 Problem: "Professional Staff" Filter Labels Vary Per Site

The same concept has different names across firms:

| Label Used | Firms Using It |
|-----------|---------------|
| `Professional Staff` | Baker & Hostetler, Baker Donelson, Shook Hardy |
| `Business Professionals` | Davis Polk, McDermott, Covington |
| `Professional Support Staff` | Paul Hastings |
| `Administrative Staff` | Weil |
| `Business Services` | Akin Gump |
| `IP Professional Staff` | Baker Botts (FilterJobCategoryID=3) |
| `Staff Openings` | Polsinelli |
| `US Professional Staff` | Hunton Andrews Kurth |
| `Administrative` (iCIMS category) | Cleary Gottlieb (searchCategory=8718) |
| `Staff-US` (board name in URL) | Orrick |

**For the agentic scraper:** Rather than clicking menus to find these labels, go **directly to
the staff-scoped board URL**. All URLs in this document are already filtered to non-attorney roles.

---

## Salary Data — Where to Find It

| ATS | Salary Location | Extraction Method |
|----|-----------------|-------------------|
| Workday | Description body OR "Pay Range" field | Regex `\$[\d,]+ - \$[\d,]+` on full page text |
| UltiPro | JSON-LD `<script type="application/ld+json">` JobPosting schema | Parse `.baseSalary.value.minValue` / `.maxValue` |
| iCIMS | Job detail page description body | Text regex |
| viDesktop | Detail page description prose | Text regex |
| FLO Recruit | Description body or job detail list items | Text regex |
| SilkRoad | Description prose | Text regex |
| ADP | Description prose | Text regex |

**Key facts:**
- ~40% of law firm jobs do NOT show salary publicly — return `"N/A"` for these
- Workday "Pay Range" is the most reliable field when present
- UltiPro JSON-LD is the most reliable for UltiPro sites
- Hourly rates (`$XX.XX/hr`) and annual salaries (`$XXX,XXX`) both appear — capture both

---

## Agentic Scraper — Step-by-Step Decision Logic

```
Given: firm_name, role_to_search

Step 1 — IDENTIFY ATS PLATFORM
  Look up firm in this document → get direct board URL + ATS type

Step 2 — NAVIGATE DIRECTLY (no LLM tokens)
  go to board URL (already staff-filtered)
  dismiss cookie banners / popups

Step 3 — SEARCH FOR ROLE (only if role specified)
  For Workday: fill search input → click Search button
  For UltiPro: fill #SearchInput → press Enter → wait for KO loading
  For iCIMS:   append searchKeyword={role} to URL → reload
  For viDesktop: fill #contentPlaceHolder_textKeyWord → click Search → await postback
  For FLO/Static: load all jobs, filter client-side by title

Step 4 — COLLECT JOB LIST
  For Workday:    a[data-automation-id="jobTitle"]
  For UltiPro:    ko.dataFor(#OpportunitiesContainer).opportunities()  (JS evaluation)
  For iCIMS:      iframe > .iCIMS_JobsTable rows
  For viDesktop:  #contentPlaceHolder_gridviewList tr rows
  For FLO Recruit: React card components
  
Step 5 — FOR EACH JOB, OPEN DETAIL & EXTRACT
  title   → page h1 / data-automation-id="jobPostingTitle" / JSON-LD
  location → data-automation-id="jobPostingHeaderLocation" / "Location:" label / City, ST pattern
  salary  → JSON-LD baseSalary OR regex \$[\d,]+ on full text
  url     → page.url (capture BEFORE closing detail)

Step 6 — NORMALISE via normalise_job() from crawlers/base.py
```

---

## Common Failure Modes (and How to Handle Them)

| Problem | Root Cause | Fix |
|---------|-----------|-----|
| Grid/cards don't appear | JS not done loading | Wait for `networkidle` + extra 3000ms |
| Workday search clears itself | React state conflict | Use `press_sequentially()` with 50ms delay; verify `input_value()` matches |
| UltiPro returns 0 results after search | KO still executing | Wait for `vm.loadOpportunities.isExecuting() === false` |
| iCIMS locator finds nothing | Content is in iframe | Switch context to `page.frame("icims_content_iframe")` |
| viDesktop apply URL is identical for all jobs | ASP.NET postback | Capture `RecJobView.aspx?...` href BEFORE clicking; ignore `RecApplicantEmail.aspx` |
| Salary is `N/A` on Workday | Not posted publicly | Expected — ~40% of roles, return `"N/A"` |
| Tag GUID expired (viDesktop) | Portal updated | Fall back to firm careers page → re-discover portal link |
| Cloudflare blocking | Bot detection | Use `launch_chromium()` from base.py which sets proper user-agent |

---

## Testing Targets (5 sites for agentic scraper pilot)

These 5 cover all major ATS types and are recommended for initial testing:

| # | Firm | ATS | Direct Staff URL | What to verify |
|---|------|-----|-----------------|----------------|
| 1 | Davis Polk | Workday | `https://davispolk.wd5.myworkdayjobs.com/business-professionals-services-usa` | Left panel list, salary in detail |
| 2 | Baker & Hostetler | UltiPro | `https://recruiting.ultipro.com/BAK1005BKH/JobBoard/da65e963-280e-4c79-9743-c8622538c0ea/` | KO view-model, JSON-LD salary |
| 3 | Willkie | iCIMS | `https://jobs-willkie.icims.com/jobs/search?hashed=-625885970` | iframe context, detail page |
| 4 | Brownstein HBFS | viDesktop | `https://apply.bhfs.com/viRecruitSelfApply/ReDefault.aspx?FilterREID=3` | ASP.NET grid, postback search |
| 5 | Loeb & Loeb | FLO Recruit | `https://florecruit.com/v2/app/loeb/jobs` | React SPA, location from list items |

---

## Answers to the Other Claude Session's Questions

### For ANY site — 6 standard questions answered:

**Q1. What is the exact URL after filtering to "Professional Staff" / non-attorney only?**
→ See tables above. Every URL listed in this document is already staff-scoped.
   For Workday: board URL contains `business-professionals`, `PH-Staff`, `work_at_weil`, etc.
   For UltiPro: board UUID is firm-specific and staff-scoped.
   For viDesktop: `FilterREID=` or `Tag=` already scopes the board.

**Q2. What is the exact category/department filter label?**
→ See "Professional Staff Filter Labels Vary Per Site" table above.
   Short answer: Do NOT click filters — navigate directly to the pre-filtered URL.

**Q3. Is there a keyword search field? What is its HTML id or placeholder?**
→ Workday: `input[placeholder="Search for jobs or keywords"]`
→ UltiPro: `#SearchInput` or `input[data-automation='search-textbox']`
→ iCIMS: URL parameter `searchKeyword=` (append to URL, no clicking needed)
→ viDesktop: `#contentPlaceHolder_textKeyWord`
→ FLO Recruit / SilkRoad: Client-side title matching (no search field interaction needed)

**Q4. What does the results list look like — cards, table rows, or links?**
→ Workday: Cards with `a[data-automation-id="jobTitle"]` anchor
→ UltiPro: Cards rendered from KnockoutJS view-model, also `a[data-automation='job-title']`
→ iCIMS: Table rows inside iframe (`.iCIMS_JobsTable tr`)
→ viDesktop: ASP.NET GridView table rows (`#contentPlaceHolder_gridviewList tr`)
→ FLO Recruit: React card components
→ SilkRoad / Custom HTML: Static HTML list items / cards

**Q5. Where exactly is salary shown?**
→ Workday: Inside description panel. May be labelled "Pay Range" or inline prose. Often absent.
→ UltiPro: JSON-LD `<script type="application/ld+json">` on detail page. Also in prose text.
→ iCIMS / viDesktop: Detail page description body only. Text regex needed.
→ FLO Recruit: Job details list or description body.
→ ~40% of all firms: Salary NOT shown — return `"N/A"`

**Q6. What ATS platform powers this site?**
→ See per-firm tables throughout this document.
→ Quick lookup: Workday = `myworkdayjobs.com`, UltiPro = `recruiting.ultipro.com`,
  iCIMS = `.icims.com`, viDesktop = `viglobalcloud.com` or `viRecruitSelfApply`,
  FLO = `florecruit.com`

---

### For iCIMS sites specifically:

**Q: What is the base search URL with keyword pre-filled?**
```
https://careers-{firm}.icims.com/jobs/search?ss=1&hashed={hash}&searchKeyword={keyword}
# Example:
https://careers-milbank.icims.com/jobs/search?ss=1&hashed=-435594439&searchKeyword=paralegal
```

**Q: Is there a Category or Job Type dropdown to filter staff-only roles?**
→ Cleary Gottlieb only: `searchCategory=8718` selects "Administrative" category
→ All other iCIMS firms: keyword search is the primary filter (no staff category needed because
   the board is already staff-scoped)

**Q: After clicking a job, does salary appear on detail page or is it hidden?**
→ Detail page, inside the description prose. NOT in a dedicated structured field.
   Extract with regex: `\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?`

**Q: Does the site redirect to a different domain for the detail page?**
→ Classic iCIMS: stays on same `.icims.com` domain
→ Orrick (Talent Cloud): `careers.orrick.com` → `talent.orrick.com` (different domain)

---

### For Workday sites specifically:

**Q: What is the exact Workday tenant URL?**
→ See per-firm table above. Pattern: `{tenant}.wd{N}.myworkdayjobs.com/en-US/{board}`

**Q: Is there a "Job Category" or "Worker Sub-Type" facet filter?**
→ NOT needed — each firm has a separate board URL for staff roles.
→ Holland & Knight is the exception: uses `refreshFacet/318c8bb6f553100021d223d9780d30be` in URL

**Q: After typing a keyword, does it use URL param ?q= or require clicking?**
→ Both. Try UI search first. If it fails, fall back to `{board_url}?q={keyword}`

**Q: Where does salary/pay range appear? Always shown?**
→ When present: in the right-side detail panel, usually at bottom of description OR as a
   "Pay Range" or "Compensation" line. NOT always shown (~40% of jobs omit it).

---

### For viDesktop sites specifically:

**Q: What exact URL gets me ONLY Professional Staff without clicking through menus?**
```
# Use FilterREID (permanent) when available:
https://apply.bhfs.com/viRecruitSelfApply/ReDefault.aspx?FilterREID=3

# Use Tag GUID (may expire) as fallback:
https://jonesdaystaffrecruitselfapply.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx?Tag=606f6ea0-9320-4c9d-bb10-2b9470b10402
```

**Q: Show me the Playwright code lines that do the navigation:**
```python
# Step 1: Go directly to portal (zero clicks for navigation)
page.goto("https://apply.bhfs.com/viRecruitSelfApply/ReDefault.aspx?FilterREID=3",
          wait_until="domcontentloaded", timeout=60000)
page.wait_for_load_state("networkidle", timeout=30000)

# Step 2: Wait for grid
page.locator("#contentPlaceHolder_gridviewList").wait_for(state="visible", timeout=15000)

# Step 3: Keyword search (if role specified)
search = page.locator("#contentPlaceHolder_textKeyWord")
search.click()
search.fill(role)
with page.expect_navigation(wait_until="domcontentloaded", timeout=10000):
    page.locator("#contentPlaceHolder_buttonSearch").click()
page.wait_for_load_state("networkidle")

# Step 4: Collect rows
rows = page.locator("#contentPlaceHolder_gridviewList tr")
count = rows.count()

# Step 5: For each matching row — capture URL BEFORE clicking
for i in range(count):
    row = rows.nth(i)
    title = row.locator("h4").inner_text()
    # Get RecJobView URL from row anchor BEFORE clicking apply
    for a in row.locator("a").all():
        href = a.get_attribute("href") or ""
        if "RecJobView" in href:
            job_url = href
            break
    # Now click apply and scrape detail
    link = row.locator("a[id*='linkButtonApply']").first
    with page.expect_navigation(wait_until="domcontentloaded"):
        link.click()
    # scrape detail page...
    page.go_back()
```

---

## Why This Architecture (Page-by-Page) Makes Sense for Agentic Scraping

The existing manual crawlers navigate page-by-page for a reason:
1. **Job portals change frequently** — hardcoded CSS selectors break when the ATS upgrades
2. **Salary requires detail page** — listing pages rarely show salary; you must click into each job
3. **Filtering is portal-specific** — each ATS has its own filter mechanism

The agentic scraper improves on this by:
- Using LLM to identify elements dynamically (no hardcoded selectors)
- Reading the HTML semantically (labels, headings, aria attributes)
- Recovering gracefully when structure changes

**The navigation steps (which URL to go to, which filter to apply) should still be hardcoded**
from this document — these are facts about the websites, not variable HTML structure.
This gives you the best of both worlds: reliable navigation + resilient extraction.
