#!/usr/bin/env python3
"""Generate and load synthetic order data into MongoDB and PostgreSQL."""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import psycopg2
from psycopg2.extras import execute_values
from pymongo import MongoClient


BATCH_SIZE = 10_000
START_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
END_DATE = datetime(2027, 1, 1, tzinfo=timezone.utc)

COUNTRIES = ("US", "MX", "CA", "ES", "CO", "AR")
TIERS = ("promotional", "regular", "vip")
SEGMENTS = ("consumer", "business", "enterprise")
CATEGORIES = ("electronics", "apparel", "home", "beauty", "sports")
BRANDS = ("Acme", "Northstar", "Globex", "Umbrella", "Starlight")
PAYMENT_METHODS = ("credit_card", "paypal", "crypto")
PAYMENT_STATUSES = ("completed", "failed", "pending")
CHANNELS = ("web", "mobile", "store", "marketplace")
USER_AGENTS = ("Chrome", "Firefox", "Safari", "Edge", "Mobile App")


def random_timestamp(rng: random.Random) -> str:
    """Return a random UTC timestamp in the requested two-year range."""
    seconds = int((END_DATE - START_DATE).total_seconds())
    timestamp = START_DATE + timedelta(seconds=rng.randrange(seconds))
    return timestamp.isoformat().replace("+00:00", "Z")


def random_item(rng: random.Random) -> dict[str, Any]:
    """Build one order item and its percentage-based pricing attributes."""
    return {
        "sku": f"SKU-{rng.randrange(1, 1_000_000):06d}",
        "category": rng.choice(CATEGORIES),
        "brand": rng.choice(BRANDS),
        "qty": rng.randint(1, 10),
        "price": round(rng.uniform(10.0, 500.0), 2),
        "tax_pct": round(rng.uniform(0.0, 21.0), 2),
        "discounts": round(rng.uniform(0.0, 30.0), 2),
    }


def item_total(item: dict[str, Any]) -> float:
    """Calculate an item's total after discount and tax."""
    subtotal = item["qty"] * item["price"]
    discounted = subtotal * (1 - item["discounts"] / 100)
    return discounted * (1 + item["tax_pct"] / 100)


def generate_order(rng: random.Random) -> dict[str, Any]:
    """Generate one order matching the documented JSON schema."""
    items = [random_item(rng) for _ in range(rng.randint(1, 5))]
    country = rng.choice(COUNTRIES)
    order_id = str(uuid.uuid4())
    return {
        "order_id": order_id,
        "timestamp": random_timestamp(rng),
        "customer": {
            "id": str(uuid.uuid4()),
            "tier": rng.choice(TIERS),
            "segment": rng.choice(SEGMENTS),
            "geo": {
                "country": country,
                "state": f"ST-{rng.randint(1, 50):02d}",
                "city": f"City-{rng.randint(1, 500):03d}",
                "zip": f"{rng.randint(0, 99999):05d}",
                "coordinates": [
                    round(rng.uniform(-90.0, 90.0), 6),
                    round(rng.uniform(-180.0, 180.0), 6),
                ],
            },
        },
        "items": items,
        "payment": {
            "method": rng.choice(PAYMENT_METHODS),
            "status": rng.choice(PAYMENT_STATUSES),
            "transaction_id": str(uuid.uuid4()),
            "gateway_latency_ms": rng.randint(20, 2_000),
            "final_total": round(sum(item_total(item) for item in items), 2),
        },
        "metadata": {
            "channel": rng.choice(CHANNELS),
            "user_agent": rng.choice(USER_AGENTS),
            "session_id": str(uuid.uuid4()),
            "flags": {
                "is_returning_customer": rng.choice((True, False)),
                "has_coupon": rng.choice((True, False)),
            },
        },
    }


def batches(count: int, rng: random.Random) -> Iterator[list[dict[str, Any]]]:
    """Yield generated orders in fixed-size batches."""
    remaining = count
    while remaining:
        batch_size = min(BATCH_SIZE, remaining)
        yield [generate_order(rng) for _ in range(batch_size)]
        remaining -= batch_size


def load_data(
    mongo_url: str,
    pg_url: str,
    count: int,
    seed: int | None = None,
) -> None:
    """Generate and insert data into both databases, then create indexes."""
    rng = random.Random(seed)
    mongo_client = MongoClient(mongo_url)
    pg_connection = psycopg2.connect(pg_url)

    try:
        mongo_database = mongo_client.get_default_database("benchmark_db")
        mongo_collection = mongo_database["orders"]

        with pg_connection:
            with pg_connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS orders (
                        id UUID PRIMARY KEY,
                        data JSONB NOT NULL
                    )
                    """
                )

                inserted = 0
                for batch in batches(count, rng):
                    postgres_rows = [
                        (order["order_id"], json.dumps(order))
                        for order in batch
                    ]
                    mongo_collection.insert_many(batch, ordered=False)
                    execute_values(
                        cursor,
                        """
                        INSERT INTO orders (id, data)
                        VALUES %s
                        """,
                        postgres_rows,
                        template="(%s, %s::jsonb)",
                        page_size=BATCH_SIZE,
                    )
                    inserted += len(batch)
                    print(f"Inserted {inserted:,}/{count:,} records", flush=True)

                mongo_collection.create_index("timestamp", name="timestamp_idx")
                mongo_collection.create_index("order_id", name="order_id_idx")
                cursor.execute(
                    """
                    CREATE OR REPLACE FUNCTION json_timestamp_to_timestamptz(value text)
                    RETURNS timestamptz
                    LANGUAGE sql
                    IMMUTABLE
                    STRICT
                    AS $$
                        SELECT value::timestamptz
                    $$
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS orders_timestamp_idx
                    ON orders (
                        json_timestamp_to_timestamptz(data->>'timestamp')
                    )
                    """
                )

    finally:
        pg_connection.close()
        mongo_client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and batch-load synthetic orders."
    )
    parser.add_argument(
        "--mongo-url",
        default="mongodb://localhost:27017/benchmark_db",
        help="MongoDB connection URL",
    )
    parser.add_argument(
        "--pg-url",
        default="postgresql://postgres:123456@localhost:5432/benchmark_db",
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=500_000,
        help="Number of records to generate (default: 500000)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducible data",
    )
    args = parser.parse_args()
    if args.count < 0:
        parser.error("--count must be non-negative")
    return args


def main() -> int:
    args = parse_args()
    try:
        load_data(args.mongo_url, args.pg_url, args.count, args.seed)
    except Exception as error:
        print(f"Data generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())