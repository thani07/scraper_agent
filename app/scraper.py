"""Core scraper engine — runs Browser-Use agent with strategy-driven prompts."""

import os
import time
import asyncio
import json
import tempfile
import shutil
import uuid
from datetime import datetime
from typing import Optional

from browser_use import Agent
from browser_use.browser.profile import BrowserProfile
from langchain_openai import ChatOpenAI, AzureChatOpenAI
from dotenv import load_dotenv

from app.models import SiteConfig, JobExtraction, ScrapeResult
from app.strategies import get_strategy

load_dotenv()


def get_llm():
    """Initialize LLM — supports both OpenAI direct and Azure OpenAI."""
    if os.getenv("USE_AZURE", "").lower() == "true":
        return AzureChatOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            temperature=0,
        )
    else:
        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0,
        )


class _NoiseFilter:
    """
    Stdout wrapper that drops browser_use's internal separator lines.
    Those lines consist entirely of '=' or '-' characters (e.g. 80x '=').
    They add no information and flood the Azure Functions log.
    """
    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, text: str):
        stripped = text.strip()
        # Drop lines that are purely separator characters and longer than 10 chars
        if stripped and len(stripped) > 10 and all(c in "=-*" for c in stripped):
            return
        self._wrapped.write(text)

    def flush(self):
        self._wrapped.flush()

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


def make_step_callback(firm_name: str, last_url_container: list, verbose: bool = False):
    """
    Returns an async step callback.

    Always:
      - Tracks the last non-blank URL (job_url fallback)
      - Prints one concise line per step showing action + goal

    When VERBOSE_ACTIONS=true:
      - Also prints current URL and full thought text
    """

    async def on_step(browser_state_summary, model_output, step_number: int):
        # Always track current URL for job_url fallback
        try:
            url = browser_state_summary.url
            if url and url not in ("about:blank", ""):
                last_url_container[0] = url
        except Exception:
            pass

        if not model_output:
            return

        # Extract action name and goal for the one-liner
        action_name = ""
        action_val  = ""
        goal        = ""

        try:
            if hasattr(model_output, "current_state"):
                state = model_output.current_state
                if hasattr(state, "next_goal") and state.next_goal:
                    goal = state.next_goal.strip()[:100]
        except Exception:
            pass

        try:
            actions = model_output.action if hasattr(model_output, "action") else []
            for action in (actions or []):
                action_dict = action.model_dump(exclude_none=True)
                for key, val in action_dict.items():
                    if key != "type":
                        action_name = key
                        action_val  = str(val)[:60] if val else ""
                        break
                if action_name:
                    break
        except Exception:
            pass

        # One-liner per step — always printed
        if action_name or goal:
            action_part = f"{action_name}({action_val})" if action_name else ""
            goal_part   = f'  ->  "{goal}"'              if goal        else ""
            print(f"  [{firm_name}] Step {step_number:>2} | {action_part}{goal_part}")

        if not verbose:
            return

        # Full verbose output — only when VERBOSE_ACTIONS=true
        timestamp = datetime.now().strftime("%H:%M:%S")
        try:
            url = browser_state_summary.url
            print(f"    URL  : {url} @ {timestamp}")
        except Exception:
            pass
        try:
            if hasattr(model_output, "current_state"):
                state = model_output.current_state
                if hasattr(state, "thought") and state.thought:
                    print(f"    Think: {state.thought[:300]}")
        except Exception:
            pass

    return on_step


def _find_json_objects(text: str) -> list:
    """
    Extract all top-level JSON objects from a string using bracket matching.
    Unlike regex, this correctly handles nested braces inside field values.
    """
    results = []
    depth = 0
    start = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start != -1:
                results.append(text[start:i + 1])
                start = -1
    return results


