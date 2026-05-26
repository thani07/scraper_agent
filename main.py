"""
HR Salary Scraper - Core logic + CLI entry point.

run_scraper()  — importable async function called by Azure Functions triggers
main()         — CLI entry point (reads argparse + .env)

CLI usage:
    python main.py                         # uses .env + config/roles.json
    python main.py --strategy videsktop    # override strategy
    python main.py --filter "Jones Day"    # run one firm only
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
from app.storage import CosmosStorage, LocalStorage


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


def load_sites(strategy: str, site_filter: str = "all") -> list[SiteConfig]:
    """Load site configs from all_firms.json, optionally filtered by strategy and name."""
    with open(ALL_FIRMS_CONFIG, "r", encoding="utf-8") as f:
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


# ── Batch runner ───────────────────────────────────────────────────────────────

async def run_batch(
    sites: list[SiteConfig],
    role: str,
    concurrency: int,
    output_file: str,
    search_terms: list[str] | None = None,
) -> list[ScrapeResult]:
    semaphore   = asyncio.Semaphore(concurrency)
    total       = len(sites)
    done_count  = [0]
    all_results: list[ScrapeResult] = []
    file_lock   = asyncio.Lock()

    async def run_one(site: SiteConfig) -> list[ScrapeResult]:
        async with semaphore:
            results  = await scrape_site(site, role, search_terms=search_terms)
            done_count[0] += 1
            jobs     = sum(1 for r in results if r.status == "success")
            dur      = results[0].scrape_duration_sec if results else 0
            has_ok   = any(r.status == "success"  for r in results)
            has_err  = any(r.status == "error"    for r in results)
            tag      = "OK " if has_ok else ("ERR" if has_err else "---")
            print(f"  [{tag}] [{done_count[0]:>2}/{total}] {site.name}  --  {jobs} job(s)  ({dur}s)")

            lines = _firm_lines(site.name, results, role)
            async with file_lock:
                with open(output_file, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")

            return results

    tasks        = [asyncio.create_task(run_one(site)) for site in sites]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    for i, r in enumerate(results_list):
        if isinstance(r, Exception):
            all_results.append(ScrapeResult(
                firm_name=sites[i].name,
                strategy_used="unknown",
                role_searched=role,
                status="error",
                error_message=str(r)[:500],
            ))
        else:
            all_results.extend(r)

    return all_results


# ── Core async function — called by both CLI and Azure Functions ───────────────

async def run_scraper(
    strategy:    str | None = None,
    site_filter: str | None = None,
    concurrency: int | None = None,
    output_file: str | None = None,
    storage_type: str | None = None,
    roles:       list[str] | None = None,
) -> dict:
    """
    Run the full scrape job.

    All parameters fall back to environment variables when not provided.
    Returns a summary dict with counts per role.
    """
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["VERBOSE_ACTIONS"]      = os.getenv("VERBOSE_ACTIONS",   "false")
    os.environ["SAVE_GIF"]             = os.getenv("SAVE_GIF",          "false")
    os.environ["SAVE_CONVERSATION"]    = os.getenv("SAVE_CONVERSATION", "false")
    os.environ["HEADLESS"]             = os.getenv("HEADLESS",          "true")

    strategy     = strategy     or os.getenv("STRATEGY",    "videsktop")
    site_filter  = site_filter  or os.getenv("SITE_FILTER", "all")
    concurrency  = concurrency  or int(os.getenv("CONCURRENCY", "5"))
    output_file  = output_file  or os.getenv("OUTPUT_FILE",  "output.txt")
    storage_type = storage_type or os.getenv("STORAGE",      "local")
    roles        = roles        or load_roles()

    sites = load_sites(strategy, site_filter)
    if not sites:
        raise ValueError(f"No sites found for strategy='{strategy}' filter='{site_filter}'")

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    run_header = (
        f"\n{'='*70}\n"
        f"  HR SALARY SCRAPER\n"
        f"  Roles       : {', '.join(roles)}\n"
        f"  Strategy    : {strategy}\n"
        f"  Firms       : {len(sites)}\n"
        f"  Concurrency : {concurrency}\n"
        f"  Storage     : {storage_type}\n"
        f"  Output      : {output_file}\n"
        f"  Started     : {started_at}\n"
        f"{'='*70}\n"
    )
    print(run_header)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(run_header)

    storage = CosmosStorage() if storage_type == "cosmos" else LocalStorage()
    await storage.connect()

    summary = {"started_at": started_at, "roles": []}
    total_jobs_all = 0

    for role in roles:
        print(f"\n  Generating search terms for \"{role}\" ...")
        search_terms = await generate_search_terms(role)
        print(f"  Search terms ({len(search_terms)}): {', '.join(search_terms)}")

        role_header = (
            f"\n{'-'*70}\n"
            f"  ROLE: {role.upper()}\n"
            f"  Search terms: {', '.join(search_terms)}\n"
            f"  Firms: {len(sites)}\n"
            f"{'-'*70}\n"
        )
        print(role_header)
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(role_header)

        all_results = await run_batch(sites, role, concurrency, output_file, search_terms)
        await storage.save_batch(all_results)

        total_firms   = len({r.firm_name for r in all_results})
        success_firms = len({r.firm_name for r in all_results if r.status == "success"})
        total_jobs    = sum(1 for r in all_results if r.status == "success")
        error_firms   = len({r.firm_name for r in all_results if r.status == "error"})
        nores_firms   = max(0, total_firms - success_firms - error_firms)

        role_summary_line = (
            f"\n  [{role.upper()}] "
            f"{success_firms}/{total_firms} firms  |  "
            f"{total_jobs} jobs  |  "
            f"{nores_firms} no results  |  "
            f"{error_firms} errors\n"
        )
        print(role_summary_line)
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(role_summary_line)

        summary["roles"].append({
            "role":          role,
            "firms_run":     total_firms,
            "firms_success": success_firms,
            "jobs_found":    total_jobs,
            "firms_error":   error_firms,
            "firms_noresult": nores_firms,
        })
        total_jobs_all += total_jobs

    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    footer = (
        f"\n{'='*70}\n"
        f"  Run complete\n"
        f"  Roles       : {len(roles)} ({', '.join(roles)})\n"
        f"  Firms       : {len(sites)}\n"
        f"  Total jobs  : {total_jobs_all}\n"
        f"  Finished    : {finished_at}\n"
        f"{'='*70}\n"
    )
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(footer)

    # Report actual storage used (CosmosStorage falls back to local if connection fails)
    cosmos_connected = (
        storage_type == "cosmos"
        and hasattr(storage, "container")
        and storage.container is not None
    )
    save_loc = "Azure Cosmos DB" if cosmos_connected else "results.json (Cosmos unavailable — saved locally)"
    print(f"\n{'-'*70}")
    print(f"  Done  |  {len(roles)} roles  |  {total_jobs_all} total jobs  |  finished {finished_at}")
    print(f"  Results -> {output_file}  |  Storage -> {save_loc}")
    print(f"{'-'*70}\n")

    await storage.close()

    summary["finished_at"] = finished_at
    summary["total_jobs"]  = total_jobs_all
    return summary


# ── CLI entry point ────────────────────────────────────────────────────────────

async def main():
    env_strategy    = os.getenv("STRATEGY",    "videsktop")
    env_concurrency = int(os.getenv("CONCURRENCY", "5"))
    env_output      = os.getenv("OUTPUT_FILE",  "output.txt")
    env_storage     = os.getenv("STORAGE",      "local")
    env_site_filter = os.getenv("SITE_FILTER",  "all")

    parser = argparse.ArgumentParser(description="HR Salary Scraper")
    parser.add_argument("--strategy",    type=str, default=env_strategy,
                        choices=["videsktop", "all", "workday", "icims", "ultipro", "florecruit", "direct"])
    parser.add_argument("--concurrency", type=int, default=env_concurrency)
    parser.add_argument("--output",      type=str, default=env_output)
    parser.add_argument("--storage",     type=str, default=env_storage, choices=["local", "cosmos"])
    parser.add_argument("--filter",      type=str, default=env_site_filter)
    args = parser.parse_args()

    await run_scraper(
        strategy     = args.strategy,
        site_filter  = args.filter,
        concurrency  = args.concurrency,
        output_file  = args.output,
        storage_type = args.storage,
    )


if __name__ == "__main__":
    asyncio.run(main())
