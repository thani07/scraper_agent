"""Storage layer — saves scrape results to Azure Cosmos DB or local JSON fallback."""

import os
import json
import uuid
import hashlib
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.models import ScrapeResult

load_dotenv()


# ── New-architecture helpers ───────────────────────────────────────────────────

def _generate_crawl_id(role: str) -> str:
    """Generate a deterministic crawl ID from role name: crawl_<md5hex>."""
    h = hashlib.md5(role.lower().strip().encode()).hexdigest()
    return f"crawl_{h}"


def _generate_cache_key() -> str:
    """Generate a short cache key: cache_<12 hex chars>."""
    return f"cache_{uuid.uuid4().hex[:12]}"


def _build_crawl_job(result: ScrapeResult, role: str) -> dict:
    """Convert a successful ScrapeResult into a job entry for the crawl doc.

    job_role is always the parent role name (lowercase), not the specific
    similar_role keyword that matched — so all jobs under one crawl doc
    share the same job_role value.
    """
    e = result.extraction
    return {
        "id":             f"job_{uuid.uuid4().hex[:20]}",
        "job_role":       role.lower().strip(),
        "firm":           result.firm_name,
        "title":          e.role_title if e else None,
        "location":       e.location if e else None,
        "salary":         None,
        "salary_raw":     (e.salary_raw or "N/A") if e else "N/A",
        "salary_period":  "yearly",
        "salary_min":     e.salary_min if e else None,
        "salary_max":     e.salary_max if e else None,
        "url":            e.job_url if e else None,
        "cached_at":      datetime.now(timezone.utc).isoformat(),
    }


def _build_document(result: ScrapeResult, run_id: str) -> dict:
    """
    Flatten a ScrapeResult into a single Cosmos DB document.

    Partition key: /role_title — the actual job title fetched from the website
                                 (e.g. "Litigation Associate", "Paralegal, Real Estate").
                                 For no_results/error records (no extraction), falls back
                                 to the searched_role so the field is never null.

    Document shape:
        id              — unique UUID per document
        role_title      — partition key — actual job title from the website
        searched_role   — the role keyword used to find this job (e.g. "paralegal")
        run_id          — UUID shared by all documents in one run
        scraped_at      — ISO-8601 UTC timestamp
        firm_name
        strategy_used
        status          — success | no_results | error
        error_message   — only present on error
        scrape_duration_sec
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

    # role_title is the partition key — Cosmos DB requires it to be non-null.
    # For success records: use the actual extracted job title from the website.
    # For no_results / error: no extraction exists, so fall back to the searched role.
    role_title = (e.role_title if e and e.role_title else result.role_searched)

    doc: dict = {
        "id":                   str(uuid.uuid4()),
        "role_title":           role_title,               # partition key — actual job title
        "searched_role":        result.role_searched,     # keyword used to search (e.g. "paralegal")
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
        Database  : set via COSMOS_DATABASE  (e.g. "salary-intelligence-uat")
        Container : set via COSMOS_CONTAINER  (e.g. "agent_job_results_v2")
        Partition key path: /role_title

    Each unique job title lands in its own logical partition.
    Use searched_role field to query all jobs found for a given search keyword.
    """

    def __init__(self):
        self.client    = None
        self.container = None
        self.run_id    = str(uuid.uuid4())   # shared across all saves in one run

    @property
    def cosmos_connected(self) -> bool:
        """True only when Cosmos DB is reachable and the container is ready."""
        return self.container is not None

    async def connect(self):
        """
        Connect to Cosmos DB and verify the connection with a lightweight read.

        Raises RuntimeError if connection or verification fails — this is intentional.
        Silent fallback to local disk caused data loss in Azure (ephemeral filesystem).
        The caller (run_scraper) must handle this and abort if STORAGE=cosmos was requested.
        """
        from azure.cosmos.aio import CosmosClient
        from azure.cosmos import PartitionKey

        endpoint = os.getenv("COSMOS_ENDPOINT")
        key      = os.getenv("COSMOS_KEY")

        if not endpoint or not key:
            raise RuntimeError(
                "COSMOS_ENDPOINT and COSMOS_KEY must both be set in environment / App Settings."
            )

        # COSMOS_KEY may be a full connection string
        # (e.g. "AccountEndpoint=...;AccountKey=abc123==;")
        # or just the bare account key (e.g. "abc123==").
        if key.startswith("AccountEndpoint=") or "AccountKey=" in key:
            for part in key.split(";"):
                if part.startswith("AccountKey="):
                    key = part[len("AccountKey="):]
                    break

        db_name        = os.getenv("COSMOS_DATABASE",  "hr-scraper")
        container_name = os.getenv("COSMOS_CONTAINER", "job-results")

        self.client = CosmosClient(endpoint, credential=key)

        # Auto-create database and container if they don't exist yet.
        # Partition key /role_title stores the actual job title from the website.
        db = await self.client.create_database_if_not_exists(id=db_name)
        self.container = await db.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path="/role_title"),
        )

        # Verify connection with a lightweight read (query metadata).
        # This catches auth errors that only surface on the first real request.
        try:
            props = await self.container.read()
            _ = props  # just confirming it returns without error
        except Exception as verify_err:
            self.container = None
            raise RuntimeError(
                f"Cosmos DB container read verification failed: {verify_err}"
            ) from verify_err

        print(f"  Cosmos DB connected and verified: {db_name} / {container_name}")

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