def _sanitize_json(s: str) -> str:
    """
    Escape literal control characters (newline, tab, carriage return) that appear
    INSIDE JSON string values. The LLM frequently outputs multi-line text in fields
    like experience_raw without escaping the newlines, which makes json.loads fail.

    This walks the string character-by-character, tracking whether we are inside a
    quoted string, and replaces bare \\n / \\r / \\t with their JSON escape sequences.
    """
    result = []
    in_string = False
    escape_next = False

    for ch in s:
        if escape_next:
            escape_next = False
            result.append(ch)
            continue
        if ch == '\\' and in_string:
            escape_next = True
            result.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            if ch == '\n':
                result.append('\\n')
                continue
            if ch == '\r':
                result.append('\\r')
                continue
            if ch == '\t':
                result.append('\\t')
                continue
        result.append(ch)

    return ''.join(result)


def _try_parse(candidate: str) -> Optional[dict]:
    """
    Attempt to parse a JSON candidate string into a dict.
    First tries the string as-is; if that fails, sanitizes literal control characters
    inside string values and tries again.
    Returns the dict if it contains 'role_title', otherwise None.
    """
    for attempt in (candidate, _sanitize_json(candidate)):
        try:
            data = json.loads(attempt)
            if isinstance(data, dict) and "role_title" in data:
                return data
        except Exception:
            pass
    return None


def _ensure_required_fields(data: dict, fallback_url: str) -> dict:
    """
    job_url is a required non-null str in JobExtraction.
    If the LLM returned null or omitted it, fill it with the fallback URL
    (the last page the agent actually visited) so Pydantic validation never fails.
    """
    if not data.get("job_url"):
        data["job_url"] = fallback_url
    return data


def _find_json_arrays(text: str) -> list:
    """
    Extract all top-level JSON arrays from a string using bracket matching.
    Companion to _find_json_objects — handles bare [...] structures.
    """
    results = []
    depth = 0
    start = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0 and start != -1:
                results.append(text[start:i + 1])
                start = -1
    return results


def parse_multi_extraction(
    raw_result: str, fallback_role: str, fallback_url: str
) -> list:
    """
    Parse LLM output that contains multiple jobs in {"jobs": [...]} format.

    Handles:
      1. {"jobs": [...]}         — canonical multi-job format
      2. [{...}, {...}]          — bare array
      3. Markdown code blocks    — ```json { "jobs": [...] } ```
      4. Embedded in prose       — bracket-matching extraction
      5. Single job fallback     — falls through to parse_extraction

    Returns a list of JobExtraction objects (may be empty).
    """
    import re as _re

    if not raw_result:
        return []

    text = raw_result.strip()

    def _extract_jobs_from_dict(data: dict) -> list:
        """Pull the jobs list out of a {"jobs": [...]} dict."""
        jobs_raw = data.get("jobs")
        if not isinstance(jobs_raw, list):
            return []
        results = []
        for job in jobs_raw:
            if isinstance(job, dict) and "role_title" in job:
                try:
                    results.append(
                        JobExtraction.model_validate(
                            _ensure_required_fields(job, fallback_url)
                        )
                    )
                except Exception:
                    pass
        return results

    def _extract_jobs_from_list(data: list) -> list:
        """Pull jobs out of a bare [...] array."""
        results = []
        for job in data:
            if isinstance(job, dict) and "role_title" in job:
                try:
                    results.append(
                        JobExtraction.model_validate(
                            _ensure_required_fields(job, fallback_url)
                        )
                    )
                except Exception:
                    pass
        return results

    # _try_parse_multi returns (found_multi_structure, jobs_list)
    # found_multi_structure=True means we saw {"jobs":...} or [...] — stop searching even if empty
    def _try_parse_multi(candidate: str):
        """Try to parse candidate as multi-job JSON.
        Returns (True, jobs) if a multi-job structure was found (jobs may be empty),
        or (False, []) if this is not a multi-job structure.
        """
        for attempt in (candidate, _sanitize_json(candidate)):
            try:
                data = json.loads(attempt)
                if isinstance(data, dict) and "jobs" in data:
                    return True, _extract_jobs_from_dict(data)
                elif isinstance(data, list) and data:
                    # Only treat a bare array as multi-job if items look like job dicts
                    jobs = _extract_jobs_from_list(data)
                    if jobs:
                        return True, jobs
            except Exception:
                pass
        return False, []

    # 1. Direct parse
    found, jobs = _try_parse_multi(text)
    if found:
        return jobs

    # 2. Markdown code blocks
    for pattern in [r'```json\s*([\[\{].+?[\]\}])\s*```', r'```\s*([\[\{].+?[\]\}])\s*```']:
        m = _re.search(pattern, text, _re.DOTALL)
        if m:
            found, jobs = _try_parse_multi(m.group(1))
            if found:
                return jobs

    # 3. Bracket-matching: {"jobs": [...]} objects
    for candidate in _find_json_objects(text):
        found, jobs = _try_parse_multi(candidate)
        if found:
            return jobs

    # 4. Bracket-matching: bare [...] arrays
    for candidate in _find_json_arrays(text):
        found, jobs = _try_parse_multi(candidate)
        if found:
            return jobs

    # 5. Single-job fallback — LLM returned old single-job format instead of multi
    single = parse_extraction(text, fallback_role, fallback_url)
    if single:
        return [single]

    return []


