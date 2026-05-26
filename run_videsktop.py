"""
Run videsktop firms — 5 at a time, each in its own isolated browser.
Reads  : config/videsktop_firms.json
Outputs: videsktop_output.txt  (written live as each firm finishes)

Usage:
    python run_videsktop.py "analyst"
    python run_videsktop.py "paralegal"
    python run_videsktop.py "HR Manager"
    python run_videsktop.py "analyst" --concurrency 3   # override default 5
"""

# ── Suppress all library logs before any imports ──────────────────────────────
import logging
logging.disable(logging.CRITICAL)

import os
import sys
import json
import asyncio
import argparse
from datetime import datetime

# Ensure UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

# Force-set batch-run defaults — override .env so no verbose logs, no GIFs,
# no conversation files leak into the clean output.
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["VERBOSE_ACTIONS"]      = "false"
os.environ["SAVE_GIF"]             = "false"
os.environ["SAVE_CONVERSATION"]    = "false"
os.environ["HEADLESS"]             = "true"   # no browser windows during batch run

from app.models import SiteConfig, ScrapeResult
from app.scraper import scrape_site


OUTPUT_FILE = "videsktop_output.txt"
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config", "videsktop_firms.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_videsktop_sites() -> list[SiteConfig]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [SiteConfig(**s) for s in raw]


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
        lines.append(f"\n[FOUND]  {firm_name}  —  {len(successes)} job(s)  ({duration}s)")
        for r in successes:
            e = r.extraction
            lines.append(f"    {'─'*50}")
            lines.append(f"    Title      : {e.role_title}")
            if e.description:
                # Wrap description at 80 chars for readability
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
            lines.append(f"    {'─'*50}")
            lines.append(f"    ({len(no_res)} additional result(s) with no data)")
    return lines


def _status_icon(firm_results: list[ScrapeResult]) -> str:
    if any(r.status == "error" for r in firm_results):
        return "[ERR]"
    if any(r.status == "success" for r in firm_results):
        return "[OK ]"
    return "[---]"


# ── Core concurrent runner ────────────────────────────────────────────────────

async def run_all(sites: list[SiteConfig], role: str, concurrency: int, output_file: str):
    """
    Run up to `concurrency` firms simultaneously.
    Each firm gets its own isolated Browser — no shared tabs.
    Results are written to output_file as each firm finishes (live).
    """
    semaphore = asyncio.Semaphore(concurrency)
    total     = len(sites)
    done_count = [0]
    all_results: list[ScrapeResult] = []
    file_lock = asyncio.Lock()

    async def run_one(site: SiteConfig) -> list[ScrapeResult]:
        async with semaphore:
            results = await scrape_site(site, role)

            # Update counter and write this firm's results immediately
            done_count[0] += 1
            icon = _status_icon(results)
            jobs = sum(1 for r in results if r.status == "success")
            dur  = results[0].scrape_duration_sec if results else 0
            print(f"  {icon} [{done_count[0]:>2}/{total}] {site.name}  —  {jobs} job(s)  ({dur}s)")

            lines = _firm_lines(site.name, results, role)
            async with file_lock:
                with open(output_file, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")

            return results

    # Launch all firms; semaphore limits how many run at once
    tasks = [asyncio.create_task(run_one(site)) for site in sites]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    for i, r in enumerate(results_list):
        if isinstance(r, Exception):
            all_results.append(ScrapeResult(
                firm_name=sites[i].name,
                strategy_used="videsktop",
                role_searched=role,
                status="error",
                error_message=str(r)[:500],
            ))
        else:
            all_results.extend(r)

    return all_results


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("role", nargs="?", default=None)
    parser.add_argument("--concurrency", type=int, default=5)
    args, _ = parser.parse_known_args()

    if not args.role:
        print('Usage: python run_videsktop.py "<role>" [--concurrency N]')
        print('Example: python run_videsktop.py "analyst"')
        print('         python run_videsktop.py "paralegal" --concurrency 3')
        sys.exit(1)

    role        = args.role.strip()
    concurrency = args.concurrency
    sites       = load_videsktop_sites()
    started_at  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\nViDesktop batch scrape")
    print(f"  Role        : {role}")
    print(f"  Firms       : {len(sites)}")
    print(f"  Concurrency : {concurrency} firms at a time (each in its own browser)")
    print(f"  Output file : {OUTPUT_FILE}")
    print(f"  Started     : {started_at}")
    print(f"{'─'*55}")

    # Write header to output file (overwrite previous run)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write(f'  VIDESKTOP SCRAPE — role: "{role}"\n')
        f.write(f"  Started     : {started_at}\n")
        f.write(f"  Firms       : {len(sites)}\n")
        f.write(f"  Concurrency : {concurrency}\n")
        f.write("=" * 70 + "\n")

    all_results = await run_all(sites, role, concurrency, OUTPUT_FILE)

    # Write footer
    finished_at   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_firms   = len({r.firm_name for r in all_results})
    success_firms = len({r.firm_name for r in all_results if r.status == "success"})
    total_jobs    = sum(1 for r in all_results if r.status == "success")
    nores_firms   = len({r.firm_name for r in all_results if r.status == "no_results"}) - success_firms
    error_firms   = len({r.firm_name for r in all_results if r.status == "error"})

    footer = (
        f"\n{'─'*70}\n"
        f"  Finished    : {finished_at}\n"
        f"  {success_firms}/{total_firms} firms had results | "
        f"{total_jobs} total jobs | "
        f"{max(0,nores_firms)} no results | "
        f"{error_firms} errors\n"
        f"{'─'*70}\n"
    )
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(footer)

    print(f"{'─'*55}")
    print(f"  Done  {success_firms}/{total_firms} firms | {total_jobs} jobs | finished {finished_at}")
    print(f"  Full results → {OUTPUT_FILE}\n")


if __name__ == "__main__":
    asyncio.run(main())
