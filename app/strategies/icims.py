"""Strategy for iCIMS-powered career sites — navigates from the firm's main careers page."""

from typing import List
from .base import BaseStrategy


class ICIMSStrategy(BaseStrategy):
    """
    Starts on the firm's main careers page.
    The LLM navigates to the Professional Staff section, which links to an
    iCIMS portal, then searches and extracts.

    iCIMS classic: all content is inside an iframe named "icims_content_iframe".
    iCIMS Talent Cloud (Orrick): modern React SPA, no iframe.
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
- Look for links or sections labelled: "Professional Staff", "Business Services",
  "Administrative Staff", "Staff Openings", or similar.
- Do NOT click "Attorneys", "Lawyers", "Associates", or any attorney section.
- Click the staff section link. It will redirect to an iCIMS career portal
  (the URL will contain ".icims.com" or "talent." subdomain).

STEP 2 — YOU ARE NOW ON AN ICIMS PORTAL
- Classic iCIMS: the entire job board is inside an iframe. Look for an iframe on the page.
  ALL job listings and search fields are INSIDE that iframe — interact with iframe elements.
- Modern iCIMS (Talent Cloud): no iframe, direct page interaction.
- Find the keyword search field and type "{role}", then submit (click Search or press Enter).
- Wait for results to load inside the iframe (or on the page for Talent Cloud).

STEP 3 — OPEN THE JOB DETAIL
- From the results list, click the best matching job title for "{role}".
- The detail page may stay on the same domain or redirect (this is normal).
- Look for:
  - Job title: heading near the top
  - Salary: in the description prose — look for "$X,XXX - $Y,XXX" or "$XX/hr" patterns.
    Salary is NOT in a dedicated field; search the full description text.
  - Experience: in "Requirements" or "Qualifications" sections
  - Location: shown near the title or in a location field
  - Department: shown as a category or team label

RULES:
- Do NOT click the same element more than once.
- Do NOT navigate to attorney or legal sections.
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
