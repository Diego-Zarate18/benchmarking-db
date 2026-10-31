#!/usr/bin/env python3
"""Extract execution plans for the benchmark queries."""

import argparse
import json
import os
import sys

import psycopg2
from pymongo import MongoClient


OUTPUT_DIR = "results/explains"

MONGO_Q1 = [
    {"$unwind": "$items"},
    {"$match": {"items.price": {"$gt": 50}}},
    {
        "$group": {
            "_id": {
                "country": "$customer.geo.country",
                "category": "$items.category",
            },
            "total_revenue": {
                "$sum": {"$multiply": ["$items.qty", "$items.price"]}
            },
            "avg_revenue": {
                "$avg": {"$multiply": ["$items.qty", "$items.price"]}
            },
            "count": {"$sum": 1},
        }
    },
]

MONGO_Q2 = [
    {
        "$match": {
            "payment.status": "completed",
            "timestamp": {"$gte": "2026-01-01T00:00:00Z"},
        }
    },
    {"$sort": {"payment.final_total": -1}},
    {"$limit": 1000},
    {
        "$project": {
            "order_id": 1,
            "customer.id": 1,
            "payment.final_total": 1,
        }
    },
]

PG_Q1 = """
EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
SELECT data->'customer'->'geo'->>'country', item->>'category',
       SUM((item->>'qty')::numeric * (item->>'price')::numeric),
       AVG((item->>'qty')::numeric * (item->>'price')::numeric), COUNT(*)
FROM orders, jsonb_array_elements(data->'items') AS item
WHERE (item->>'price')::numeric > 50 GROUP BY 1, 2;
"""

PG_Q2 = """
EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
SELECT data->>'order_id', data->'customer'->>'id',
       (data->'payment'->>'final_total')::numeric
FROM orders
WHERE data->'payment'->>'status' = 'completed'
  AND (data->>'timestamp')::timestamptz >= '2026-01-01T00:00:00Z'
ORDER BY (data->'payment'->>'final_total')::numeric DESC LIMIT 1000;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture database execution plans."
    )
    parser.add_argument(
        "--mongo-url",
        default="mongodb://localhost:27017/benchmark_db",
    )
    parser.add_argument(
        "--pg-url",
        default="postgresql://postgres:123456@localhost:5432/benchmark_db",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("Capturando planes de ejecución...")

    mongo_client = None
    try:
        mongo_client = MongoClient(
            args.mongo_url,
            serverSelectionTimeoutMS=5000,
        )
        mongo_client.admin.command("ping")
        database = mongo_client["benchmark_db"]

        for name, pipeline in (("mongo_q1", MONGO_Q1), ("mongo_q2", MONGO_Q2)):
            plan = database.command(
                "aggregate",
                "orders",
                pipeline=pipeline,
                explain=True,
            )
            path = os.path.join(OUTPUT_DIR, f"{name}_explain.json")
            with open(path, "w", encoding="utf-8") as output:
                json.dump(plan, output, indent=2, default=str)
            print(f"Guardado: {path}")
    except Exception as error:
        print(f"Error extrayendo explain de MongoDB: {error}", file=sys.stderr)
        return 1
    finally:
        if mongo_client is not None:
            mongo_client.close()

    pg_conn = None
    try:
        pg_conn = psycopg2.connect(args.pg_url, connect_timeout=5)
        with pg_conn.cursor() as cursor:
            cursor.execute(PG_Q1)
            rows = cursor.fetchall()
            with open(
                os.path.join(OUTPUT_DIR, "pg_q1_explain.txt"),
                "w",
                encoding="utf-8",
            ) as output:
                output.write("\n".join(row[0] for row in rows))

            cursor.execute("SET work_mem = '4MB'")
            cursor.execute(PG_Q2)
            rows = cursor.fetchall()
            with open(
                os.path.join(OUTPUT_DIR, "pg_q2_explain_4mb.txt"),
                "w",
                encoding="utf-8",
            ) as output:
                output.write("\n".join(row[0] for row in rows))
        print(f"Guardado: {OUTPUT_DIR}/pg_q1_explain.txt")
        print(f"Guardado: {OUTPUT_DIR}/pg_q2_explain_4mb.txt")
    except Exception as error:
        print(
            f"Error extrayendo explain de PostgreSQL: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        if pg_conn is not None:
            pg_conn.close()

    print(f"\nPlanes de ejecución guardados en {OUTPUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
