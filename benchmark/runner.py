#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import numpy as np
import psycopg2
from pymongo import MongoClient

def query_prometheus(metric_query: str) -> float:
    try:
        url = f"http://localhost:9090/api/v1/query?query={urllib.parse.quote(metric_query)}"
        req = urllib.request.urlopen(url, timeout=5)
        res = json.loads(req.read().decode())
        result = res.get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
        print(
            f"Prometheus returned no data for query: {metric_query}",
            file=sys.stderr,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"Prometheus query failed: {error}", file=sys.stderr)
    return 0.0


def docker_container_id(container_name: str) -> str:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.Id}}", container_name],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Could not inspect {container_name}: {error}", file=sys.stderr)
        return ""
    return result.stdout.strip()


def execute_mongo_q1(client):
    db = client["benchmark_db"]
    pipeline = [
        {"$unwind": "$items"},
        {"$match": {"items.price": {"$gt": 50}}},
        {
            "$group": {
                "_id": {"country": "$customer.geo.country", "category": "$items.category"},
                "total_revenue": {"$sum": {"$multiply": ["$items.qty", "$items.price"]}},
                "avg_revenue": {"$avg": {"$multiply": ["$items.qty", "$items.price"]}},
                "count": {"$sum": 1}
            }
        }
    ]
    list(db.orders.aggregate(pipeline))

def execute_mongo_q2(client, allow_disk_use):
    db = client["benchmark_db"]
    pipeline = [
        {"$match": {"payment.status": "completed", "timestamp": {"$gte": "2026-01-01T00:00:00Z"}}},
        {"$sort": {"payment.final_total": -1}},
        {"$limit": 1000},
        {"$project": {"order_id": 1, "customer.id": 1, "payment.final_total": 1}}
    ]
    list(db.orders.aggregate(pipeline, allowDiskUse=allow_disk_use))

def execute_pg_q1(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT data->'customer'->'geo'->>'country', item->>'category',
                   SUM((item->>'qty')::numeric * (item->>'price')::numeric),
                   AVG((item->>'qty')::numeric * (item->>'price')::numeric), COUNT(*)
            FROM orders, jsonb_array_elements(data->'items') AS item
            WHERE (item->>'price')::numeric > 50 GROUP BY 1, 2;
        """)
        cur.fetchall()

def execute_pg_q2(conn, work_mem):
    if not re.fullmatch(r"\d+(?:\.\d+)?\s*(?:kB|MB|GB)", work_mem, re.IGNORECASE):
        raise ValueError("work_mem must use a value such as 4MB, 512MB, or 1GB")

    with conn.cursor() as cur:
        cur.execute(f"SET work_mem = '{work_mem}'")
        cur.execute("""
            SELECT data->>'order_id', data->'customer'->>'id',
                   (data->'payment'->>'final_total')::numeric
            FROM orders
            WHERE data->'payment'->>'status' = 'completed'
              AND (data->>'timestamp')::timestamptz >= '2026-01-01T00:00:00Z'
            ORDER BY (data->'payment'->>'final_total')::numeric DESC LIMIT 1000;
        """)
        cur.fetchall()


def postgres_temp_bytes(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT temp_bytes
            FROM pg_stat_database
            WHERE datname = current_database()
            """
        )
        row = cur.fetchone()
    return int(row[0]) if row else 0