def parse_extraction(raw_result: str, fallback_role: str, fallback_url: str) -> Optional[JobExtraction]:
    """
    Robustly parse the agent's final output into a JobExtraction model.

    Parse order:
      1. Direct json.loads (+ sanitized variant)         — clean JSON string
      2. Markdown code-block extraction (+ sanitized)    — ```json { ... } ```
      3. Bracket-matching finder (+ sanitized per hit)   — JSON embedded in prose
      4. Last resort                                     — stores raw text for debugging
    """
    if not raw_result:
        return None

    text = raw_result.strip()

    # 1. Direct parse — fastest path when the LLM returns clean JSON
    data = _try_parse(text)
    if data:
        return JobExtraction.model_validate(_ensure_required_fields(data, fallback_url))

    # 2. Markdown code block — LLM sometimes wraps output in ```json ... ```
    import re
    for pattern in [r'```json\s*(\{.+?\})\s*```', r'```\s*(\{.+?\})\s*```']:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            data = _try_parse(m.group(1))
            if data:
                return JobExtraction.model_validate(_ensure_required_fields(data, fallback_url))

    # 3. Bracket-matching finder — handles JSON embedded inside prose text.
    #    Also covers cases where experience_raw / salary_raw contain literal newlines
    #    that make the raw string invalid JSON (_try_parse sanitizes those).
    for candidate in _find_json_objects(text):
        data = _try_parse(candidate)
        if data:
            return JobExtraction.model_validate(_ensure_required_fields(data, fallback_url))

    # 4. Last resort — parsing truly failed; store raw text in salary_raw for debugging
    return JobExtraction(
        role_title=fallback_role,
        salary_raw=text[:500],
        job_url=fallback_url,
    )


