"""Storage layer — saves scrape results to Azure Cosmos DB or local JSON fallback."""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from app.models import ScrapeResult

load_dotenv()


class CosmosStorage:
    """Save results to Azure Cosmos DB."""

    def __init__(self):
        self.client = None
        self.container = None

    async def connect(self):
        try:
            from azure.cosmos.aio import CosmosClient
            endpoint = os.getenv("COSMOS_ENDPOINT")
            key = os.getenv("COSMOS_KEY")

            if not endpoint or not key:
                raise ValueError("COSMOS_ENDPOINT and COSMOS_KEY must be set")

            self.client = CosmosClient(endpoint, credential=key)
            db = self.client.get_database_client(os.getenv("COSMOS_DATABASE", "logicapps-db"))
            self.container = db.get_container_client(os.getenv("COSMOS_CONTAINER", "job_cache"))
        except Exception as e:
            print(f"[WARN] Cosmos DB connection failed: {e}")
            print("[WARN] Falling back to local JSON storage")
            self.container = None

    async def save(self, result: ScrapeResult):
        doc = result.model_dump()
        doc["id"] = str(uuid.uuid4())
        doc["timestamp"] = datetime.now(timezone.utc).isoformat()
        doc["partitionKey"] = result.firm_name  # Adjust to match your partition strategy

        if self.container:
            await self.container.upsert_item(doc)
        else:
            await self._save_local(doc)

    async def save_batch(self, results: list[ScrapeResult]):
        for result in results:
            await self.save(result)

    async def _save_local(self, doc: dict):
        """Fallback — write to local JSON file."""
        filepath = "results.json"
        existing = []
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                existing = json.load(f)
        existing.append(doc)
        with open(filepath, "w") as f:
            json.dump(existing, f, indent=2, default=str)

    async def close(self):
        if self.client:
            await self.client.close()


class LocalStorage:
    """Simple local JSON storage (no Cosmos DB dependency)."""

    def __init__(self, filepath: str = "results.json"):
        self.filepath = filepath

    async def connect(self):
        pass

    async def save(self, result: ScrapeResult):
        doc = result.model_dump()
        doc["id"] = str(uuid.uuid4())
        doc["timestamp"] = datetime.now(timezone.utc).isoformat()

        existing = []
        if os.path.exists(self.filepath):
            with open(self.filepath, "r") as f:
                existing = json.load(f)
        existing.append(doc)
        with open(self.filepath, "w") as f:
            json.dump(existing, f, indent=2, default=str)

    async def save_batch(self, results: list[ScrapeResult]):
        for result in results:
            await self.save(result)

    async def close(self):
        pass