def worker_loop(engine, query_id, duration, allow_disk_use, work_mem, mongo_url, pg_url):
    latencies = []
    spill_observed = False
    spill_measured = engine == "postgres" and query_id == "Q2"
    start_time = time.time()

    client = None
    conn = None
    try:
        if engine == "mongo":
            client = MongoClient(mongo_url, serverSelectionTimeoutMS=5_000)
            client.admin.command("ping")
        else:
            conn = psycopg2.connect(pg_url, connect_timeout=5)

        while time.time() - start_time < duration:
            t0 = time.perf_counter()
            try:
                temp_bytes_before = (
                    postgres_temp_bytes(conn) if spill_measured else 0
                )
                if engine == "mongo":
                    if query_id == "Q1":
                        execute_mongo_q1(client)
                    else:
                        execute_mongo_q2(client, allow_disk_use)
                elif query_id == "Q1":
                    execute_pg_q1(conn)
                else:
                    execute_pg_q2(conn, work_mem)
                if spill_measured:
                    spill_observed = (
                        postgres_temp_bytes(conn) > temp_bytes_before
                    )
                latencies.append((time.perf_counter() - t0) * 1000)
            except Exception as error:
                print(f"{engine} {query_id} failed: {error}", file=sys.stderr)
                latencies.append(-1)
    finally:
        if client is not None:
            client.close()
        if conn is not None:
            conn.close()

    return latencies, spill_observed, spill_measured

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=["mongo", "postgres"])
    parser.add_argument("--query-id", required=True, choices=["Q1", "Q2"])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--run-number", type=int, required=True)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--allow-disk-use", action="store_true")
    parser.add_argument("--work-mem", default="4MB")
    parser.add_argument(
        "--mongo-url",
        default="mongodb://localhost:27017/benchmark_db",
    )
    parser.add_argument(
        "--pg-url",
        default="postgresql://postgres:postgrespassword@localhost:5432/benchmark_db",
    )
    parser.add_argument("--output-csv", default="results/benchmark_results.csv")
    args = parser.parse_args()

    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.duration < 1:
        parser.error("--duration must be at least 1 second")

    container_name = "benchmark_mongo" if args.engine == "mongo" else "benchmark_pg"
    container_id = docker_container_id(container_name)
    if not container_id:
        raise SystemExit(f"Could not resolve Docker container: {container_name}")
    metric_selector = f'id="/docker/{container_id}"'

    print(f"Running {args.engine} | {args.profile} | {args.query_id} | Conc: {args.concurrency} | Run: {args.run_number}...")

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(
                worker_loop, args.engine, args.query_id, args.duration,
                args.allow_disk_use,
                args.work_mem,
                args.mongo_url,
                args.pg_url,
            )
            for _ in range(args.concurrency)
        ]
        results = [f.result() for f in futures]

        flat_latencies = [
            lat for res, _, _ in results for lat in res if lat > 0
        ]
    total_queries = len(flat_latencies)
    throughput = total_queries / args.duration if args.duration > 0 else 0

    p50 = np.percentile(flat_latencies, 50) if flat_latencies else 0
    p95 = np.percentile(flat_latencies, 95) if flat_latencies else 0
    p99 = np.percentile(flat_latencies, 99) if flat_latencies else 0

    # PromQL métricas de telemetría
    throttled = query_prometheus(
        f"container_cpu_cfs_throttled_seconds_total{{{metric_selector}}}"
    )
    max_rss = query_prometheus(
        f"container_memory_rss{{{metric_selector}}}"
    ) / (1024 * 1024)

    status = "ok" if flat_latencies else "failed"
    spill_measured = any(measured for _, _, measured in results)
    spill_observed = any(observed for _, observed, _ in results)
    if not spill_measured:
        spill = "not_measured"
    elif spill_observed:
        spill = "observed"
    else:
        spill = "not_observed"

    epoch = int(datetime.now(timezone.utc).timestamp())

    # Escribir fila usando el esquema exacto
    row = f"{epoch},{args.engine},{args.profile},{args.query_id},{args.concurrency},{args.run_number},{throughput:.2f},{p50:.2f},{p95:.2f},{p99:.2f},{throttled:.2f},{max_rss:.2f},{spill},{status}\n"

    output_directory = os.path.dirname(args.output_csv)
    if output_directory:
        os.makedirs(output_directory, exist_ok=True)
    with open(args.output_csv, "a", encoding="utf-8") as f:
        f.write(row)

if __name__ == "__main__":
    main()