def _expand_search_terms(role: str) -> list[str]:
    """
    Generate an ordered list of search terms for a role, from most specific to broadest.
    Used when the exact role name returns no results on a job portal — different firms
    use different naming conventions for the same role.

    Examples:
      "Paralegal"                  → ["Paralegal", "Legal Assistant", "Legal Support"]
      "Corporate Paralegal"        → ["Corporate Paralegal", "Paralegal", "Corporate", "Legal Assistant"]
      "Senior Quantitative Analyst"→ ["Senior Quantitative Analyst", "Quantitative Analyst", "Analyst"]
      "Associate Attorney"         → ["Associate Attorney", "Attorney", "Associate", "Counsel", "Lawyer"]
      "GCP Manager"                → ["GCP Manager", "Manager", "GCP", "Cloud Manager"]
      "Data Strategy Manager"      → ["Data Strategy Manager", "Strategy Manager", "Data Manager", "Manager"]
    """
    words = role.split()
    terms: list[str] = [role]  # exact match always first

    # Drop leading level/qualifier word: "Senior Corporate Paralegal" → "Corporate Paralegal"
    if len(words) >= 3:
        terms.append(" ".join(words[1:]))

    # Last two meaningful words: "Quantitative Analyst", "Strategy Manager"
    if len(words) >= 3:
        terms.append(" ".join(words[-2:]))

    # Core title — last word only: "Paralegal", "Attorney", "Manager", "Analyst"
    if len(words) >= 2:
        terms.append(words[-1])

    # First word if it looks like a meaningful label (>3 chars): "GCP", "Data"
    if len(words) >= 2 and len(words[0]) > 3:
        terms.append(words[0])

    # Role-specific synonyms — common alternative names used by law firms and
    # professional-services firms for the same underlying role.
    role_lower = role.lower()
    if "business professional" in role_lower or role_lower in ("business professional", "business professionals"):
        # Law firms call non-attorney staff many different things.
        # Try every common label before giving up.
        terms += [
            "Business Services",
            "Professional Staff",
            "Business Staff",
            "Staff",
            "Business Operations",
            "Administrative",
            "Operations",
            "Support Staff",
            "Non-Attorney",
            "Business Support",
            "Firm Administration",
            "Administrative Professional",
        ]
    elif "paralegal" in role_lower:
        terms += ["Legal Assistant", "Legal Support", "Litigation Support",
                  "Legal Support Specialist", "Paralegal Specialist"]
    elif "attorney" in role_lower or "counsel" in role_lower or "lawyer" in role_lower:
        terms += ["Counsel", "Attorney", "Lawyer", "Associate"]
    elif "legal secretary" in role_lower or "legal admin" in role_lower:
        terms += ["Legal Secretary", "Legal Administrative Assistant",
                  "Legal Administrator", "Secretary"]
    elif "analyst" in role_lower:
        terms += ["Analyst", "Specialist", "Coordinator"]
    elif "manager" in role_lower:
        terms += ["Manager", "Director", "Lead", "Head"]
    elif "coordinator" in role_lower:
        terms += ["Coordinator", "Specialist", "Administrator", "Assistant"]
    elif "administrator" in role_lower or "admin" in role_lower:
        terms += ["Administrator", "Coordinator", "Specialist", "Assistant"]

    # Deduplicate preserving order, case-insensitive
    seen: set[str] = set()
    result: list[str] = []
    for t in terms:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(t.strip())

    return result


async def generate_search_terms(role: str) -> list[str]:
    """
    Ask the LLM to generate 4-5 law-firm-specific alternative search terms for the
    given role. Returns a deduplicated list starting with the original role.

    Example: "litigation" -> ["litigation", "Litigation Associate", "Litigation Counsel",
                               "Trial Attorney", "Litigation Paralegal", "Dispute Resolution"]
    """
    import re as _re

    llm = get_llm()
    prompt = (
        f"You are an expert in US law firm HR and job titles.\n\n"
        f"The user wants to search law firm career portals for the role: \"{role}\"\n\n"
        f"Generate exactly 4 alternative job titles or search keywords that US law firms "
        f"commonly use on their career portals to describe this type of position. "
        f"These must be real terms found on law firm job postings — not generic phrases.\n\n"
        f"Return ONLY a valid JSON array of 4 strings. No explanation, no markdown.\n"
        f"Example for \"litigation\": [\"Litigation Associate\", \"Litigation Counsel\", "
        f"\"Trial Attorney\", \"Dispute Resolution Attorney\"]"
    )

    try:
        response = await llm.ainvoke(prompt)
        text = response.content.strip()

        # Try direct JSON parse
        try:
            alts = json.loads(text)
            if isinstance(alts, list):
                alts = [a for a in alts if isinstance(a, str) and a.strip()]
                return _dedupe_terms([role] + alts)
        except Exception:
            pass

        # Try bracket-match extraction from prose
        m = _re.search(r'\[.*?\]', text, _re.DOTALL)
        if m:
            try:
                alts = json.loads(m.group())
                if isinstance(alts, list):
                    alts = [a for a in alts if isinstance(a, str) and a.strip()]
                    return _dedupe_terms([role] + alts)
            except Exception:
                pass
    except Exception:
        pass

    # Fallback to static expansion if LLM call fails
    return _expand_search_terms(role)


