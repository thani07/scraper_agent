# Prompt: Map All Playwright Crawler Files → all_firms.json

## Context — What You Are Working With

You have access to a folder called **`crawlers/`** (or similar) that contains **200+ Playwright files written manually** — one file per website, or in some cases a single **master file** that handles multiple websites/URLs together.

Each file contains working Playwright code that:
- Navigates from a firm's original careers page to their job portal
- Searches for specific roles (paralegal, litigation, business development, etc.)
- Extracts job data (salary, experience, location, job URL)

There is also an existing **`config/all_firms.json`** file that already has some firms mapped. Use it as the reference structure and style guide — your job is to read ALL the crawler files and produce a **complete, merged JSON** covering every website across all files.

---

## Your Task

1. **Read every file** in the crawlers folder — including master files that handle multiple websites
2. **For each website** found in any file, extract the 4 fields below
3. **Produce a single JSON array** in the exact format shown at the bottom of this prompt
4. **Do not duplicate** firms already in `all_firms.json` — merge them (update hints if the code has better detail)

---

## The 4 Fields to Extract Per Website

### 1. `name`
The law firm or company name. Extract from:
- Variable names like `FIRM_NAME = "Jones Day"`
- Comments at the top of the file like `# Jones Day crawler`
- The base URL domain (e.g. `jonesday.com` → "Jones Day")

---

### 2. `careers_url`  ← **CRITICAL RULE**

This must be the **firm's own careers page URL** — the page a human would land on first when looking for jobs at that firm.

**CORRECT examples:**
```
https://www.jonesday.com/en/careers
https://www.hklaw.com/en/careers/professional-staff
https://www.davispolk.com/careers/business-professionals
https://www.kirkland.com/content/staff-careers
```

**WRONG — do NOT use these as careers_url:**
```
https://jonesdaystaffrecruitselfapply.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx  ← videsktop portal
https://davispolk.wd5.myworkdayjobs.com/...                                                 ← workday portal
https://careers-foley.icims.com/...                                                         ← icims portal
https://florecruit.com/v2/app/loeb/jobs                                                     ← florecruit portal
```

The `careers_url` is where the Playwright script **starts** — look for the first `page.goto(...)` call or the `BASE_URL` / `START_URL` variable. If the script goes to the firm's own domain first and then navigates to a portal, use the firm's domain URL.

Exception: if the crawler goes **directly** to the portal with no intermediate firm page, then use the portal URL as-is (but note this in `navigation_hints`).

---

### 3. `strategy`

Detect which ATS (Applicant Tracking System) the firm uses from the code. Use exactly these lowercase string values:

| Strategy value | How to identify in the code |
|---|---|
| `"videsktop"` | URL contains `viglobalcloud.com`, `viRecruitSelfApply`, `RecDefault.aspx`, `RecJobView.aspx`, `RecApplicantEmail.aspx`, or `__doPostBack` |
| `"workday"` | URL contains `myworkdayjobs.com` or `workday.com`; code waits for Workday job cards |
| `"icims"` | URL contains `icims.com`; code interacts with `#icims_content_iframe` or iCIMS job listings |
| `"greenhouse"` | URL contains `greenhouse.io` or `boards.greenhouse.io` |
| `"lever"` | URL contains `lever.co` or `jobs.lever.co` |
| `"ultipro"` | URL contains `recruiting.ultipro.com` or `ukg.com`; code waits for KnockoutJS job cards |
| `"florecruit"` | URL contains `florecruit.com`; React SPA job cards |
| `"direct"` | Everything else — custom ATS, static HTML job pages, SilkRoad (`silkroad.com`), ADP (`myjobs.adp.com`), Taleo, SmartRecruiters, or any non-standard portal |

---

### 4. `navigation_hints`

Write a **1–4 sentence plain English description** of what the crawler does, derived from reading the actual code. Include:

