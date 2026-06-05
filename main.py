"""
HR Salary Scraper - Core logic + CLI entry point.

run_scraper()  — importable async function called by Azure Functions triggers
main()         — CLI entry point (reads argparse + .env)

Processing order: FIRM-FIRST.
  For each firm (up to `concurrency` at once), all roles are searched sequentially.
  This ensures every firm is fully processed before moving on, avoids role starvation,
  and limits open browsers to `concurrency` at most (one per firm slot).

CLI usage:
    python main.py                                           # all firms, all roles
    python main.py --strategy videsktop                     # only videsktop firms
    python main.py --filter "Jones Day"                     # one firm only
    python main.py --firms-config config/all_firms_test.json  # use test config (5 firms)
    python main.py --storage local                          # save to results.json only
"""

import logging
logging.disable(logging.CRITICAL)

import asyncio
import json
import sys
import os
import argparse
from datetime import datetime

# Ensure UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

from app.models import SiteConfig, ScrapeResult
from app.scraper import scrape_site, generate_search_terms
from app.storage import CosmosStorage, LocalStorage, CrawlStorage, _generate_crawl_id, _build_crawl_job


CONFIG_DIR       = os.path.join(os.path.dirname(__file__), "config")
ALL_FIRMS_CONFIG = os.path.join(CONFIG_DIR, "all_firms.json")
ROLES_CONFIG     = os.path.join(CONFIG_DIR, "roles.json")


# ── Config loading ─────────────────────────────────────────────────────────────

def load_roles() -> list[str]:
    """Load roles to scrape from config/roles.json."""
    with open(ROLES_CONFIG, "r", encoding="utf-8") as f:
        roles = json.load(f)
    if not isinstance(roles, list) or not roles:
        raise ValueError("config/roles.json must be a non-empty JSON array of role strings.")
    return [r.strip() for r in roles if isinstance(r, str) and r.strip()]


def load_sites(
    strategy: str,
    site_filter: str = "all",
    firms_config: str | None = None,
) -> list[SiteConfig]:
    """Load site configs, optionally filtered by strategy and firm name.

    firms_config: path to a JSON config file; defaults to config/all_firms.json.
    """
    config_path = firms_config or ALL_FIRMS_CONFIG
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    sites = [SiteConfig(**s) for s in raw]

    if strategy.lower() != "all":
        sites = [s for s in sites if s.strategy.value == strategy.lower()]

    if site_filter and site_filter.lower() != "all":
        sites = [s for s in sites if site_filter.lower() in s.name.lower()]

    return sites


# ── Output formatting ──────────────────────────────────────────────────────────

def _firm_lines(firm_name: str, firm_results: list[ScrapeResult], role: str) -> list[str]:
    lines = []
    successes = [r for r in firm_results if r.status == "success"]
    errors    = [r for r in firm_results if r.status == "error"]
    no_res    = [r for r in firm_results if r.status == "no_results"]
    duration  = firm_results[0].scrape_duration_sec if firm_results else 0

    if errors:
        lines.append(f"\n[ERROR]  {firm_name}  ({duration}s)")
        lines.append(f"    {errors[0].error_message}")
    elif no_res and not successes:
        lines.append(f"\n[NONE ]  {firm_name}  ({duration}s)")
        lines.append(f"    No matching jobs found for \"{role}\"")
    else:
        lines.append(f"\n[FOUND]  {firm_name}  --  {len(successes)} job(s)  ({duration}s)")
        for r in successes:
            e = r.extraction
            lines.append(f"    {'-'*50}")
            lines.append(f"    Title      : {e.role_title}")
            if e.description:
                desc = e.description.strip()
                lines.append(f"    Description: {desc[:120]}")
                if len(desc) > 120:
                    lines.append(f"               {desc[120:240]}")
                    if len(desc) > 240:
                        lines.append(f"               {desc[240:360]}...")
            if e.salary_min and e.salary_max:
                lines.append(f"    Salary Min : {e.salary_min}")
                lines.append(f"    Salary Max : {e.salary_max}")
                if e.salary_raw and e.salary_raw.strip().lower() not in ("not listed", ""):
                    lines.append(f"    Salary Raw : {e.salary_raw}")
            elif e.salary_min:
                lines.append(f"    Salary Min : {e.salary_min}+")
                if e.salary_raw and e.salary_raw.strip().lower() not in ("not listed", ""):
                    lines.append(f"    Salary Raw : {e.salary_raw}")
            elif e.salary_raw and e.salary_raw.strip().lower() not in ("not listed", ""):
                lines.append(f"    Salary     : {e.salary_raw}")
            else:
                lines.append(f"    Salary     : Not listed")
            if e.is_hourly:
                lines.append(f"    Hourly     : Yes (salary above is hourly rate)")
            exp = e.experience_years or e.experience_raw
            lines.append(f"    Experience : {exp or 'Not listed'}")
            lines.append(f"    Location   : {e.location or 'Not listed'}")
            if e.practice_area:
                lines.append(f"    Department : {e.practice_area}")
            lines.append(f"    URL        : {e.job_url}")
        if no_res:
            lines.append(f"    {'-'*50}")
            lines.append(f"    ({len(no_res)} additional result(s) with no data)")
    return lines


