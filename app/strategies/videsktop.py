"""Strategy for viDesktop / viGlobalCloud career portals.

Navigation flow (phase by phase):
  Phase 1 — Firm careers page → find & click the Professional Staff / Business Professionals link
  Phase 2 — viDesktop portal loads (viRecruitSelfApply ASP.NET app) → search for role
  Phase 3 — Search results grid → find best matching job, capture RecJobView.aspx URL, click it
  Phase 4 — Job detail page → extract title, salary, experience, location
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
You are on a law firm careers page at: {url}
Your goal is to find a job matching "{role}" and extract its salary, experience, location, and the direct job page URL.

Follow these phases in order. Complete each phase fully before moving to the next.
After every navigation action, verify you are on the correct type of page before continuing.

━━━ HOW TO DETECT YOU ARE ON A WRONG / ERROR PAGE ━━━
After ANY click or navigation, immediately check the page. You are on a WRONG page if you see:
  - A browser error: "This site can't be reached", "ERR_", "Network Error", "Connection refused"
  - A website error: "404", "Page Not Found", "Something went wrong", "Access Denied"
  - An irrelevant page: news articles, attorney/lawyer profiles, practice area descriptions,
    office location pages, press releases, diversity pages, alumni pages
  - A page with NO links toward jobs or career openings

If you land on any of these → IMMEDIATELY click the browser back button (go_back) to return
to the previous page. Then try a DIFFERENT link on that page.
If go_back does not work → navigate directly back to: {url}

━━━ PHASE 1 — NAVIGATE FROM CAREERS PAGE TO THE STAFF JOB PORTAL ━━━

STEP 1a — Identify the correct link on the careers page.
You are looking for the section that lists open positions for NON-ATTORNEY staff.
The correct link will be labelled with one of these terms (they all mean the same thing):
  ✅ "Professional Staff"
  ✅ "Business Professionals"
  ✅ "Business Services"
  ✅ "Staff Careers" / "Staff Openings"
  ✅ "Administrative Staff"
  ✅ "Support Staff"
  ✅ "Open Positions" (only if it is inside a staff/non-attorney section)
  ✅ "View Positions" / "Search Openings" / "Search Jobs" (inside staff section)
  ✅ "External Self Apply" (if on an intermediate page before the portal)

Links you must NEVER click in Phase 1:
  ❌ "Attorneys", "Lawyers", "Associates", "Partners", "Of Counsel"
  ❌ "Legal Careers", "Attorney Careers", "Join Our Legal Team"
  ❌ "Alumni", "Diversity", "Pro Bono", "Newsroom", "Press", "Events"
  ❌ Navigation menu items like "About", "Practice Areas", "Offices", "People"
  ❌ Any link to a PDF, press release, or news article

STEP 1b — Click the correct staff link.
After clicking, verify the page you land on:
  ✅ CORRECT: the new page is either:
     - The viDesktop portal (URL contains "viRecruitSelfApply" or "viglobalcloud.com")
     - An intermediate careers page still showing staff/professional job content with more links
  ❌ WRONG: you are on an error page or an irrelevant page
     → Go back immediately and try a different link

STEP 1c — Handle intermediate pages.
Some firms have 1-2 intermediate pages between the main careers page and the viDesktop portal.
At each intermediate page, look for one of:
  - "View Open Positions", "Search Openings", "Search Staff Openings"
  - "External Self Apply", "Apply Now", "View Positions"
  - A direct link whose href contains "viglobalcloud.com" or "viRecruitSelfApply"
Click that link and verify you reach the portal.

STEP 1d — Confirm you are on the viDesktop portal.
You are on the correct portal when ALL of these are true:
  ✅ The page URL contains "viRecruitSelfApply" OR "viglobalcloud.com" OR a firm-specific
     subdomain like "selfapply.{"{firm}"}.com" or "recruiter.{"{firm}"}.com"
  ✅ The page shows a TABLE or GRID of job listings (rows of jobs with titles)
  ✅ There is a text input field for keyword search
If these are NOT true, you are not on the portal yet — keep following links.

━━━ PHASE 2 — SEARCH ON THE VIDESKTOP PORTAL ━━━

STEP 2a — Wait for the grid to fully load.
The job grid is an HTML table. Its container has id="contentPlaceHolder_gridviewList".
Wait until table rows (job listings) are visible before proceeding.
If the page is still loading or shows a spinner, wait.

STEP 2b — Enter the search keyword.
Find the search input field (id="contentPlaceHolder_textKeyWord" or similar).
Clear any existing text in it, then type exactly: {role}

STEP 2c — Submit the search.
Find the Search button (id="contentPlaceHolder_buttonSearch" or a button labelled "Search").
Click it ONCE.

STEP 2d — Wait for the ASP.NET postback to complete.
⚠️ IMPORTANT: This is an ASP.NET WebForms page. Clicking Search triggers a FULL PAGE RELOAD
(called a "postback"). The URL does NOT change, but the entire page content reloads.
You MUST wait for the page to finish reloading before reading the results.
Signs the postback is complete: the job grid is visible again and shows updated results.
Do NOT interact with the page until the grid is fully visible after the reload.

━━━ PHASE 3 — OPEN THE CORRECT JOB DETAIL PAGE ━━━

STEP 3a — Read the search results.
The grid now shows jobs matching your search. Read the job title in each row.
Find the row whose title best matches "{role}".

STEP 3b — Locate and record the job detail URL BEFORE clicking.
In the matching row, find an anchor tag (<a>) whose href contains "RecJobView.aspx".
This is the permanent URL for this specific job detail page.
RECORD THIS EXACT URL — you will use it as the job_url in your output.
Example of what it looks like: .../viRecruitSelfApply/RecJobView.aspx?...

STEP 3c — Click to open the job detail page.
Click the right-arrow icon in the row (<i class="vi vi-long-arrow-right">) OR
click the anchor link whose href contains "RecJobView.aspx".
⚠️ NEVER click a link containing "RecApplicantEmail.aspx" — that is the shared apply form,
NOT the job detail page.

STEP 3d — Verify you are on the job detail page.
After clicking, confirm the current URL contains "RecJobView.aspx".
If it does NOT, go back and try again with a different element in the same row.

━━━ PHASE 4 — EXTRACT FROM THE JOB DETAIL PAGE ━━━
You are now on the individual job detail page. Extract the following fields carefully:

  role_title      → The exact job title shown as the main heading on this page.

  salary_min      → The minimum salary figure only (e.g. "75000" or "$75,000"). null if absent.
  salary_max      → The maximum salary figure only (e.g. "90000" or "$90,000"). null if absent.
  salary_raw      → ⚠️ MUST be a plain text string — the salary sentence EXACTLY as written on
                    the page. Example: "We are offering a salary range of $75,000 - $90,000."
                    If no salary is shown anywhere on the page, use the string: "Not listed"
                    ❌ NEVER put a JSON object in this field.
                    ❌ NEVER copy your full output JSON into this field.
                    ❌ NEVER put any structured data here — only plain text from the page.

  experience_years → Summarised years of experience required (e.g. "3-5 years"). null if absent.
  experience_raw   → The raw experience/qualifications text as written on the page. null if absent.

  location         → City and state where the job is based (e.g. "Denver, CO"). null if absent.
                    Look for labels: "Office:", "Location:", or a "City, State" pattern.

  job_url          → ⚠️ MUST be the RecJobView.aspx URL of this specific job.
                    Use the URL you recorded in Step 3b.
                    ❌ NEVER use the careers page URL ({url}).
                    ❌ NEVER use the portal home page (RecDefault.aspx).
                    ❌ NEVER use the apply form (RecApplicantEmail.aspx).

  practice_area    → Department or practice group (e.g. "Corporate", "Litigation"). null if absent.

━━━ FALLBACK — NO RESULTS ━━━
If the search in Phase 2 returns no matching jobs in the grid:
  - Set role_title to "No results found"
  - Set all other fields to null except job_url
  - Set job_url to the current portal page URL (RecDefault.aspx URL)

━━━ HARD RULES ━━━
- Maximum 30 actions total across all phases.
- After every navigation, verify you are on the expected page. If not, go back.
- If you land on an error page or irrelevant page, go back immediately.
- Do NOT click any element more than once.
- Do NOT navigate to attorney, lawyer, or legal sections.
- salary_raw MUST be a plain text string from the page. Never a JSON object.
- job_url MUST contain "RecJobView.aspx". Never a careers or apply URL.

Return ONLY a valid JSON object with no extra text before or after it:
{{
  "role_title": "exact title from the job detail page",
  "salary_min": "minimum salary value or null",
  "salary_max": "maximum salary value or null",
  "salary_raw": "raw salary text as written on the page, or 'Not listed'",
  "experience_years": "summarised years or null",
  "experience_raw": "raw experience text or null",
  "location": "city and state or null",
  "job_url": "full RecJobView.aspx URL of this specific job",
  "practice_area": "department name or null"
}}
"""
