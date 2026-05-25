"""Strategy for Greenhouse-powered career sites."""

from typing import List
from .base import BaseStrategy


class GreenhouseStrategy(BaseStrategy):
    """
    Greenhouse career pages typically:
    - URL pattern: boards.greenhouse.io/companyname or company.com/careers
    - Job listings as a flat list with department groupings
    - Clicking a job opens a detail page with description
    """

    def get_initial_actions(self, role: str, url: str) -> List[dict]:
        return [
            {"go_to_url": url},
            {"wait": 3},
        ]

    def get_extraction_task(self, role: str, url: str) -> str:
        return f"""
You are on a Greenhouse career page at {url}.

IMPORTANT RULES:
- Focus on PROFESSIONAL STAFF / BUSINESS SERVICES roles, NOT attorney/lawyer positions.
- Do NOT click the same element more than once.
- If a link redirects or fails, go BACK and try another path.
- Maximum 15 actions.

Your task:
1. Scan the page for job listings. They may be grouped by department.
2. If there is a search/filter option, type "{role}" and filter.
3. If no search exists, scroll through listings to find "{role}" or a close match.
4. Look specifically under sections like "Professional Staff", "Business Services", "Operations", "Administration" — NOT under "Attorneys" or "Legal" sections.
5. Click on the matching job to open its detail page.
6. Extract:
   - Exact job title
   - Salary or compensation range (often near the bottom of the description)
   - Experience requirements (in "Requirements" or "Qualifications" sections)
   - Location (usually shown near the title)
   - Department
7. Current page URL is the job_url.
8. If salary is not listed, set salary_raw to "Not listed".
9. If no match found, return role_title as "No results found".

Return ONLY a valid JSON object:
{{
  "role_title": "exact title or 'No results found'",
  "salary_min": "minimum salary or null",
  "salary_max": "maximum salary or null",
  "salary_raw": "raw salary text or 'Not listed' or null",
  "experience_years": "e.g. '5-7 years' or null",
  "experience_raw": "raw experience text or null",
  "location": "city/state or null",
  "job_url": "current page URL",
  "practice_area": "department or null"
}}
"""