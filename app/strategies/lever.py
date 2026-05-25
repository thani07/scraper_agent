"""Strategy for Lever-powered career sites."""

from typing import List
from .base import BaseStrategy


class LeverStrategy(BaseStrategy):
    """
    Lever career pages typically:
    - URL pattern: jobs.lever.co/companyname
    - Clean list of jobs grouped by team/department
    - Clicking opens a detail page with description + apply button
    """

    def get_initial_actions(self, role: str, url: str) -> List[dict]:
        return [
            {"go_to_url": url},
            {"wait": 2},
        ]

    def get_extraction_task(self, role: str, url: str) -> str:
        return f"""
You are on a Lever career page at {url}.

IMPORTANT RULES:
- Focus on PROFESSIONAL STAFF / BUSINESS SERVICES roles, NOT attorney/lawyer positions.
- Do NOT click the same element more than once.
- Maximum 15 actions.

Your task:
1. Scan the job listings. They may be grouped by team/department.
2. Look for "{role}" or a close match under staff/business/operations sections.
3. Click on the matching job to open the detail page.
4. Extract:
   - Exact job title
   - Salary or compensation (often at the bottom, labeled "Compensation" or "Salary Range")
   - Experience requirements
   - Location (below the title)
   - Team or department
5. Current URL is the job_url.
6. If salary is not listed, set salary_raw to "Not listed".

Return ONLY a valid JSON object:
{{
  "role_title": "exact title or 'No results found'",
  "salary_min": "minimum salary or null",
  "salary_max": "maximum salary or null",
  "salary_raw": "raw salary text or 'Not listed' or null",
  "experience_years": "e.g. '3-5 years' or null",
  "experience_raw": "raw experience text or null",
  "location": "city/state or null",
  "job_url": "current page URL",
  "practice_area": "department or null"
}}
"""