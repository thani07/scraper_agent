"""Strategy for viDesktop / VI Recruit career portals.

Covers all patterns from VIDESKTOP_SCRAPER_GUIDE.md:
  - S3  Navigation: organic discovery, entry-URL + link text, direct portal
  - S4  Grid structure, row href capture (3-strategy fallback)
  - S5  Keyword search + silent networkidle
  - S6  Pagination (Next button detection)
  - S7  Row clicking, new-tab handling, RecJobView confirmation
  - S8  RecApplicantEmail.aspx?Tag=UUID is unique per job (acceptable job URL)
  - S9  Timeout standards
  - S10 Popup / cookie-banner dismissal (11 selectors)
  - S11 Location validation rules
  - S12 Salary extraction (6 patterns, K-suffix, hourly conversion)
  - S13 Title blocklist filtering
  - S14 Deduplication key
  - S18 Edge cases (expired Tag URL, empty grid, postback failures)

Multi-job output: returns {"jobs": [...]} with ALL matching positions found.
"""

from typing import List
from .base import BaseStrategy


class ViDesktopStrategy(BaseStrategy):

    def get_initial_actions(self, role: str, url: str) -> List[dict]:
        return [
            {"go_to_url": url},
            {"wait": 3},
        ]

    def get_extraction_task(self, role: str, url: str) -> str:
        return f"""
You are a web scraping agent specialised in VI Recruit / ViDesktop job portals used by law firms.
Starting URL: {url}
Goal: find ALL jobs matching "{role}", open each detail page, and extract salary, experience, location, and job URL for EVERY matching position.

=========================================================
SECTION A -- PORTAL IDENTIFICATION
=========================================================
A ViDesktop portal is identified by ALL of these:
  - URL path contains /viRecruitSelfApply/RecDefault.aspx
  - Page has a job-listing grid/table
  - Page has a keyword search input

URL types you will encounter:
  - Tag-based (may expire):   ?Tag=<UUID>
  - Filter-based (permanent): ?FilterREID=<N>&FilterJobCategoryID=<N>  <- prefer these

=========================================================
SECTION B -- POPUP / COOKIE BANNER DISMISSAL
=========================================================
Before doing anything else on any page, try to dismiss any popup or cookie banner.
Try these selectors in order (silently skip if not found -- do NOT fail):
  1. button[aria-label='Close']
  2. button[aria-label='Dismiss']
  3. button.close
  4. button.modal-close
  5. button.popup-close
  6. button[id*='accept']
  7. button[id*='cookie']
  8. [data-dismiss='modal']
  9. [data-action='close']
  10. button[class*='cookie']
  11. a[class*='close']
Click whichever is visible. If none are visible, proceed immediately.

=========================================================
SECTION C -- WRONG PAGE DETECTION & RECOVERY
=========================================================
After EVERY navigation action, immediately check the page.
You are on a WRONG page if you see any of:
  - Browser errors: "ERR_", "This site can't be reached", "Network Error"
  - HTTP errors: "404", "403", "Page Not Found", "Access Denied"
  - Irrelevant pages: news, attorney profiles, office pages, press releases, alumni pages
  - "Invalid Tag" message or a completely empty grid with no rows at all
Recovery: click go_back IMMEDIATELY -> try a different link or approach.
If go_back fails: navigate directly to {url} and start over.

=========================================================
SECTION D -- PHASE 1: NAVIGATE TO THE VIDESKTOP PORTAL
=========================================================
You are on the firm's careers page. Navigate to the ViDesktop portal using whichever pattern applies:

PATTERN D1 -- You are already on the portal (URL contains /viRecruitSelfApply/RecDefault.aspx):
  Skip to Section E immediately.

PATTERN D2 -- Organic discovery (you are on a firm careers page):
  Step 1: Dismiss any popups (Section B).
  Step 2: Look for accordion / expandable sections. If you see
          button[aria-expanded='false'], .accordion-toggle, or [class*='expand'] elements,
          click them to expand all sections before searching for links.
  Step 3: Search the page for a link whose href contains "viglobalcloud" or "viRecruitSelfApply".
          If found: extract the href value and use go_to_url in the current tab.
          Do not click it directly if it has target="_blank".
  Step 4: If no direct portal link found, look for text links in this order:
          "Available Positions", "search and apply for available positions",
          "External Self Apply", "Click here to view current openings",
          "Apply Online", "Career Opportunities", "View Open Positions",
          "Search Staff Openings", "View Positions", "Current Openings"
          For each candidate link: check its target attribute first.
          If target="_blank" -> extract href and use go_to_url (never click directly).
          If no target or target="_self" -> click normally.
  Step 5: If you reach an intermediate page (not yet the portal, not an error):
          repeat steps 2-4 on the intermediate page.

Links you must NEVER click during navigation:
  NOT "Attorneys", "Lawyers", "Associates", "Partners", "Of Counsel", "Legal Careers"
  NOT "Alumni", "Diversity", "Pro Bono", "Newsroom", "Press", "Events", "About", "People"
  NOT Any link to a PDF, news article, or press release

PATTERN D3 -- Entry URL + Link Text (for Reed Smith, Winston & Strawn style):
  If the careers page has a button/link labelled "External Self Apply" or
  "search and apply for available positions":
  - Extract the href attribute of that link
  - Navigate directly to that href using go_to_url (avoids new-tab issues)
  - If navigation opens a new tab, close the new tab and navigate the main page to the same URL

After reaching the portal: confirm the URL contains /viRecruitSelfApply/RecDefault.aspx.
If it does not, you are not on the portal yet -- continue following links.

=========================================================
SECTION E -- PHASE 2: WAIT FOR AND VERIFY THE JOB GRID
=========================================================
Wait for the job listing grid to become visible. Try these selectors in order:
  1. #contentPlaceHolder_gridviewList       <- used by 90%+ of portals
  2. #contentPlaceHolder_dataGridMain
  3. [id*='gridview']
  4. [id*='dataGrid']
  5. [id*='GridView']
  6. table.grid
  7. table[class*='grid']

If none are visible after waiting:
  - The portal may be down or the Tag URL may have expired
  - Try navigating to {url} and starting organic discovery again
  - If still no grid, set jobs to empty array and return

After the grid is visible: note the grid selector that worked (you will need it again after search).

=========================================================
SECTION F -- PHASE 3: KEYWORD SEARCH (search ALL provided terms)
=========================================================
You have been given a list of search terms at the end of this prompt under
"SEARCH ALL TERMS -- MANDATORY". You must search EVERY term on this site.

Do NOT stop after the first term that finds results.
Search all terms, collect jobs from each, deduplicate by title at the end.

For EACH term in your list:

  Step F1: Find the keyword search input (id="contentPlaceHolder_textKeyWord").
           Clear any existing text completely. Type the current search term.

  Step F2: Submit the search -- try in this order:
           Attempt 1: Press Enter in the search box
           Attempt 2: Click #contentPlaceHolder_buttonSearch
           Attempt 3: Click #contentPlaceHolder_linkBtnSearch

  Step F3: Wait for grid to update after postback.
           This is ASP.NET WebForms -- Search triggers a full page reload (postback).
           The URL does NOT change. The grid content updates.
           Wait until the grid selector is visible again before reading results.

  Step F4: Read ALL matching rows from the grid for this term.
           Add any new jobs to your candidate list (skip if title already collected).

  Step F5: Repeat F1-F4 for the next term in the list.

After ALL terms have been searched:
  - Deduplicate candidates by job title (keep first occurrence, skip exact duplicates)
  - Proceed to SECTION G with the combined candidate list

Only proceed to SECTION J (return empty) if ALL terms returned 0 rows.

=========================================================
SECTION G -- PHASE 4: SCAN ALL MATCHING ROWS (build candidate list)
=========================================================
WARNING: CRITICAL -- VIDESKTOP POSTBACK BEHAVIOUR (read before doing anything):
  viDesktop uses ASP.NET WebForms. Clicking any job row fires a __doPostBack() call
  that replaces the page content IN PLACE. page.url NEVER changes -- it stays at
  RecDefault.aspx FOREVER, even after you see the job detail content.
  NEVER use page.url as job_url on viDesktop.
  The correct job URL must be extracted from HTML -- either from the row before clicking,
  or from the page content after the postback renders the detail view.

STEP G0 -- COMPUTE PATH PREFIX (save this, you need it to build all absolute URLs):
  Take the current listing URL (the RecDefault.aspx URL you are on now).
  Strip "RecDefault.aspx" and everything that follows it (query string included).
  What remains is the PATH_PREFIX.
  Example:
    Listing URL : https://jonesdaystaffrecruitselfapply.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx?Tag=d92de008-89e5-424b-890b-d1ee4223566b
    PATH_PREFIX : https://jonesdaystaffrecruitselfapply.viglobalcloud.com/viRecruitSelfApply/
  Special case -- Thompson Hine and a few others use /videsktop/viRecruitSelfApply/:
    Strip to the last "/" before RecDefault.aspx.
  Save PATH_PREFIX now. You will prepend it to every relative href to make it absolute.

STEP G1 -- READ ALL MATCHING ROWS and build a candidate list:

  For EACH visible grid row:

  TITLE FILTERING -- skip any row whose title contains:
    "applies", "apply", "add to", "remove", "submit application", "more info",
    "requirement", "travel", "sitting", "standing", "walking", "reading", "typing",
    "concentration", "hearing", "vision", "speaking", "pushing", "pulling", "carrying"

  For each matching row -- check for a pre-click job URL using this priority order:

    OPTION 1 -- RecJobView href already in the row HTML:
      Find any <a href="RecJobView.aspx?FilterJobID=..."> in the row.
      Build absolute: PATH_PREFIX + that href.
      -> Store as pre_click_job_url.

    OPTION 2 -- RecApplicantEmail href already in the row HTML:
      Find any <a href="RecApplicantEmail.aspx?Tag=..."> in the row.
      Build absolute: PATH_PREFIX + that href.
      -> Store as pre_click_job_url.

    OPTION 3 -- No valid href found in the row:
      The row only has javascript:__doPostBack(...) links -- those are useless for URL building.
      DO NOT try to build a RecJobView URL from the aria-label -- it 404s on most portals.
      Store pre_click_job_url = None (will extract URL after postback click in Section H).

  DO NOT use hrefs that say "javascript:__doPostBack(...)" -- those are useless postback triggers.
  DO NOT build a RecJobView URL from the aria-label value -- RecJobView is not supported by all portals.

  CANDIDATE LIST format: [(row_index, title, pre_click_job_url_or_None), ...]
  Collect up to 15 matching rows.

STEP G2 -- PAGINATION:
  After collecting from the current page, look for more pages:
  Look for td.rptPager a with text "Next", ">", or page numbers.
  If found AND fewer than 15 candidates: click Next, wait for grid reload, collect more.

Save the portal listing URL (RecDefault.aspx URL) -- needed to return after each job.
Proceed to Section H.

=========================================================
SECTION H -- PHASE 5: OPEN EACH JOB, GET URL, EXTRACT DATA
=========================================================
For EACH candidate in your list:

  ---------------------------------------------------------
  STEP H1 -- GET THE JOB DETAIL PAGE + RECORD THE JOB URL
  ---------------------------------------------------------

  CASE A -- You have a pre_click_job_url (from Option 1, 2 or 3 in Section G):
    -> Call go_to_url(pre_click_job_url) to navigate directly. No click needed.
    -> Wait for page to load.
    -> job_url for this job = pre_click_job_url (the URL you just navigated to).
    -> page.url will now correctly show RecJobView or RecApplicantEmail -- record it.
    -> Skip to Step H2.

  CASE B -- pre_click_job_url is None (Option 4 -- no ID found before click):

    SUB-STEP B1 -- Click the arrow button to load the detail via postback:
      Click: td.rptAction a[id*='linkButtonApply'] for this row (the -> arrow button).
      Wait 2-3 seconds. The page CONTENT will change to show the job detail.
      page.url will STILL show RecDefault.aspx -- this is EXPECTED. Do NOT use it.

    SUB-STEP B2 -- Extract the real job URL from the post-click page HTML:
      Read the current page HTML (all visible content rendered after the postback).
      The detail view that just loaded contains an "Apply" or "Apply Now" link.
      Search for these href patterns IN THIS ORDER:

        PATTERN 1 (best -- always present on detail view):
          href="RecApplicantEmail.aspx?Tag=<UUID>"
          Example: href="RecApplicantEmail.aspx?Tag=f4a91c2d-8b3e-4521-a7d0-1234abcd5678"
          Build absolute: PATH_PREFIX + "RecApplicantEmail.aspx?Tag=f4a91c2d-8b3e-4521-a7d0-1234abcd5678"
          OK This is unique per job -- use this as the job URL.

        PATTERN 2 (fallback -- only if Pattern 1 not found):
          href="RecJobView.aspx?FilterJobID=<N>&..."
          Example: href="RecJobView.aspx?FilterJobID=789&FilterREID=3"
          Build absolute: PATH_PREFIX + "RecJobView.aspx?FilterJobID=789&FilterREID=3"
          WARNING: RecJobView may 404 on some portals -- prefer RecApplicantEmail when both exist.

        IGNORE: href="javascript:__doPostBack(...)" -- postback triggers, not real URLs.
        IGNORE: href containing "RecDefault" -- that is the listing page, not a job URL.

    SUB-STEP B3 -- Navigate to the absolute URL extracted in B2:
      Call go_to_url(<absolute_url>).
      Wait for page to load.
      page.url now correctly shows RecApplicantEmail.aspx or RecJobView.aspx.
      job_url for this job = that absolute URL (same one you passed to go_to_url).

    WARNING: If NEITHER pattern is found in the page HTML after the postback:
      Extract salary from the current in-page detail view content.
      Use the listing URL + "#job-" + row_index as a last-resort placeholder job_url.

  ---------------------------------------------------------
  VALIDATION -- before proceeding to extraction:
    OK VALID job_url: contains "RecJobView" or "RecApplicantEmail"
    NOT INVALID job_url: contains "RecDefault" -> you have the listing URL, NOT a job URL.
      If invalid: go back to SUB-STEP B2 and try PATTERN 2, or try CASE A with aria-label.
  ---------------------------------------------------------

  STEP H2 -- Extract all fields (Section I schema) from the current page content.

  STEP H3 -- Build the job object:
    All extracted fields + job_url = the URL recorded in Step H1.
    Add to your jobs array.

  STEP H4 -- Return to the portal listing grid:
    Call go_back(). If the grid (#contentPlaceHolder_gridviewList or similar) is visible -> good.
    If not -> navigate directly to the saved PORTAL LISTING URL (RecDefault.aspx URL).
    Wait for grid to appear before processing the next candidate.

  Repeat H1-H4 for every candidate.

=========================================================
SECTION I -- EXTRACTION SCHEMA (one object per job)
=========================================================
For each job, extract:

ROLE TITLE
  The exact job title shown as the main heading on this page (h1 or h2).

DESCRIPTION
  A 2-4 sentence plain-text summary of what this role does.
  Write it from the job posting content -- key responsibilities, purpose of the role,
  and who the person works with. Do NOT copy the full job description verbatim.
  Summarise in your own words based on what is on the page.
  If no description content is visible: null.

SALARY EXTRACTION -- apply these patterns in order (stop at first match):
  Pattern 1 (K-range):  $75K - $150K     -> min=$75,000  max=$150,000
  Pattern 2 (full range): $75,000 - $150,000  -> min=$75,000  max=$150,000
  Pattern 3 (K single): $120K            -> min=$120,000  max=null
  Pattern 4 (full):    $120,000          -> min=$120,000  max=null
  Pattern 5 (prose):   "minimum $X ... maximum $Y" -> min=X  max=Y
  Pattern 6 (hourly):  $35/hr -> multiply by 2080 -> min=$72,800  max=null
  Also fix DOM-split: "$ 75,000" (space after $) -> treat as "$75,000"

  salary_min: the minimum figure formatted as "$X,XXX" (e.g. "$75,000"). null if absent.
  salary_max: the maximum figure formatted as "$X,XXX" (e.g. "$90,000"). null if absent.
  salary_raw: WARNING: the EXACT raw salary sentence as written on the page (plain text string only).
              Example: "The salary range for this position is $75,000 - $90,000 annually."
              If no salary is visible anywhere: use the string "Not listed"
              INVALID: NEVER put a JSON object in this field.
              INVALID: NEVER copy your full output JSON into this field.
  is_hourly:  true  -- if and only if the page explicitly states the salary as an hourly rate
                       (e.g. "$35/hr", "$35 per hour", "$35/hour", "hourly rate of $35").
              false -- if the salary is annual, monthly, weekly, or no salary is listed at all.
              WARNING: Set true ONLY when the page uses "per hour", "/hr", "/hour", or "hourly" wording.
              Do NOT set true just because you converted hourly to annual in salary_min/max.

LOCATION EXTRACTION -- priority order:
  1. Text after "Location:" or "Office:" label
  2. section.sub-title h5, h6, or p (in row -- already noted)
  3. Text matching "City, ST" pattern (e.g. "Chicago, IL")
  4. null if none found

  LOCATION VALIDATION -- reject any candidate location if it:
    - Is blank, "n/a", "global", "firmwide", "multiple offices", "department",
      "practice group", "various"
    - Is longer than 60 characters (it is a sentence, not a location)
    - Contains ".", "!" or "?" (sentence punctuation)
    - Does not start with an uppercase letter
    - Does not match at least "Word, XX" pattern (city + 2-letter state)
    If rejected: set location to null.

EXPERIENCE
  experience_years: summarised (e.g. "3-5 years", "7+ years"). null if absent.
  experience_raw: the full raw requirements text as written. null if absent.

JOB URL
  job_url: The role-specific URL for this job -- extracted from row HTML or detail page HTML.
           WARNING: viDesktop uses ASP.NET postbacks: page.url NEVER changes after clicking a row.
              page.url stays at RecDefault.aspx -- NEVER use page.url as job_url.
           OK VALID: contains "RecJobView.aspx"        -- unique job detail page
           OK VALID: contains "RecApplicantEmail.aspx" -- unique apply page with job description
           INVALID: INVALID: contains "RecDefault.aspx"     -- listing page, NOT a job URL
           INVALID: INVALID: is the starting careers page URL ({url})
           How to get it (Section H): extract RecJobView or RecApplicantEmail href from
           the row HTML (before click) or from page.content() (after postback), then
           make it absolute using PATH_PREFIX and navigate to it with go_to_url.

PRACTICE AREA
  Department or practice group name (e.g. "Corporate", "Litigation"). null if absent.

=========================================================
SECTION J -- FALLBACK (NO MATCHING JOBS FOUND)
=========================================================
Use this if: search returns 0 rows AND pagination finds nothing AND broader search finds nothing.
  Return: {{"jobs": []}}

=========================================================
HARD RULES
=========================================================
- NEVER OPEN EXTRA TABS.
  Navigate all links in the current tab. If a link has target="_blank": extract its href
  and use go_to_url instead of clicking. If an extra tab opens by accident, close it.

- Maximum 60 actions total.
- After every navigation, verify you are on the expected page. Go back if wrong.
- job_url MUST contain "RecJobView" or "RecApplicantEmail" -- NEVER "RecDefault".
  viDesktop postbacks do NOT change page.url -- extract job URLs from HTML, not from page.url.
- salary_raw MUST be a plain text string. NEVER a JSON object or structured data.
- Do NOT click the same element more than once.
- Do NOT navigate to attorney, lawyer, or legal sections.
- Collect ALL matching jobs (up to 15), not just the first one.

Return ONLY a valid JSON object with no text before or after it:
{{
  "jobs": [
    {{
      "role_title": "exact title from job detail page",
      "description": "2-4 sentence summary of the role responsibilities, or null",
      "salary_min": "$X,XXX minimum salary or null",
      "salary_max": "$X,XXX maximum salary or null",
      "salary_raw": "raw salary sentence as written on page, or 'Not listed'",
      "is_hourly": true,
      "experience_years": "summarised years e.g. '3-5 years' or null",
      "experience_raw": "raw experience requirements text or null",
      "location": "City, ST or null",
      "job_url": "RecApplicantEmail or RecJobView URL for this job",
      "practice_area": "department name or null"
    }},
    ...more jobs...
  ]
}}
"""
