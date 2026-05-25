"""Strategy for UltiPro / UKG career sites — navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class UltiProStrategy(BaseStrategy):
    """
    Starts on the firm's main careers page.
    The LLM navigates to the Professional Staff section, which links to a
    UltiPro (UKG) job board (recruiting.ultipro.com), then searches and extracts.
    """

    def get_initial_actions(self, role: str, url: str) -> List[dict]:
        return [
            {"go_to_url": url},
            {"wait": 3},
        ]

    def get_extraction_task(self, role: str, url: str) -> str:
        return f"""
You are on a law firm careers page at {url}.
Your goal is to find salary and job details for the role: "{role}".

STEP 1 — FIND THE STAFF CAREERS SECTION
- Look for links or sections labelled: "Professional Staff", "Business Professionals",
  "Staff Openings", "US Professional Staff", or similar.
- Do NOT click "Attorneys", "Lawyers", "Associates", or any attorney section.
- Click the staff link. It may say "View Open Positions" or similar.
- It will redirect to a UltiPro/UKG job board (URL contains "recruiting.ultipro.com").

STEP 2 — YOU ARE NOW ON ULTIPRO (recruiting.ultipro.com)
- This is a KnockoutJS application. Wait 4 seconds for job cards to finish loading.
- Jobs appear inside a container as cards. If the page looks empty, wait longer.
- Find the search box (id: SearchInput) and type "{role}", then press Enter.
- Wait for the job cards to refresh after searching.

STEP 3 — OPEN THE JOB DETAIL
- Click the best matching job title link for "{role}".
- On the detail page, look for:
  - Job title: heading near the top
  - Salary: check the page source for a JSON-LD block (type="application/ld+json")
    which contains baseSalary with minValue and maxValue. This is the most reliable.
    If absent, scan the description text for "$X,XXX" or "$XX/hr" patterns.
  - Experience: in "Requirements" or "Qualifications" sections
  - Location: in a location label or "City, State" pattern
  - Department: shown as category or team label

RULES:
- Do NOT click the same element more than once.
- Do NOT navigate to attorney or legal sections.
- Maximum 20 actions total.
- If no results after search, return role_title as "No results found".

Return ONLY a valid JSON object:
{{
  "role_title": "exact title or 'No results found'",
  "salary_min": "minimum salary or null",
  "salary_max": "maximum salary or null",
  "salary_raw": "raw salary text or 'Not listed' if absent",
  "experience_years": "e.g. '3-5 years' or null",
  "experience_raw": "raw experience text or null",
  "location": "city/state or null",
  "job_url": "current page URL of the job detail",
  "practice_area": "department or null"
}}
"""
