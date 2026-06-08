"""Strategy for direct/custom career pages -- navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class DirectStrategy(BaseStrategy):

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
- If there is a search box, type "{role}" and search.
- If the page has department/category filters, select a staff or business category.

STEP 2 -- NAVIGATE TO JOB LISTINGS
- Follow links toward individual job listings.
- If the site redirects to an external ATS (Workday, iCIMS, Greenhouse, etc.),
  continue navigating there -- use the search field to find "{role}".
- Look for job cards, list items, or table rows showing job titles.

STEP 3 -- COLLECT ALL MATCHING JOBS
- Identify ALL listings whose title matches or is related to "{role}".
- For each matching job:
  - Click the job title to open the detail page.
  - Extract: title, salary, experience, location, department, and the page URL.
  - Click the browser Back button to return to the listings.
  - Continue to the next matching job.
- Collect up to 10 matching jobs.

STEP 4 -- EXTRACT FROM EACH JOB DETAIL
For each job, extract:
- Job title: main heading
- Salary: labelled "Pay Range", "Compensation", "Salary Range", or inline "$X,XXX - $Y,XXX". Use "Not listed" if absent.
- Experience: in "Requirements", "Qualifications", or "About You"
- Location: near the title or in a location field
- Department: tag, label, or breadcrumb
- URL: the full page URL of the job detail

RULES:
- Do NOT click the same element more than once.
- Do NOT navigate to attorney or legal sections.
- If a link opens "Access Denied", go Back and try a different path.
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
