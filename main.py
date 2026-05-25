"""
HR Salary Scraper — Main Entry Point
=====================================
Usage:
    python main.py --role "Associate Attorney" --sites all
    python main.py --role "Paralegal" --sites "Kirkland & Ellis"
    python main.py --role "Corporate Associate" --sites all --storage cosmos
"""

import asyncio
import json
import sys
import os
import argparse
from datetime import datetime

# Ensure UTF-8 output on Windows so emoji characters render correctly
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

from app.models import SiteConfig, ScrapeResult
from app.scraper import scrape_site, scrape_all_sites
from app.storage import CosmosStorage, LocalStorage


def load_sites(filter_name: str = "all") -> list[SiteConfig]:
    """Load site configs from JSON, optionally filter by name."""
    config_path = os.path.join(os.path.dirname(__file__), "config", "sites.json")
    with open(config_path, "r") as f:
        raw = json.load(f)

    sites = [SiteConfig(**s) for s in raw]

    if filter_name and filter_name.lower() != "all":
        sites = [s for s in sites if filter_name.lower() in s.name.lower()]

    return sites


def print_results(results: list[ScrapeResult]):
    """Pretty-print results to console."""
    print("\n" + "=" * 70)
    print(f"  SCRAPE RESULTS — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    for r in results:
        status_icon = "✅" if r.status == "success" else "❌" if r.status == "error" else "⚠️"
        print(f"\n{status_icon}  {r.firm_name} ({r.strategy_used})")
        print(f"   Role searched: {r.role_searched}")
        print(f"   Duration: {r.scrape_duration_sec}s")

        if r.status == "error":
            print(f"   Error: {r.error_message}")
        elif r.extraction:
            e = r.extraction
            print(f"   Title: {e.role_title}")
            if e.salary_min and e.salary_max:
                print(f"   Salary: {e.salary_min} — {e.salary_max}")
            elif e.salary_raw:
                print(f"   Salary: {e.salary_raw}")
            else:
                print(f"   Salary: Not found")
            print(f"   Experience: {e.experience_years or e.experience_raw or 'Not found'}")
            print(f"   Location: {e.location or 'Not found'}")
            print(f"   URL: {e.job_url}")
        else:
            print(f"   No data extracted")

    # Summary
    success = sum(1 for r in results if r.status == "success")
    errors = sum(1 for r in results if r.status == "error")
    no_results = sum(1 for r in results if r.status == "no_results")
    print(f"\n{'─' * 70}")
    print(f"  Summary: {success} success | {no_results} no results | {errors} errors")
    print(f"{'─' * 70}\n")


async def main():
    parser = argparse.ArgumentParser(description="HR Salary Scraper — AI-powered job data extraction")
    parser.add_argument("--role", type=str, required=True, help='Job role to search (e.g. "Associate Attorney")')
    parser.add_argument("--sites", type=str, default="all", help='Site filter: "all" or firm name substring')
    parser.add_argument("--storage", type=str, default="local", choices=["local", "cosmos"], help="Storage backend")
    parser.add_argument("--single", action="store_true", help="Run sites sequentially instead of concurrently")

    args = parser.parse_args()

    # Load sites
    sites = load_sites(args.sites)
    if not sites:
        print(f"❌ No sites found matching '{args.sites}'")
        sys.exit(1)

    print(f"\n🔍 Searching for '{args.role}' across {len(sites)} site(s)...\n")
    for s in sites:
        print(f"   • {s.name} ({s.strategy.value}) — {s.careers_url}")
    print()

    # Initialize storage
    if args.storage == "cosmos":
        storage = CosmosStorage()
    else:
        storage = LocalStorage()
    await storage.connect()

    # Run scraper
    if args.single:
        results = []
        for site in sites:
            print(f"⏳ Scraping {site.name}...")
            result = await scrape_site(site, args.role)
            results.append(result)
            print(f"   Done in {result.scrape_duration_sec}s — {result.status}")
    else:
        results = await scrape_all_sites(sites, args.role)

    # Save results
    await storage.save_batch(results)

    # Print results
    print_results(results)

    save_location = "results.json" if args.storage == "local" else "Azure Cosmos DB"
    print(f"💾 Results saved to {save_location}")

    await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