- What page it starts on and what it clicks first
- The domain/URL of the job portal it reaches (if different from careers_url)
- Any special handling: cookie banners, iframes, accordions, pagination, SPAs
- The job listing selector or search mechanism used
- Any permanent URL parameters (e.g. `FilterREID=3` means the URL never expires)
- Any known quirks: postback navigation, Tag-based URLs that may expire, iframe nesting

**Style guide** — match the tone of these existing hints:
```
"Professional Staff careers page. Click 'View Open Positions' — redirects to Workday 
(hklaw.wd1.myworkdayjobs.com) with a pre-filtered facet for staff roles. Search by keyword."

"Direct viDesktop portal URL — no navigation needed. Permanent FilterREID=3 URL, never 
expires. Grid: #contentPlaceHolder_gridviewList. Search box: #contentPlaceHolder_textKeyWord."

"Main careers page. Expand all accordion sections first (button[aria-expanded]), then scan 
hrefs for 'brownrudnickcareers.viglobalcloud.com'. Permanent URL: FilterREID=3&FilterJobCategoryID=1."
```

---

## Master Files — Multiple Websites in One File

Some files handle multiple websites. For example a file called `workday_firms.py` might loop over a list of 20 Workday firms. In this case:

- Create **one JSON entry per firm/website** — do NOT create one entry for the whole file
- Each firm gets its own `careers_url` and `navigation_hints`
- The `strategy` will likely be the same for all firms in a master file
- Look for arrays/dicts like `FIRMS = [{"name": "...", "url": "..."}]` at the top of the file

---

## Output Format

Produce a single JSON array. Every entry must follow this exact structure:

```json
[
  {
    "name": "Holland & Knight",
    "careers_url": "https://www.hklaw.com/en/careers/professional-staff",
    "strategy": "workday",
    "navigation_hints": "Professional Staff careers page. Click 'View Open Positions' — redirects to Workday (hklaw.wd1.myworkdayjobs.com) with a pre-filtered facet for staff roles. Search by keyword."
  },
  {
    "name": "Jones Day",
    "careers_url": "https://www.jonesday.com/en/careers",
    "strategy": "videsktop",
    "navigation_hints": "Main careers page. Navigate to the staff board at jonesdaystaffrecruitselfapply.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx. Tag-based URL — re-discover from careers page if expired. Grid: #contentPlaceHolder_gridviewList. Search: #contentPlaceHolder_textKeyWord."
  }
]
```

**Rules for the JSON:**
- All 4 fields are required on every entry (`name`, `careers_url`, `strategy`, `navigation_hints`)
- `strategy` must be one of the 8 values listed above — no other values allowed
- `navigation_hints` must be a single string (no line breaks inside)
- No trailing commas
- UTF-8, no escaped unicode (write `–` not `\u2013`)

---

## Steps to Follow

1. `ls crawlers/` — get the full list of files
2. For each file: read it, identify all websites it covers
3. Check if the firm already exists in `config/all_firms.json`
   - If yes: keep existing entry but update `navigation_hints` if the code has more detail
   - If no: create a new entry
4. Build the complete merged JSON
5. At the end, print a summary:
   - Total files read
   - Total websites found
   - Breakdown by strategy (how many workday, videsktop, etc.)
   - Any files you could not parse / identify the firm for

---

## What to Do If You Are Unsure

- **Can't identify the firm name?** Use the domain from the `careers_url` as the name (e.g. `hklaw.com` → `"Holland & Knight"` — look it up if needed)
- **Can't identify the strategy?** Use `"direct"` as the fallback
- **The file is a utility/helper, not a crawler?** Skip it — only process files that contain actual `page.goto()` navigation to a careers portal
- **The file has a TODO or is incomplete?** Still include it with whatever information is available; add `"[INCOMPLETE]"` at the start of `navigation_hints`

---

## Final Deliverable

A single file: **`config/all_firms_complete.json`**  
— containing every website from every crawler file, merged with the existing `all_firms.json`, deduplicated, in the format shown above.
