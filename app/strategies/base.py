"""Base strategy -- defines the contract every ATS strategy must follow."""

from abc import ABC, abstractmethod
from typing import List, Optional


class BaseStrategy(ABC):
    """
    Each strategy returns:
    1. initial_actions -- deterministic Playwright steps (free, no LLM)
    2. extraction_task -- natural language prompt for the LLM agent
    """

    @abstractmethod
    def get_initial_actions(self, role: str, url: str) -> List[dict]:
        ...

    @abstractmethod
    def get_extraction_task(self, role: str, url: str) -> str:
        ...

    def get_navigation_task(self, role: str, url: str, hints: Optional[str] = None) -> str:
        """Full task prompt combining extraction task + optional hints + hard rules."""
        task = self.get_extraction_task(role, url)
        if hints:
            task += f"\n\nADDITIONAL NAVIGATION HINTS: {hints}"
        task += """

CRITICAL RULES -- MUST FOLLOW AT ALL TIMES:
1. NEVER OPEN EXTRA TABS.
   You work in a single browser tab. If a link would open a new tab, do not click it
   directly -- extract its href and use go_to_url to navigate in the current tab.
   - If a link has target="_blank": read its href, then use go_to_url with that href.
   - If an unintended extra tab does open: close it and continue in the original tab.

2. WRONG PAGE RECOVERY -- after every click or navigation, check what page you are on.
   If you land on any of the following, use go_back() IMMEDIATELY and try a different link:
   - Browser errors: "ERR_", "This site can't be reached", "Network Error", "Connection refused"
   - HTTP errors: "404", "403", "Page Not Found", "Access Denied", "Something went wrong"
   - Completely irrelevant pages: news articles, attorney/lawyer profiles, office pages,
     press releases, alumni pages, events pages, practice area descriptions
   Never stay on an error or wrong page -- always recover by going back.

3. You MUST click on a specific job title link to open the individual job detail page.
   Do NOT extract from a search results / listing page. The URL must change to a
   job-specific URL (e.g. contains /job/, /jobs/, ?opportunityId=, RecJobView.aspx, etc.)
   before you call done().

4. Verify you are on a SINGLE JOB detail page (one job title as the main heading).
   If you see a list of multiple jobs, you are still on the listing page -- click one.

5. Only call done() once you have opened the individual job detail page and attempted
   to extract salary, experience, and location from it.
"""
        return task