"""Core scraper engine — runs Browser-Use agent with strategy-driven prompts."""

import os
import time
import asyncio
import json
from datetime import datetime
from typing import Optional

from browser_use import Agent, Browser, BrowserConfig, BrowserContextConfig
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


def make_step_callback(firm_name: str, last_url_container: list, verbose: bool = False):
    """
    Returns an async step callback that:
      - Always tracks the last non-blank URL the agent visits (used as job_url fallback)
      - Prints detailed action logs only when verbose=True

    Called by Browser-Use after every step with:
      browser_state_summary  — current page state
      model_output           — what the LLM decided to do (actions list)
      step_number            — current step index
    """
    async def on_step(browser_state_summary, model_output, step_number: int):
        # Always track the current URL so we have a better fallback than site.careers_url
        try:
            url = browser_state_summary.url
            if url and url not in ("about:blank", ""):
                last_url_container[0] = url
        except Exception:
            pass

        if not verbose:
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{firm_name}] Step {step_number} @ {timestamp}")

        # Current URL
        try:
            url = browser_state_summary.url
            print(f"  URL    : {url}")
        except Exception:
            pass

        # What the LLM is thinking / doing
        if model_output:
            try:
                if hasattr(model_output, "current_state"):
                    state = model_output.current_state
                    if hasattr(state, "thought") and state.thought:
                        print(f"  Think  : {state.thought[:200]}")
                    if hasattr(state, "next_goal") and state.next_goal:
                        print(f"  Goal   : {state.next_goal[:200]}")
            except Exception:
                pass

            try:
                actions = model_output.action if hasattr(model_output, "action") else []
                for i, action in enumerate(actions or []):
                    action_dict = action.model_dump(exclude_none=True)
                    for key, val in action_dict.items():
                        if val is not None and key != "type":
                            val_str = str(val)[:120]
                            print(f"  Action : {key}({val_str})")
                            break
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


def parse_extraction(raw_result: str, fallback_role: str, fallback_url: str) -> Optional[JobExtraction]:
    """
    Robustly parse the agent's final output into JobExtraction.
    Handles JSON strings, dicts, markdown code blocks, and messy LLM output.
    """
    if not raw_result:
        return None

    text = raw_result.strip()

    # 1. Try direct JSON parse first (fastest path)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "role_title" in data:
            return JobExtraction.model_validate(data)
    except Exception:
        pass

    # 2. Try extracting JSON from markdown code blocks
    try:
        import re
        for pattern in [r'```json\s*(\{.*?\})\s*```', r'```\s*(\{.*?\})\s*```']:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                if isinstance(data, dict) and "role_title" in data:
                    return JobExtraction.model_validate(data)
    except Exception:
        pass

    # 3. Bracket-matching JSON finder — handles nested braces inside field values
    #    (the old [^{}]* regex fails when experience_raw or salary_raw contain braces)
    for candidate in _find_json_objects(text):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and "role_title" in data:
                return JobExtraction.model_validate(data)
        except Exception:
            continue

    # 4. Last resort — raw text stored for debugging, not as real extraction
    return JobExtraction(
        role_title=fallback_role,
        salary_raw=text[:500],
        job_url=fallback_url,
    )


async def scrape_site(site: SiteConfig, role: str) -> ScrapeResult:
    """
    Scrape a single site for a given role using Browser-Use agent.
    """
    start_time = time.time()
    strategy = get_strategy(site.strategy)

    task = strategy.get_navigation_task(role, site.careers_url, site.navigation_hints)

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

    browser = None
    try:
        browser = Browser(
            config=BrowserConfig(
                headless=headless,
                browser_context_config=BrowserContextConfig(
                    disable_security=True,
                    no_viewport=False,
                    browser_window_size={"width": 1280, "height": 900},
                ),
            )
        )

        llm = get_llm()

        # videsktop needs more steps: careers page navigation (4-6) + portal search (3) +
        # postback wait + job row selection (3) + detail extraction (3) = 15+ minimum.
        # Use 30 to allow retries. Other strategies are simpler, 20 is fine.
        is_videsktop = (site.strategy.value == "videsktop")
        max_steps = 30 if is_videsktop else 20

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
        include_attrs = [
            'title', 'type', 'name', 'role', 'aria-label', 'placeholder', 'value',
            'alt', 'aria-expanded', 'data-date-format', 'checked', 'data-state',
            'aria-checked', 'href',
        ]

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            use_vision=False,
            max_actions_per_step=actions_per_step,
            max_failures=5,
            generate_gif=gif_path if save_gif else False,
            save_conversation_path=convo_path,
            include_attributes=include_attrs,
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

        # Extract structured output
        extraction = None

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

        if final:
            # last_url[0] is the most recent page the agent visited — for a successful
            # videsktop run this will be the RecJobView.aspx URL, not the careers page.
            extraction = parse_extraction(str(final), role, last_url[0])

        if not extraction:
            try:
                extracted = result.extracted_content()
                if extracted:
                    combined = " ".join(str(e) for e in extracted if e)
                    if combined.strip():
                        extraction = parse_extraction(combined, role, last_url[0])
            except Exception:
                pass

        duration = round(time.time() - start_time, 2)

        if verbose and gif_path and os.path.exists(gif_path):
            print(f"\n  GIF saved -> {gif_path}")
        if verbose and convo_path:
            print(f"  Conversation log -> {convo_path}")

        # A result is "no_results" only when the agent explicitly found nothing.
        # Do NOT compare role_title to the search term — a job can be titled the
        # same word as the role being searched (e.g. role="Paralegal", title="Paralegal").
        _no_job = (
            not extraction
            or not extraction.role_title
            or extraction.role_title.strip().lower() in ("no results found", "")
        )
        return ScrapeResult(
            firm_name=site.name,
            strategy_used=site.strategy.value,
            role_searched=role,
            extraction=extraction,
            status="no_results" if _no_job else "success",
            scrape_duration_sec=duration,
        )

    except asyncio.TimeoutError:
        duration = round(time.time() - start_time, 2)
        return ScrapeResult(
            firm_name=site.name,
            strategy_used=site.strategy.value,
            role_searched=role,
            status="error",
            error_message=f"Timeout after {duration}s",
            scrape_duration_sec=duration,
        )

    except Exception as e:
        duration = round(time.time() - start_time, 2)
        return ScrapeResult(
            firm_name=site.name,
            strategy_used=site.strategy.value,
            role_searched=role,
            status="error",
            error_message=str(e)[:500],
            scrape_duration_sec=duration,
        )

    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def scrape_all_sites(sites: list[SiteConfig], role: str) -> list[ScrapeResult]:
    """Scrape multiple sites concurrently with controlled parallelism."""
    max_concurrent = int(os.getenv("MAX_CONCURRENT", "3"))
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _scrape_with_limit(site: SiteConfig) -> ScrapeResult:
        async with semaphore:
            return await scrape_site(site, role)

    results = await asyncio.gather(
        *[_scrape_with_limit(site) for site in sites],
        return_exceptions=True,
    )

    final_results = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            final_results.append(ScrapeResult(
                firm_name=sites[i].name,
                strategy_used=sites[i].strategy.value,
                role_searched=role,
                status="error",
                error_message=str(r)[:500],
            ))
        else:
            final_results.append(r)

    return final_results