# ── Firm-first batch runner ────────────────────────────────────────────────────

async def run_batch_firm_first(
    sites: list[SiteConfig],
    roles: list[str],
    concurrency: int,
    output_file: str,
    role_search_terms: dict[str, list[str]],
    on_progress=None,
    storage=None,
    save_every: int = 5,
    crawl_storage=None,
    save_every_jobs: int = 20,
    stop_check=None,
) -> list[ScrapeResult]:
    """
    Process firms concurrently. For each firm, ALL roles are searched sequentially.

    Execution pattern (concurrency=3 example):
        Slot 1: [Firm A] paralegal → litigation → business development
        Slot 2: [Firm B] paralegal → litigation → business development
        Slot 3: [Firm C] paralegal → litigation → business development
        (Firm D starts once any slot frees up)

    crawl_storage  : CrawlStorage instance — when set, jobs are upserted to
                     agent_job_results every `save_every_jobs` jobs per role.
    save_every_jobs: upsert each role's crawl doc after this many jobs accumulate.
                     Default 20 — safe for large runs (no data loss if job crashes mid-run).
    stop_check     : callable() -> bool — when it returns True, no new firms are started
                     and the batch exits after active firms finish their current role.
    """
    semaphore       = asyncio.Semaphore(concurrency)
    total           = len(sites)
    aborted         = [False]
    done_count      = [0]
    total_jobs_live = [0]
    all_results: list[ScrapeResult] = []
    pending_save: list[ScrapeResult] = []
    file_lock   = asyncio.Lock()
    save_lock   = asyncio.Lock()

    # Per-role job buffer for incremental Cosmos upserts
    # Protected by save_lock — same lock used for pending_save flush
    role_jobs_buffer: dict[str, list] = {role: [] for role in roles}

    if on_progress:
        on_progress(firms_total=total)

    async def flush_pending():
        """Save buffered results to storage and clear the buffer."""
        async with save_lock:
            if pending_save and storage:
                batch = list(pending_save)
                pending_save.clear()
                try:
                    await storage.save_batch(batch)
                    jobs = sum(1 for r in batch if r.status == "success")
                    print(f"  [DB] Saved {len(batch)} results ({jobs} jobs) to storage.")
                except Exception as e:
                    print(f"  [DB][WARN] Save failed: {e}")

    async def flush_crawl_jobs(role: str, force: bool = False):
        """
        Upsert the crawl doc for a role when its buffer hits save_every_jobs.
        Set force=True to upsert regardless of count (used at end of run).
        Protected by save_lock — caller must NOT hold save_lock when calling this.
        """
        if not crawl_storage:
            return
        async with save_lock:
            jobs = role_jobs_buffer.get(role, [])
            if not jobs:
                return
            if not force and len(jobs) < save_every_jobs:
                return
            crawl_id = _generate_crawl_id(role)
            try:
                await crawl_storage.save_crawl_result(crawl_id, role, list(jobs))
            except Exception as e:
                print(f"  [DB][WARN] Incremental save failed for role '{role}': {e}")

    async def process_firm(site: SiteConfig) -> list[ScrapeResult]:
        # Check stop flag before waiting for a semaphore slot.
        # Firms already inside the semaphore finish their current role first.
        if stop_check and stop_check():
            aborted[0] = True
            return []

        async with semaphore:
            # Re-check after acquiring the slot — stop may have been requested
            # while this firm was queued waiting for a free slot.
            if stop_check and stop_check():
                aborted[0] = True
                return []

            if on_progress:
                on_progress(current_firm=site.name)

            firm_results: list[ScrapeResult] = []

            # ── Search each role sequentially within this firm ──
            for role in roles:
                if on_progress:
                    on_progress(current_role=role)
                role_results = await scrape_site(
                    site, role, role_search_terms.get(role, [])
                )
                firm_results.extend(role_results)

            # ── Console one-liner ──
            done_count[0] += 1
            new_jobs         = sum(1 for r in firm_results if r.status == "success")
            total_jobs_live[0] += new_jobs
            dur_vals         = [r.scrape_duration_sec for r in firm_results if r.scrape_duration_sec]
            dur              = round(sum(dur_vals), 1) if dur_vals else 0
            has_ok           = any(r.status == "success" for r in firm_results)
            has_err          = any(r.status == "error"   for r in firm_results)
            tag              = "OK " if has_ok else ("ERR" if has_err else "---")

            # Per-role job counts for the console line
            role_counts = "  ".join(
                f"{role}:{sum(1 for r in firm_results if r.role_searched == role and r.status == 'success')}"
                for role in roles
            )
            print(
                f"  [{tag}] [{done_count[0]:>2}/{total}] {site.name}  --  "
                f"{new_jobs} job(s)  ({dur}s)  [{role_counts}]"
            )

            if on_progress:
                on_progress(
                    firms_done=done_count[0],
                    firms_total=total,
                    jobs_found_so_far=total_jobs_live[0],
                )

            # ── Write firm section to output file (all roles grouped) ──
            lines: list[str] = [
                f"\n{'─'*70}",
                f"  FIRM: {site.name}  ({site.strategy.value})",
                f"{'─'*70}",
            ]
            for role in roles:
                role_results = [r for r in firm_results if r.role_searched == role]
                lines.append(f"\n  ── Role: {role.upper()} ──")
                lines.extend(_firm_lines(site.name, role_results, role))

            async with file_lock:
                with open(output_file, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                pending_save.extend(firm_results)

                # Accumulate new success jobs into per-role buffer
                if crawl_storage:
                    for r in firm_results:
                        if r.status == "success":
                            role_jobs_buffer[r.role_searched].append(
                                _build_crawl_job(r, r.role_searched)
                            )

            if done_count[0] % save_every == 0:
                await flush_pending()

            # Upsert any role whose buffer has hit the threshold
            if crawl_storage:
                for role in roles:
                    await flush_crawl_jobs(role)

            return firm_results

    tasks        = [asyncio.create_task(process_firm(site)) for site in sites]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    for i, r in enumerate(results_list):
        if isinstance(r, Exception):
            # Firm-level exception: create error results for every role
            for role in roles:
                all_results.append(ScrapeResult(
                    firm_name=sites[i].name,
                    strategy_used="unknown",
                    role_searched=role,
                    status="error",
                    error_message=str(r)[:500],
                ))
        else:
            all_results.extend(r)

    # Final flush — save remaining jobs in buffer regardless of count
    if pending_save:
        await flush_pending()
    if crawl_storage:
        for role in roles:
            await flush_crawl_jobs(role, force=True)

    if aborted[0]:
        firms_done = done_count[0]
        jobs_done  = total_jobs_live[0]
        print(
            f"\n  [ABORTED] Stop signal received.\n"
            f"  Firms completed : {firms_done}/{total}\n"
            f"  Firms skipped   : {total - firms_done}\n"
            f"  Jobs saved      : {jobs_done}\n"
            f"  All collected jobs have been saved to DB.\n"
        )

    return all_results, aborted[0]


# ── Core async function — called by both CLI and Azure Functions ───────────────

async def run_scraper(
    strategy:     str | None = None,
    site_filter:  str | None = None,
    concurrency:  int | None = None,
    output_file:  str | None = None,
    storage_type: str | None = None,
    roles:        list[str] | None = None,
    on_progress   = None,
    firms_config: str | None = None,
    stop_check    = None,
) -> dict:
    """
    Run the full scrape job.

    Processing order: FIRM-FIRST.
      Pre-generates search terms for all roles, then processes all firms concurrently
      (up to `concurrency`). Each firm searches all roles sequentially before releasing
      its concurrency slot.

    Parameters:
        firms_config: Path to firms JSON file. Defaults to config/all_firms.json.
                      Override via env var FIRMS_CONFIG or CLI --firms-config.
                      Use config/all_firms_test.json for local 5-firm testing.

    Returns a summary dict with counts per role.
    """
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["VERBOSE_ACTIONS"]      = os.getenv("VERBOSE_ACTIONS",   "false")
    os.environ["SAVE_GIF"]             = os.getenv("SAVE_GIF",          "false")
    os.environ["SAVE_CONVERSATION"]    = os.getenv("SAVE_CONVERSATION", "false")
    os.environ["HEADLESS"]             = os.getenv("HEADLESS",          "true")

    strategy     = strategy     or os.getenv("STRATEGY",    "all")
    site_filter  = site_filter  or os.getenv("SITE_FILTER", "all")
    concurrency  = concurrency  or int(os.getenv("CONCURRENCY", "5"))
    output_file  = output_file  or os.getenv("OUTPUT_FILE",  "output.txt")
    storage_type = storage_type or os.getenv("STORAGE",      "cosmos")
    firms_config = firms_config or os.getenv("FIRMS_CONFIG") or None

    sites = load_sites(strategy, site_filter, firms_config)
    if not sites:
        raise ValueError(f"No sites found for strategy='{strategy}' filter='{site_filter}'")

    started_at   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    config_label = os.path.basename(firms_config) if firms_config else "all_firms.json"

    # ── Step 1: Load roles + search terms ────────────────────────────────────────

    crawl_storage: CrawlStorage | None = None
    role_docs:     list[dict]          = []
    role_search_terms: dict[str, list[str]] = {}

    if storage_type == "cosmos":
        # NEW FLOW: read roles from analyses container, use similar_roles as search terms
        crawl_storage = CrawlStorage()
        await crawl_storage.connect()

        role_docs = await crawl_storage.read_analyses()
        if not role_docs:
            raise ValueError("No role documents found in the analyses container.")

        roles = [doc["role"] for doc in role_docs]
        role_search_terms = {
            doc["role"]: doc.get("similar_roles") or [doc["role"]]
            for doc in role_docs
        }
        save_storage = None  # results are aggregated per-role and saved after the batch
    else:
        # LOCAL FALLBACK: use roles.json + LLM-generated search terms
        local_storage = LocalStorage()
        await local_storage.connect()
        save_storage = local_storage

        roles = roles or load_roles()
        for i, role in enumerate(roles):
            if on_progress:
                on_progress(current_role=role, role_index=i + 1)
            print(f"\n  Generating search terms for \"{role}\" ...")
            role_search_terms[role] = await generate_search_terms(role)
            print(f"  Search terms ({len(role_search_terms[role])}): {', '.join(role_search_terms[role])}")

    run_header = (
        f"\n{'='*70}\n"
        f"  HR SALARY SCRAPER\n"
        f"  Roles       : {len(roles)} role(s)\n"
        f"  Strategy    : {strategy}\n"
        f"  Firms       : {len(sites)}\n"
        f"  Concurrency : {concurrency}\n"
        f"  Storage     : {storage_type}\n"
        f"  Output      : {output_file}\n"
        f"  Config      : {config_label}\n"
        f"  Started     : {started_at}\n"
        f"{'='*70}\n"
    )
    print(run_header)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(run_header)

    # Write search terms block to output file
    terms_section = [
        f"\n{'─'*70}",
        f"  SEARCH TERMS (from {'analyses container' if storage_type == 'cosmos' else 'roles.json + LLM'})",
        f"{'─'*70}",
    ]
    for role, terms in role_search_terms.items():
        terms_section.append(f"  {role}: {', '.join(terms[:5])}{'...' if len(terms) > 5 else ''}")
    with open(output_file, "a", encoding="utf-8") as f:
        f.write("\n".join(terms_section) + "\n")

    if on_progress:
        on_progress(total_roles=len(roles), firms_total=len(sites))

    batch_header = (
        f"\n{'─'*70}\n"
        f"  Processing {len(sites)} firm(s) × {len(roles)} role(s)  "
        f"(concurrency={concurrency})\n"
        f"  Order: firm-first — all roles searched per firm before next firm starts\n"
        f"{'─'*70}\n"
    )
    print(batch_header)
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(batch_header)

    # ── Step 2: Run firm-first batch ─────────────────────────────────────────────

    all_results, was_aborted = await run_batch_firm_first(
        sites             = sites,
        roles             = roles,
        concurrency       = concurrency,
        output_file       = output_file,
        role_search_terms = role_search_terms,
        on_progress       = on_progress,
        storage           = save_storage,
        save_every        = 5,
        crawl_storage     = crawl_storage,
        save_every_jobs   = int(os.getenv("SAVE_EVERY_JOBS", "20")),
        stop_check        = stop_check,
    )

    # ── Step 3: Build per-role summary + Cosmos save ──────────────────────────────

    summary        = {"started_at": started_at, "roles": [], "aborted": was_aborted}
    total_jobs_all = 0

    summary_lines = [
        f"\n{'─'*70}",
        f"  SUMMARY BY ROLE",
        f"{'─'*70}",
    ]

    # Build role→doc_id lookup for analyses updates
    role_to_doc_id = {doc["role"]: doc["id"] for doc in role_docs}

    for role in roles:
        role_results  = [r for r in all_results if r.role_searched == role]
        total_firms   = len({r.firm_name for r in role_results})
        success_firms = len({r.firm_name for r in role_results if r.status == "success"})
        total_jobs    = sum(1 for r in role_results if r.status == "success")
        error_firms   = len({r.firm_name for r in role_results if r.status == "error"})
        nores_firms   = max(0, total_firms - success_firms - error_firms)

        role_line = (
            f"  [{role.upper()}] "
            f"{success_firms}/{total_firms} firms  |  "
            f"{total_jobs} jobs  |  "
            f"{nores_firms} no results  |  "
            f"{error_firms} errors"
        )
        print(role_line)
        summary_lines.append(role_line)

        summary["roles"].append({
            "role":           role,
            "firms_run":      total_firms,
            "firms_success":  success_firms,
            "jobs_found":     total_jobs,
            "firms_error":    error_firms,
            "firms_noresult": nores_firms,
        })
        total_jobs_all += total_jobs

        # Patch the analyses doc now that the full crawl is done
        # (job saving happened incrementally inside run_batch_firm_first)
        if crawl_storage:
            crawl_id = _generate_crawl_id(role)
            doc_id   = role_to_doc_id.get(role)
            if doc_id:
                await crawl_storage.update_analysis(doc_id, crawl_id)

    if on_progress:
        on_progress(jobs_found_so_far=total_jobs_all)

    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    footer = (
        f"\n{'='*70}\n"
        f"  Run complete\n"
        f"  Roles       : {len(roles)}\n"
        f"  Firms       : {len(sites)}\n"
        f"  Total jobs  : {total_jobs_all}\n"
        f"  Finished    : {finished_at}\n"
        f"{'='*70}\n"
    )

    with open(output_file, "a", encoding="utf-8") as f:
        f.write("\n".join(summary_lines) + "\n")
        f.write(footer)

    save_loc   = "Azure Cosmos DB (analyses → agent_job_results)" if crawl_storage else "results.json (local)"
    run_label  = "ABORTED" if was_aborted else "Done"
    firms_done = len({r.firm_name for r in all_results})
    print(f"\n{'-'*70}")
    print(f"  {run_label}  |  {len(roles)} roles  |  {firms_done}/{len(sites)} firms  |  {total_jobs_all} jobs  |  {finished_at}")
    print(f"  TXT  -> {output_file}")
    print(f"  DB   -> {save_loc}")
    print(f"{'-'*70}\n")
    summary["firms_done"]  = firms_done
    summary["firms_total"] = len(sites)

    if crawl_storage:
        await crawl_storage.close()
    elif save_storage:
        await save_storage.close()

    summary["finished_at"] = finished_at
    summary["total_jobs"]  = total_jobs_all
    return summary


# ── CLI entry point ────────────────────────────────────────────────────────────

async def main():
    env_strategy    = os.getenv("STRATEGY",     "videsktop")
    env_concurrency = int(os.getenv("CONCURRENCY", "5"))
    env_output      = os.getenv("OUTPUT_FILE",   "output.txt")
    env_storage     = os.getenv("STORAGE",       "local")
    env_site_filter = os.getenv("SITE_FILTER",   "all")
    env_firms_cfg   = os.getenv("FIRMS_CONFIG",  "")

    parser = argparse.ArgumentParser(description="HR Salary Scraper")
    parser.add_argument(
        "--strategy", type=str, default=env_strategy,
        choices=["videsktop", "workday", "icims", "ultipro", "florecruit", "direct", "all"],
    )
    parser.add_argument("--concurrency",  type=int, default=env_concurrency)
    parser.add_argument("--output",       type=str, default=env_output)
    parser.add_argument("--storage",      type=str, default=env_storage, choices=["local", "cosmos"])
    parser.add_argument("--filter",       type=str, default=env_site_filter)
    parser.add_argument(
        "--firms-config", type=str, default=env_firms_cfg or None,
        metavar="PATH",
        help="Path to firms JSON config (default: config/all_firms.json). "
             "Use config/all_firms_test.json for local 5-firm testing.",
    )
    args = parser.parse_args()

    await run_scraper(
        strategy     = args.strategy,
        site_filter  = args.filter,
        concurrency  = args.concurrency,
        output_file  = args.output,
        storage_type = args.storage,
        firms_config = args.firms_config,
    )


if __name__ == "__main__":
    asyncio.run(main())
