"""Storage layer — saves scrape results to Azure Cosmos DB or local JSON fallback."""

import os
import json
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.models import ScrapeResult

load_dotenv()


def _build_document(result: ScrapeResult, run_id: str) -> dict:
    """
    Flatten a ScrapeResult into a single Cosmos DB document.

    Partition key: /role  — all jobs for the same role land in the same partition.
    run_id groups every document produced in one scheduled execution together.

    Document shape:
        id            — unique UUID per document
        role          — partition key  (e.g. "paralegal")
        run_id        — UUID shared by all documents in one run
        scraped_at    — ISO-8601 UTC timestamp
        firm_name
        strategy_used
        status        — success | no_results | error
        error_message — only present on error
        scrape_duration_sec
        role_title
        description
        salary_min
        salary_max
        salary_raw
        is_hourly
        experience_years
        experience_raw
        location
        job_url
        practice_area
    """
    e = result.extraction

    doc: dict = {
        "id":                   str(uuid.uuid4()),
        "role":                 result.role_searched,        # partition key
        "run_id":               run_id,
        "scraped_at":           datetime.now(timezone.utc).isoformat(),
        "firm_name":            result.firm_name,
        "strategy_used":        result.strategy_used,
        "status":               result.status,
        "scrape_duration_sec":  result.scrape_duration_sec,
    }

    if result.error_message:
        doc["error_message"] = result.error_message

    if e:
        doc["role_title"]        = e.role_title
        doc["description"]       = e.description
        doc["salary_min"]        = e.salary_min
        doc["salary_max"]        = e.salary_max
        doc["salary_raw"]        = e.salary_raw
        doc["is_hourly"]         = e.is_hourly if e.is_hourly else False
        doc["experience_years"]  = e.experience_years
        doc["experience_raw"]    = e.experience_raw
        doc["location"]          = e.location
        doc["job_url"]           = e.job_url
        doc["practice_area"]     = e.practice_area

    return doc


class CosmosStorage:
    """
    Save results to Azure Cosmos DB.

    Container setup:
        Database  : set via COSMOS_DATABASE  (e.g. "hr-scraper")
        Container : set via COSMOS_CONTAINER  (e.g. "job-results")
        Partition key path: /role

    Same role → same partition (fast cross-firm queries per role).
    Different roles → different partitions.
    """

    def __init__(self):
        self.client    = None
        self.container = None
        self.run_id    = str(uuid.uuid4())   # shared across all saves in one run

    async def connect(self):
        try:
            from azure.cosmos.aio import CosmosClient
            endpoint = os.getenv("COSMOS_ENDPOINT")
            key      = os.getenv("COSMOS_KEY")

            if not endpoint or not key:
                raise ValueError("COSMOS_ENDPOINT and COSMOS_KEY must be set")

            # COSMOS_KEY may be a full connection string
            # (e.g. "AccountEndpoint=...;AccountKey=abc123==;")
            # or just the bare account key (e.g. "abc123==").
            # Extract the bare key if a connection string was provided.
            if key.startswith("AccountEndpoint=") or "AccountKey=" in key:
                for part in key.split(";"):
                    if part.startswith("AccountKey="):
                        key = part[len("AccountKey="):]
                        break

            self.client = CosmosClient(endpoint, credential=key)
            db = self.client.get_database_client(
                os.getenv("COSMOS_DATABASE", "hr-scraper")
            )
            self.container = db.get_container_client(
                os.getenv("COSMOS_CONTAINER", "job-results")
            )
        except Exception as e:
            print(f"  [WARN] Cosmos DB connection failed: {e}")
            print("  [WARN] Falling back to local JSON storage")
            self.container = None

    async def save(self, result: ScrapeResult):
        doc = _build_document(result, self.run_id)
        if self.container:
            await self.container.upsert_item(doc)
        else:
            await _save_local(doc)

    async def save_batch(self, results: list[ScrapeResult]):
        for result in results:
            await self.save(result)

    async def close(self):
        if self.client:
            await self.client.close()


class LocalStorage:
    """Local JSON fallback — writes results.json in project root."""

    def __init__(self, filepath: str = "results.json"):
        self.filepath = filepath
        self.run_id   = str(uuid.uuid4())

    async def connect(self):
        pass

    async def save(self, result: ScrapeResult):
        doc = _build_document(result, self.run_id)
        await _save_local(doc, self.filepath)

    async def save_batch(self, results: list[ScrapeResult]):
        for result in results:
            await self.save(result)

    async def close(self):
        pass


async def _save_local(doc: dict, filepath: str = "results.json"):
    existing = []
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except Exception:
                existing = []
    existing.append(doc)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, default=str, ensure_ascii=False)
