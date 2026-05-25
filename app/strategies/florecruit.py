"""Strategy for FLO Recruit career portals — navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class FloRecruitStrategy(BaseStrategy):
    """
    Starts on the firm's main careers page.
    The LLM navigates to the jobs section, which links to a FLO Recruit portal
    (florecruit.com), then scrolls through the React SPA to find and extract the role.
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

STEP 1 — FIND THE CAREERS / JOBS SECTION
- Look for a link to "Careers", "Open Positions", "Job Openings", "Staff Openings",
  or a "Join Our Team" section.
- Click it. It may redirect to a FLO Recruit portal
  (the URL will contain "florecruit.com/v2/app/").
- All jobs on FLO Recruit portals are Professional Staff roles — no filtering needed.

STEP 2 — YOU ARE NOW ON FLO RECRUIT (florecruit.com)
- This is a React Single Page Application. Wait for job cards to appear.
- All available jobs are listed as cards on the page. There may be no search box —
  scroll through all cards to find "{role}" or the closest matching role.
- Each card shows a job title, location, and brief description.

STEP 3 — OPEN THE JOB DETAIL
- Click the card or job title link for the best match to "{role}".
- On the detail page, look for:
  - Job title: heading near the top
  - Salary: in the description body or in a "Job details" list — look for "$X,XXX"
    or "$XX/hr" patterns. Location is often the second item in the job details list.
  - Experience: in "Requirements" or "Qualifications" sections
  - Location: in a "Job details" list (usually the second list item) or near the title
  - Department: shown as a tag or category

RULES:
- Do NOT click the same element more than once.
- Maximum 20 actions total.
- If no match found, return the closest available staff role.

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
