"""Strategy for direct/custom career pages — navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class DirectStrategy(BaseStrategy):
    """
    Starts on the firm's main careers page.
    Used for firms with custom-built portals (no standard ATS).
    The LLM navigates to the Professional Staff section and extracts the role.
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
- Do NOT click "Attorneys", "Lawyers", "Associates", "Lateral Partners",
  "Judicial Clerks", or any attorney-focused section.
- If there is a search box on the page, type "{role}" and search directly.
- If the page has department/category filters, select a staff or business category first.

STEP 2 — NAVIGATE TO JOB LISTINGS
- Follow links toward individual job listings.
- If the site redirects to an external ATS (Workday, iCIMS, Greenhouse, etc.),
  continue navigating there — use the search field on that ATS to find "{role}".
- Look for job cards, list items, or table rows that show job titles.

STEP 3 — OPEN THE JOB DETAIL
- Click on the best matching job title for "{role}".
- On the detail page, look for:
  - Job title: main heading
  - Salary: may be labelled "Pay Range", "Compensation", "Salary Range", or inline
    in the description as "$X,XXX - $Y,XXX" or "$XX/hr".
  - Experience: in "Requirements", "Qualifications", or "About You" sections
  - Location: near the title or in a location field
  - Department: shown as a tag, label, or breadcrumb

RULES:
- Do NOT click the same element more than once.
- Do NOT navigate to attorney or legal sections.
- If a link opens an "Access Denied" page, go BACK and try a different path.
- Maximum 20 actions total.
- If you cannot find "{role}", return role_title as "No results found".

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
