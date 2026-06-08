"""Strategy for FLO Recruit career portals -- navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class FloRecruitStrategy(BaseStrategy):

    def get_initial_actions(self, role: str, url: str) -> List[dict]:
        return [
            {"go_to_url": url},
            {"wait": 3},
        ]

    def get_extraction_task(self, role: str, url: str) -> str:
        return f"""
You are on a law firm careers page at {url}.
Your goal is to find ALL jobs matching "{role}" and extract salary, experience, location, and URL for EVERY match.

STEP 1 -- FIND THE CAREERS / JOBS SECTION
- Look for a link to "Careers", "Open Positions", "Job Openings", "Staff Openings", or "Join Our Team".
- Click it. It may redirect to a FLO Recruit portal (URL contains "florecruit.com/v2/app/").
- All jobs on FLO Recruit portals are Professional Staff roles -- no filtering needed.

STEP 2 -- YOU ARE NOW ON FLO RECRUIT
- This is a React Single Page Application. Wait for job cards to appear.
- Scroll through ALL visible job cards to see every available role.
- There may be no search box -- scan all cards by title.

STEP 3 -- COLLECT ALL MATCHING JOBS
- Identify ALL cards whose title matches or is related to "{role}".
- For each matching card:
  - Click the card or job title to open the detail page.
  - Extract: title, salary, experience, location, department, and the page URL.
  - Click the browser Back button to return to the job list.
  - Continue to the next matching card.
- Collect up to 10 matching jobs.
- If NO exact match exists, pick the closest staff role available.

STEP 4 -- EXTRACT FROM EACH JOB DETAIL
For each job, extract:
- Job title: main heading at the top
- Salary: in the description or "Job details" list -- look for "$X,XXX" or "$XX/hr". Use "Not listed" if absent.
- Experience: in "Requirements" or "Qualifications"
- Location: in "Job details" list (usually second item) or near the title
- Department: tag or category label
- URL: the full page URL of the job detail

RULES:
- Do NOT click the same element more than once.
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
