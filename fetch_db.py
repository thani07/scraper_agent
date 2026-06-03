"""
Fetch all documents from Cosmos DB and print a summary report.

Schema (v2):
    Partition key : role_title    — actual job title from the website
    searched_role               — the keyword used to find this job (e.g. "paralegal")

Usage:
    python fetch_db.py                           # summary grouped by searched_role
    python fetch_db.py --role paralegal          # only jobs found when searching "paralegal"
    python fetch_db.py --export                  # save everything to db_export.json
"""

import asyncio
import json
import os
import argparse
from dotenv import load_dotenv

load_dotenv()


async def fetch_all():
    from azure.cosmos.aio import CosmosClient
    from azure.cosmos import PartitionKey

    endpoint = os.getenv("COSMOS_ENDPOINT")
    key      = os.getenv("COSMOS_KEY", "")
    db_name  = os.getenv("COSMOS_DATABASE",  "hrsalarydb")
    con_name = os.getenv("COSMOS_CONTAINER", "agent_job_results")

    # Parse connection string if needed
    if "AccountKey=" in key:
        for part in key.split(";"):
            if part.startswith("AccountKey="):
                key = part[len("AccountKey="):]
                break

    client    = CosmosClient(endpoint, credential=key)
    db        = client.get_database_client(db_name)
    container = db.get_container_client(con_name)

    docs = []
    async for item in container.read_all_items():
        docs.append(item)

    await client.close()
    return docs


def print_report(docs, role_filter=None):
    # Filter by searched_role if requested (e.g. --role paralegal)
    if role_filter:
        docs = [d for d in docs if d.get("searched_role", "").lower() == role_filter.lower()]

    if not docs:
        print("No documents found.")
        return

    # ── Summary counts ─────────────────────────────────────────────────────
    from collections import defaultdict
    by_searched_role = defaultdict(list)
    by_status        = defaultdict(int)

    for d in docs:
        by_searched_role[d.get("searched_role", "unknown")].append(d)
        by_status[d.get("status", "unknown")] += 1

    print(f"\n{'='*70}")
    print(f"  COSMOS DB REPORT — {db_name()} / {con_name()}")
    print(f"  Total documents  : {len(docs)}")
    print(f"  Status breakdown : " + "  |  ".join(f"{k}={v}" for k, v in by_status.items()))
    print(f"  Partition key    : role_title (actual job title from website)")
    print(f"{'='*70}")

    for searched_role, role_docs in sorted(by_searched_role.items()):
        successes = [d for d in role_docs if d.get("status") == "success"]
        errors    = [d for d in role_docs if d.get("status") == "error"]
        no_res    = [d for d in role_docs if d.get("status") == "no_results"]

        print(f"\n  SEARCHED ROLE: {searched_role.upper()}")
        print(f"    Total docs  : {len(role_docs)}")
        print(f"    Success     : {len(successes)}")
        print(f"    No results  : {len(no_res)}")
        print(f"    Errors      : {len(errors)}")

        if successes:
            print(f"\n    Jobs found ({len(successes)}):")
            for d in successes:
                # role_title is now the partition key — the actual title from the website
                job_title = d.get("role_title", "?")
                salary = ""
                if d.get("salary_min") and d.get("salary_max"):
                    salary = f"  |  {d['salary_min']} – {d['salary_max']}"
                elif d.get("salary_min"):
                    salary = f"  |  {d['salary_min']}+"
                elif d.get("salary_raw") and d.get("salary_raw") not in ("Not listed", ""):
                    salary = f"  |  {d['salary_raw']}"
                hourly = "  [HOURLY]" if d.get("is_hourly") else ""
                print(f"      [{d.get('firm_name','?')}]  {job_title}{salary}{hourly}")
                if d.get("location"):
                    print(f"        Location   : {d['location']}")
                if d.get("experience_years"):
                    print(f"        Experience : {d['experience_years']}")
                if d.get("practice_area"):
                    print(f"        Department : {d['practice_area']}")
                if d.get("job_url"):
                    print(f"        URL        : {d['job_url']}")
                print()

        if errors:
            print(f"    Errors ({len(errors)}):")
            shown = {}
            for d in errors:
                firm = d.get("firm_name", "?")
                if firm not in shown:
                    shown[firm] = d.get("error_message", "")[:120]
                    print(f"      [{firm}]  {shown[firm]}")

    print(f"\n{'='*70}\n")


def db_name():
    return os.getenv("COSMOS_DATABASE", "hrsalarydb")

def con_name():
    return os.getenv("COSMOS_CONTAINER", "agent_job_results")


async def main():
    parser = argparse.ArgumentParser(description="Fetch Cosmos DB scrape results")
    parser.add_argument("--role",   type=str, default=None, help="Filter by searched_role (e.g. paralegal, litigation)")
    parser.add_argument("--export", action="store_true",    help="Export all docs to db_export.json")
    args = parser.parse_args()

    print("Connecting to Cosmos DB...")
    docs = await fetch_all()
    print(f"Fetched {len(docs)} documents.")

    if args.export:
        with open("db_export.json", "w", encoding="utf-8") as f:
            json.dump(docs, f, indent=2, default=str, ensure_ascii=False)
        print(f"Exported to db_export.json")

    print_report(docs, role_filter=args.role)


if __name__ == "__main__":
    asyncio.run(main())
