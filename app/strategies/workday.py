"""Strategy for Workday-powered career sites -- navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class WorkdayStrategy(BaseStrategy):

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
- Look for links labelled: "Professional Staff", "Business Professionals", "Business Services",
  "Staff Careers", "Administrative Staff", or similar.
- Do NOT click "Attorneys", "Lawyers", "Associates", "Lateral Partners", or any attorney section.
- Click the staff section link. It will redirect to a Workday board (myworkdayjobs.com).

STEP 2 -- SEARCH ON WORKDAY
- Workday is a Single Page Application. Wait 3-4 seconds for it to fully load.
- Find the keyword search box (placeholder: "Search for jobs or keywords").
- Type "{role}" and press Enter or click Search.
- Wait for job cards to appear on the left panel.
- If 0 results, try a shorter keyword (e.g. if "{role}" is "business development", try "business").

STEP 3 -- COLLECT ALL MATCHING JOBS
- Read ALL job cards visible on the left panel.
- For each card that matches "{role}" (by title or description):
  - Click the card to open the detail panel on the right.
  - Extract: title, salary, experience, location, department, and the current page URL.
  - Add to your jobs list.
- If there are more pages (pagination), click Next and collect from those too.
- Collect up to 10 matching jobs.

STEP 4 -- EXTRACT FROM EACH JOB DETAIL
For each job, extract:
- Job title: heading near the top of the detail panel
- Salary: labelled "Pay Range" or inline "$X,XXX - $Y,XXX". About 40% do not list salary -- use "Not listed".
- Experience: in "Basic Qualifications" or "Requirements"
- Location: near the title or "View All N Locations"
- Department: tag or category label
- URL: the full page URL of the job detail (copy from browser address bar or the job card link)

RULES:
- Do NOT click the same element more than once.
- Do NOT navigate to attorney or legal sections.
- Maximum 40 actions total.
- If 0 jobs found after all searches, return {{"jobs": []}}.

Return ONLY a valid JSON object with no text before or after it:
{{
  "jobs": [
    {{
      "role_title": "exact title from job detail",
      "salary_min": "minimum salary as '$X,XXX' or null",
      "salary_max": "maximum salary as '$X,XXX' or null",
      "salary_raw": "raw pay range text or 'Not listed'",
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
