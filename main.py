"""
HR Salary Scraper - Main Entry Point

All configuration is driven by environment variables (.env file).
Roles to scrape are read from config/roles.json.

Usage:
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
        print("ERROR: config/roles.json must be a non-empty JSON array of role strings.")
        sys.exit(1)
    return [r.strip() for r in roles if isinstance(r, str) and r.strip()]


def load_sites(strategy: str, site_filter: str = "all") -> list[SiteConfig]:
    """Load site configs from all_firms.json, optionally filtered by strategy and name."""
    with open(ALL_FIRMS_CONFIG, "r", encoding="utf-8") as f:
        raw = json.load(f)

    sites = [SiteConfig(**s) for s in raw]

    # Filter by strategy when not "all"
    if strategy.lower() != "all":
        sites = [s for s in sites if s.strategy.value == strategy.lower()]

    if site_filter and site_filter.lower() != "all":
        sites = [s for s in sites if site_filter.lower() in s.name.lower()]

    return sites


# ── Output formatting ──────────────────────────────────────────────────────────

def _firm_lines(firm_name: str, firm_results: list[ScrapeResult], role: str) -> list[str]:
    """Format one firm's results into output lines."""
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
    """Run all sites concurrently for one role, writing results live to output_file."""
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


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    env_strategy    = os.getenv("STRATEGY",    "videsktop")
    env_concurrency = int(os.getenv("CONCURRENCY", os.getenv("MAX_CONCURRENT", "5")))
    env_output      = os.getenv("OUTPUT_FILE",  "output.txt")
    env_storage     = os.getenv("STORAGE",      "local")
    env_site_filter = os.getenv("SITE_FILTER",  "all")

    parser = argparse.ArgumentParser(
        description="HR Salary Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Roles are read from config/roles.json. All other options via .env or CLI.",
    )
    parser.add_argument("--strategy",    type=str, default=env_strategy,
                        choices=["videsktop", "all", "workday", "icims", "ultipro", "florecruit", "direct"],
                        help=f"Which firms to run. ENV: STRATEGY (current: '{env_strategy}')")
    parser.add_argument("--concurrency", type=int, default=env_concurrency,
                        help=f"Concurrent browser sessions. ENV: CONCURRENCY (current: {env_concurrency})")
    parser.add_argument("--output",      type=str, default=env_output,
                        help=f"Output file path. ENV: OUTPUT_FILE (current: '{env_output}')")
    parser.add_argument("--storage",     type=str, default=env_storage,
                        choices=["local", "cosmos"],
                        help=f"Storage backend. ENV: STORAGE (current: '{env_storage}')")
    parser.add_argument("--filter",      type=str, default=env_site_filter,
                        help="Narrow to one firm by name substring. ENV: SITE_FILTER")

    args = parser.parse_args()

    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["VERBOSE_ACTIONS"]      = os.getenv("VERBOSE_ACTIONS",   "false")
    os.environ["SAVE_GIF"]             = os.getenv("SAVE_GIF",          "false")
    os.environ["SAVE_CONVERSATION"]    = os.getenv("SAVE_CONVERSATION", "false")
    os.environ["HEADLESS"]             = os.getenv("HEADLESS",          "true")

    # Load roles and sites once
    roles = load_roles()
    sites = load_sites(args.strategy, args.filter)
    if not sites:
        print(f"ERROR: No sites found  (strategy='{args.strategy}'  filter='{args.filter}')")
        sys.exit(1)

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    run_header = (
        f"\n{'='*70}\n"
        f"  HR SALARY SCRAPER\n"
        f"  Roles       : {', '.join(roles)}\n"
        f"  Strategy    : {args.strategy}\n"
        f"  Firms       : {len(sites)}\n"
        f"  Concurrency : {args.concurrency}\n"
        f"  Storage     : {args.storage}\n"
        f"  Output      : {args.output}\n"
        f"  Started     : {started_at}\n"
        f"{'='*70}\n"
    )
    print(run_header)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(run_header)

    # Storage backend — shared across all roles so all results share one run_id
    storage = CosmosStorage() if args.storage == "cosmos" else LocalStorage()
    await storage.connect()

    total_jobs_all_roles  = 0
    total_firms_all_roles = 0

    # ── One full batch per role ────────────────────────────────────────────────
    for role in roles:
        print(f"\n  Generating search terms for \"{role}\" ...")
        search_terms = await generate_search_terms(role)
        print(f"  Search terms ({len(search_terms)}):")
        for i, t in enumerate(search_terms):
            prefix = "  [user]" if i == 0 else "  [ AI ]"
            print(f"    {prefix}  {t}")

        role_header = (
            f"\n{'-'*70}\n"
            f"  ROLE: {role.upper()}\n"
            f"  Search terms: {', '.join(search_terms)}\n"
            f"  Firms: {len(sites)}\n"
            f"{'-'*70}\n"
        )
        print(role_header)
        with open(args.output, "a", encoding="utf-8") as f:
            f.write(role_header)

        all_results = await run_batch(
            sites, role, args.concurrency, args.output, search_terms=search_terms
        )

        # Save this role's results to storage
        await storage.save_batch(all_results)

        # Role summary
        total_firms   = len({r.firm_name for r in all_results})
        success_firms = len({r.firm_name for r in all_results if r.status == "success"})
        total_jobs    = sum(1 for r in all_results if r.status == "success")
        error_firms   = len({r.firm_name for r in all_results if r.status == "error"})
        nores_firms   = max(0, total_firms - success_firms - error_firms)

        role_summary = (
            f"\n  [{role.upper()}] "
            f"{success_firms}/{total_firms} firms  |  "
            f"{total_jobs} jobs  |  "
            f"{nores_firms} no results  |  "
            f"{error_firms} errors\n"
        )
        print(role_summary)
        with open(args.output, "a", encoding="utf-8") as f:
            f.write(role_summary)

        total_jobs_all_roles  += total_jobs
        total_firms_all_roles  = len(sites)

    # ── Final footer ───────────────────────────────────────────────────────────
    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    footer = (
        f"\n{'='*70}\n"
        f"  Run complete\n"
        f"  Roles       : {len(roles)} ({', '.join(roles)})\n"
        f"  Firms       : {total_firms_all_roles}\n"
        f"  Total jobs  : {total_jobs_all_roles}\n"
        f"  Finished    : {finished_at}\n"
        f"{'='*70}\n"
    )
    with open(args.output, "a", encoding="utf-8") as f:
        f.write(footer)

    save_loc = "results.json" if args.storage == "local" else "Azure Cosmos DB"
    print(f"\n{'-'*70}")
    print(f"  Done  |  {len(roles)} roles  |  {total_jobs_all_roles} total jobs  |  finished {finished_at}")
    print(f"  Results -> {args.output}  |  Storage -> {save_loc}")
    print(f"{'-'*70}\n")

    await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
