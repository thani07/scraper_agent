"""Strategy for iCIMS-powered career sites — navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class ICIMSStrategy(BaseStrategy):

    def get_initial_actions(self, role: str, url: str) -> List[dict]:
        return [
            {"go_to_url": url},
            {"wait": 3},
        ]

    def get_extraction_task(self, role: str, url: str) -> str:
        return f"""
You are on a law firm careers page at {url}.
Your goal is to find ALL jobs matching "{role}" and extract salary, experience, location, and URL for EVERY match.

STEP 1 — FIND THE STAFF CAREERS SECTION
- Look for links labelled: "Professional Staff", "Business Services", "Administrative Staff",
  "Staff Openings", or similar.
- Do NOT click "Attorneys", "Lawyers", "Associates", or any attorney section.
- Click the staff section link. It will redirect to an iCIMS portal (.icims.com).

STEP 2 — SEARCH ON ICIMS
- Classic iCIMS: ALL content is inside an iframe on the page. Look for an iframe —
  ALL interactions (search, job links) must happen INSIDE that iframe.
- Modern iCIMS (Talent Cloud): no iframe, interact directly.
- Find the keyword search field, type "{role}", then click Search or press Enter.
- Wait for results to load.
- If 0 results, try a shorter keyword.

STEP 3 — COLLECT ALL MATCHING JOBS
- Read ALL job listings that match "{role}".
- For each matching job:
  - Click the job title link to open the detail page.
  - Extract: title, salary, experience, location, department, and the page URL.
  - Click the browser Back button to return to the results list.
  - Continue to the next matching job.
- Collect up to 10 matching jobs.

STEP 4 — EXTRACT FROM EACH JOB DETAIL
For each job, extract:
- Job title: main heading at the top
- Salary: search the full description text for "$X,XXX - $Y,XXX" or "$XX/hr". Use "Not listed" if absent.
- Experience: in "Requirements" or "Qualifications"
- Location: near the title or in a location field
- Department: category or team label
- URL: the full page URL of the job detail

RULES:
- Do NOT click the same element more than once.
- Do NOT navigate to attorney or legal sections.
- Maximum 40 actions total.
- If 0 jobs found, return {{"jobs": []}}.

Return ONLY a valid JSON object with no text before or after it:
{{
  "jobs": [
    {{
      "role_title": "exact title from job detail",
      "salary_min": "minimum salary as '$X,XXX' or null",
      "salary_max": "maximum salary as '$X,XXX' or null",
      "salary_raw": "raw salary text or 'Not listed'",
      "is_hourly": false,
      "experience_years": "e.g. '3-5 years' or null",
      "experience_raw": "raw experience text or null",
      "location": "City, ST or null",
      "job_url": "full URL of this job detail page",
      "practice_area": "department or null"
    }}
  ]
}}
"""
