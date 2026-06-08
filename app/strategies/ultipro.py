"""Strategy for UltiPro / UKG career sites -- navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class UltiProStrategy(BaseStrategy):

    def get_initial_actions(self, role: str, url: str) -> List[dict]:
        return [
            {"go_to_url": url},
            {"wait": 3},
        ]

    def get_extraction_task(self, role: str, url: str) -> str:
        return f"""
You are on a law firm careers page at {url}.
Your goal is to find ALL jobs matching "{role}" and extract salary, experience, location, and URL for EVERY match.

STEP 1 -- FIND THE STAFF CAREERS SECTION
- Look for links labelled: "Professional Staff", "Business Professionals",
  "Staff Openings", "US Professional Staff", or similar.
- Do NOT click "Attorneys", "Lawyers", "Associates", or any attorney section.
- Click the staff link (e.g. "View Open Positions"). It will redirect to UltiPro/UKG
  (URL contains "recruiting.ultipro.com").

STEP 2 -- SEARCH ON ULTIPRO
- This is a KnockoutJS application. Wait 4 seconds for job cards to finish loading.
- Find the search box (id: SearchInput) and type "{role}", then press Enter.
- Wait for job cards to refresh.
- If 0 results, try a shorter keyword.

STEP 3 -- COLLECT ALL MATCHING JOBS
- Read ALL job cards visible that match "{role}".
- For each matching card:
  - Click the job title link to open the detail page.
  - Extract: title, salary, experience, location, department, and the page URL.
  - Click the browser Back button to return to the job list.
  - Continue to the next matching card.
- Collect up to 10 matching jobs.

STEP 4 -- EXTRACT FROM EACH JOB DETAIL
For each job, extract:
- Job title: heading near the top
- Salary: check page source for JSON-LD block (type="application/ld+json") with
  baseSalary.minValue and maxValue -- most reliable. If absent, scan description
  for "$X,XXX" or "$XX/hr" patterns. Use "Not listed" if absent.
- Experience: in "Requirements" or "Qualifications"
- Location: "City, State" pattern near title
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
