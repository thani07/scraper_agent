"""Strategy for Workday-powered career sites — navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class WorkdayStrategy(BaseStrategy):
    """
    Starts on the firm's main careers page.
    The LLM navigates to the Professional Staff section, which redirects to
    a Workday board (myworkdayjobs.com), then searches and extracts.
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
  "Business Services", "Staff Careers", "Administrative Staff", or similar.
- Do NOT click "Attorneys", "Lawyers", "Associates", "Lateral Partners", or any attorney section.
- Click the staff section link. It will likely redirect to a Workday job board
  (the URL will contain "myworkdayjobs.com").

STEP 2 — YOU ARE NOW ON WORKDAY (myworkdayjobs.com)
- Workday is a Single Page Application. Wait for it to fully load (3-4 seconds).
- Find the keyword search box (placeholder: "Search for jobs or keywords").
- Type "{role}" into the search box and click the Search button or press Enter.
- Wait for job results to appear as cards on the left panel.

STEP 3 — OPEN THE JOB DETAIL
- From the results, click the best matching job title for "{role}".
- The detail view opens on the right panel OR navigates to a new URL — both are fine.
- Look for:
  - Job title: heading near the top
  - Pay Range / Salary: often labelled "Pay Range" near the bottom, or inline in the description
    as "$X,XXX - $Y,XXX". About 40% of roles do not list salary — return "Not listed".
  - Experience: in "Basic Qualifications" or "Requirements" section
  - Location: shown near the title or as "View All N Locations"
  - Department: shown as a tag or category label

RULES:
- Do NOT click the same element more than once.
- Do NOT navigate to attorney or legal sections.
- Maximum 20 actions total.
- If you cannot find "{role}" after searching, return role_title as "No results found".

Return ONLY a valid JSON object:
{{
  "role_title": "exact title or 'No results found'",
  "salary_min": "minimum salary or null",
  "salary_max": "maximum salary or null",
  "salary_raw": "raw pay range text or 'Not listed' if absent",
  "experience_years": "e.g. '3-5 years' or null",
  "experience_raw": "raw experience text or null",
  "location": "city/state or null",
  "job_url": "current page URL of the job detail",
  "practice_area": "department or null"
}}
"""