# ── New-architecture Cosmos storage (analyses → agent_job_results) ─────────────

class CrawlStorage:
    """
    New-architecture storage for the analyses-driven crawl flow.

    Source  : 'analyses' container — role docs with similar_roles[]
    Dest    : 'agent_job_results' container — nested crawl docs per role

    Each crawl doc shape:
        {
            "id":          "crawl_<md5>",
            "role":        "role name",          ← partition key
            "job_count":   N,
            "jobs":        [...],
            "crawled_at":  "ISO-8601 UTC"
        }

    After saving a crawl doc, update the analyses doc:
        cache_id   = crawl_id
        is_crawled = true
    """

    def __init__(self):
        self.client               = None
        self.analyses_container   = None
        self.results_container    = None

    async def connect(self):
        """Connect to Cosmos DB and open both containers."""
        from azure.cosmos.aio import CosmosClient
        from azure.cosmos import PartitionKey

        endpoint = os.getenv("COSMOS_ENDPOINT")
        key      = os.getenv("COSMOS_KEY")

        if not endpoint or not key:
            raise RuntimeError(
                "COSMOS_ENDPOINT and COSMOS_KEY must both be set in environment / App Settings."
            )

        if key.startswith("AccountEndpoint=") or "AccountKey=" in key:
            for part in key.split(";"):
                if part.startswith("AccountKey="):
                    key = part[len("AccountKey="):]
                    break

        db_name         = os.getenv("COSMOS_DATABASE",            "salary-intelligence-uat")
        analyses_name   = os.getenv("COSMOS_ANALYSES_CONTAINER",  "analyses")
        results_name    = os.getenv("COSMOS_RESULTS_CONTAINER",   "agent_job_results")

        self.client = CosmosClient(endpoint, credential=key)
        db = await self.client.create_database_if_not_exists(id=db_name)

        # analyses — already exists, just get the client (no partition key needed)
        self.analyses_container = db.get_container_client(analyses_name)

        # agent_job_results — create if not exists, partition key /role
        self.results_container = await db.create_container_if_not_exists(
            id=results_name,
            partition_key=PartitionKey(path="/role"),
        )

        # Verify both containers are reachable
        try:
            await self.analyses_container.read()
            await self.results_container.read()
        except Exception as e:
            raise RuntimeError(f"Cosmos DB container verification failed: {e}") from e

        print(f"  Cosmos DB connected: {db_name} / {analyses_name} + {results_name}")

    async def read_analyses(self) -> list[dict]:
        """Read ALL role documents from the analyses container.

        Full documents are cached in memory by id so update_analysis()
        can upsert them back without needing to know the partition key path.
        """
        query = "SELECT * FROM c"
        items: list[dict] = []
        async for item in self.analyses_container.query_items(query=query):
            items.append(item)
        # Cache by id for O(1) lookup during updates
        self._analyses_cache: dict[str, dict] = {doc["id"]: doc for doc in items}
        print(f"  Loaded {len(items)} role doc(s) from analyses container.")
        return items

    async def save_crawl_result(self, crawl_id: str, role: str, jobs: list[dict]) -> None:
        """Upsert a crawl result document into agent_job_results."""
        doc = {
            "id":          crawl_id,
            "crawled_by":  "ai_agent",
            "cache_key":   _generate_cache_key(),
            "role":        role.lower().strip(),
            "city":        "",
            "cached_at":   datetime.now(timezone.utc).isoformat(),
            "job_count":   len(jobs),
            "jobs":        jobs,
        }
        await self.results_container.upsert_item(doc)
        print(f"  [DB] Saved crawl doc {crawl_id}  ({len(jobs)} jobs)  role='{role}'")

    async def update_analysis(self, doc_id: str, crawl_id: str) -> None:
        """Update analyses doc by upserting the modified in-memory copy.

        Uses upsert instead of patch so we don't need to know the container's
        partition key path — Cosmos extracts it automatically from the document body.
        """
        doc = self._analyses_cache.get(doc_id) if hasattr(self, "_analyses_cache") else None
        if not doc:
            print(f"  [DB][WARN] analyses doc {doc_id} not in memory cache, skipping update.")
            return
        doc["cache_id"]   = crawl_id
        doc["is_crawled"] = "agent"
        try:
            await self.analyses_container.upsert_item(doc)
            print(f"  [DB] Updated analyses doc {doc_id}: is_crawled='agent', cache_id={crawl_id}")
        except Exception as e:
            print(f"  [DB][WARN] Failed to upsert analyses doc {doc_id}: {e}")

    async def close(self):
        if self.client:
            await self.client.close()