def _dedupe_terms(terms: list[str]) -> list[str]:
    """Deduplicate a list of search terms preserving order, case-insensitive."""
    seen: set[str] = set()
    result: list[str] = []
    for t in terms:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(t.strip())
    return result


async def scrape_site(site: SiteConfig, role: str, search_terms: list[str] | None = None) -> list:
    """
    Scrape a single site for a given role using Browser-Use agent.

    search_terms: full list of terms to search on this site (user role + AI alternatives).
                  The agent searches EVERY term and combines all results.
                  If None, falls back to _expand_search_terms(role).

    Returns a list of ScrapeResult objects — one per matching job found.
    videsktop strategy uses multi-job extraction ({"jobs": [...]}) so may return
    multiple results. All other strategies return a single-element list.
    """
    start_time = time.time()
    strategy = get_strategy(site.strategy)

    # Use provided search_terms or fall back to static expansion
    all_terms = search_terms if search_terms else _expand_search_terms(role)

    # Build the search instructions injected into the agent task.
    # For videsktop: agent must search EVERY term and collect all results.
    # Terms are shown as a numbered list so the agent knows exactly what to search.
    terms_hint = (
        f"\n\nSEARCH ALL TERMS — MANDATORY:\n"
        f"You must search the following terms ONE BY ONE on this site and collect ALL "
        f"matching jobs from every search. Do NOT stop after the first term that returns results.\n"
        f"Search every term regardless of whether previous terms found jobs.\n"
        f"After all searches, deduplicate by job title before returning.\n\n"
        f"Terms to search (in order):\n"
        + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(all_terms))
        + f"\n\nFor each term: clear the search box, type the term, submit, wait for results, "
        f"collect all matching rows. Then move to the next term."
    )

    effective_hints = (site.navigation_hints or "") + terms_hint
    task = strategy.get_navigation_task(role, site.careers_url, effective_hints)

    # Feature flags from .env
    headless    = os.getenv("HEADLESS", "true").lower() == "true"
    timeout     = int(os.getenv("SCRAPE_TIMEOUT", "180"))
    save_gif    = os.getenv("SAVE_GIF", "false").lower() == "true"
    save_convo  = os.getenv("SAVE_CONVERSATION", "false").lower() == "true"
    verbose     = os.getenv("VERBOSE_ACTIONS", "false").lower() == "true"

    # GIF output path: logs/{firm_name}_{timestamp}.gif
    gif_path = None
    if save_gif:
        os.makedirs("logs", exist_ok=True)
        safe_name = site.name.replace(" ", "_").replace("&", "and")
        gif_path = f"logs/{safe_name}_{int(start_time)}.gif"

    # Conversation log path: logs/{firm_name}_{timestamp}_conversation.json
    convo_path = None
    if save_convo:
        os.makedirs("logs", exist_ok=True)
        safe_name = site.name.replace(" ", "_").replace("&", "and")
        convo_path = f"logs/{safe_name}_{int(start_time)}_conversation.json"

    # Each concurrent scrape_site call gets a unique Chrome profile directory.
    # Without this, all concurrent browsers fight over the same SingletonLock file
    # at the default user_data_dir, causing "Target page, context or browser has
    # been closed" errors immediately on launch.
    unique_profile_dir = os.path.join(
        tempfile.gettempdir(), f"bu_{uuid.uuid4().hex[:12]}"
    )
    try:
        profile = BrowserProfile(
            user_data_dir=unique_profile_dir,
            headless=headless,
            disable_security=True,
            viewport={"width": 1280, "height": 900},
            chromium_sandbox=False,
        )

        llm = get_llm()

        # videsktop needs many more steps for multi-job extraction:
        #   navigation (5-10) + search (3) + per-job: navigate + extract + back (3×N)
        #   15 jobs × 3 steps = 45 + 15 overhead = 60 steps.
        # Other strategies extract one job and need far fewer steps.
        is_videsktop = (site.strategy.value == "videsktop")
        max_steps = 60 if is_videsktop else 20

        # videsktop uses ASP.NET WebForms — every Search/click triggers a full page reload
        # (postback). If the LLM batches 3 actions in one step, action 1 fires the postback,
        # actions 2 & 3 then reference stale element indices from the pre-reload page →
        # wrong clicks, null data. Forcing 1 action per step ensures the LLM sees the
        # refreshed element listing before deciding its next action.
        actions_per_step = 1 if is_videsktop else 3

        # Track the last URL the agent visits. Used as a better fallback for job_url
        # than site.careers_url (which is the starting careers page, not the job detail page).
        last_url = [site.careers_url]

        # 'href' is added so the LLM can read link destinations in the element listing and
        # identify RecJobView.aspx links without having to blindly click every row element.
        # 'target' is added so the LLM can detect target="_blank" links and avoid
        # clicking them directly (which opens new tabs). Instead it extracts the
        # href and uses go_to_url, keeping navigation in the current tab.
        include_attrs = [
            'title', 'type', 'name', 'role', 'aria-label', 'placeholder', 'value',
            'alt', 'aria-expanded', 'data-date-format', 'checked', 'data-state',
            'aria-checked', 'href', 'target',
        ]

        # Pre-navigate to the careers page before the LLM takes over.
        # Without this the agent starts at about:blank and wastes a step (or worse,
        # misreads the blank state as an error and quits immediately).
        initial_actions = [{"go_to_url": {"url": site.careers_url}}]

        agent = Agent(
            task=task,
            llm=llm,
            browser_profile=profile,
            use_vision=False,
            max_actions_per_step=actions_per_step,
            max_failures=5,
            generate_gif=gif_path if save_gif else False,
            save_conversation_path=convo_path,
            include_attributes=include_attrs,
            initial_actions=initial_actions,
            # Always register the callback — even when not verbose — so last_url is tracked.
            register_new_step_callback=make_step_callback(site.name, last_url, verbose),
        )

        if verbose:
            print(f"\n{'='*60}")
            print(f"  STARTING: {site.name} ({site.strategy.value})")
            print(f"  URL     : {site.careers_url}")
            print(f"  Role    : {role}")
            print(f"  max_steps={max_steps}  actions_per_step={actions_per_step}")
            if save_gif:
                print(f"  GIF     : {gif_path}")
            if save_convo:
                print(f"  Log     : {convo_path}")
            print(f"{'='*60}")

        # Suppress browser_use's internal separator-line noise while the agent runs.
        # The _NoiseFilter drops lines made entirely of '=' or '-' characters.
        import sys as _sys
        _orig_stdout = _sys.stdout
        _sys.stdout  = _NoiseFilter(_sys.stdout)
        try:
            result = await asyncio.wait_for(
                agent.run(max_steps=max_steps),
                timeout=timeout,
            )
        except Exception as agent_err:
            # GIF/PIL errors happen inside agent.run after the task completes.
            # Try to recover the result from agent state before re-raising.
            result = getattr(agent, "state", None)
            if result is None:
                raise agent_err
            # If it's a PIL/GIF error specifically, warn and continue with result
            if "PIL" in str(agent_err) or "gif" in str(agent_err).lower() or "Pillow" in str(agent_err):
                if verbose:
                    print(f"  [WARN] GIF generation failed: {agent_err}")
            else:
                raise agent_err
        finally:
            _sys.stdout = _orig_stdout

        # Extract structured output
        # result may be AgentHistoryList or AgentState depending on error recovery path
        final = None
        try:
            final = result.final_result()
        except AttributeError:
            # Recovered AgentState — grab last extracted content
            try:
                history = result.history
                for item in reversed(history):
                    if hasattr(item, "result"):
                        for r in (item.result or []):
                            if getattr(r, "extracted_content", None):
                                final = r.extracted_content
                                break
                    if final:
                        break
            except Exception:
                pass

        if not final:
            try:
                extracted = result.extracted_content()
                if extracted:
                    combined = " ".join(str(e) for e in extracted if e)
                    if combined.strip():
                        final = combined
            except Exception:
                pass

        duration = round(time.time() - start_time, 2)

        if verbose and gif_path and os.path.exists(gif_path):
            print(f"\n  GIF saved -> {gif_path}")
        if verbose and convo_path:
            print(f"  Conversation log -> {convo_path}")

        # videsktop uses {"jobs": [...]} multi-job format; all other strategies
        # use single-job format. Both paths return a list of ScrapeResult.
        if is_videsktop:
            extractions = parse_multi_extraction(str(final) if final else "", role, last_url[0])
            if not extractions:
                # No jobs found at all
                return [ScrapeResult(
                    firm_name=site.name,
                    strategy_used=site.strategy.value,
                    role_searched=role,
                    status="no_results",
                    scrape_duration_sec=duration,
                )]
            site_results = []
            for extraction in extractions:
                # Fix viDesktop job URLs: the LLM often records page.url which stays at
                # RecDefault.aspx after a postback click. The real job URL is identical
                # except RecDefault.aspx → RecApplicantEmail.aspx (same Tag, same host).
                # Also strip any #job-N fallback fragment added by the last-resort rule.
                if extraction.job_url and "RecDefault.aspx" in extraction.job_url:
                    fixed = extraction.job_url.split("#")[0]
                    fixed = fixed.replace("RecDefault.aspx", "RecApplicantEmail.aspx")
                    extraction.job_url = fixed

                _no_job = (
                    not extraction.role_title
                    or extraction.role_title.strip().lower() in ("no results found", "")
                )
                site_results.append(ScrapeResult(
                    firm_name=site.name,
                    strategy_used=site.strategy.value,
                    role_searched=role,
                    extraction=extraction,
                    status="no_results" if _no_job else "success",
                    scrape_duration_sec=duration,
                ))
            return site_results
        else:
            # Single-job strategies
            extraction = parse_extraction(str(final) if final else "", role, last_url[0]) if final else None
            _no_job = (
                not extraction
                or not extraction.role_title
                or extraction.role_title.strip().lower() in ("no results found", "")
            )
            return [ScrapeResult(
                firm_name=site.name,
                strategy_used=site.strategy.value,
                role_searched=role,
                extraction=extraction,
                status="no_results" if _no_job else "success",
                scrape_duration_sec=duration,
            )]

    except asyncio.TimeoutError:
        duration = round(time.time() - start_time, 2)
        return [ScrapeResult(
            firm_name=site.name,
            strategy_used=site.strategy.value,
            role_searched=role,
            status="error",
            error_message=f"Timeout after {duration}s",
            scrape_duration_sec=duration,
        )]

    except Exception as e:
        duration = round(time.time() - start_time, 2)
        return [ScrapeResult(
            firm_name=site.name,
            strategy_used=site.strategy.value,
            role_searched=role,
            status="error",
            error_message=str(e)[:500],
            scrape_duration_sec=duration,
        )]

    finally:
        # Remove the temporary Chrome profile directory created for this instance.
        # This keeps the temp dir clean and avoids leftover lock files.
        shutil.rmtree(unique_profile_dir, ignore_errors=True)


async def scrape_all_sites(sites: list[SiteConfig], role: str) -> list[ScrapeResult]:
    """Scrape multiple sites concurrently with controlled parallelism.

    scrape_site now returns list[ScrapeResult] (multiple jobs per site for videsktop).
    This function flattens those lists into a single results list.
    """
    max_concurrent = int(os.getenv("MAX_CONCURRENT", "3"))
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _scrape_with_limit(site: SiteConfig) -> list:
        async with semaphore:
            return await scrape_site(site, role)

    raw_results = await asyncio.gather(
        *[_scrape_with_limit(site) for site in sites],
        return_exceptions=True,
    )

    final_results = []
    for i, r in enumerate(raw_results):
        if isinstance(r, Exception):
            final_results.append(ScrapeResult(
                firm_name=sites[i].name,
                strategy_used=sites[i].strategy.value,
                role_searched=role,
                status="error",
                error_message=str(r)[:500],
            ))
        elif isinstance(r, list):
            final_results.extend(r)
        else:
            # Defensive: unexpected single result
            final_results.append(r)

    return final_results
