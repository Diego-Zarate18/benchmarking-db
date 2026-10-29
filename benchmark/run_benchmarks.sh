#!/usr/bin/env bash
set -e

RESULTS_CSV="results/benchmark_results.csv"

# Inicializar cabecera del CSV si no existe
if [ ! -f "$RESULTS_CSV" ]; then
    echo "timestamp_epoch,engine,profile,query_id,concurrency,run_number,throughput_qps,latency_p50_ms,latency_p95_ms,latency_p99_ms,cpu_throttled_seconds,max_rss_mb,disk_spill,status" > "$RESULTS_CSV"
fi

# Matriz de perfiles de recursos
declare -A PROFILES
PROFILES=(
    ["PROFILE_A_BASELINE"]="4.0 4096M"
    ["PROFILE_B_MEDIUM"]="2.0 2048M"
    ["PROFILE_C_SEVERE"]="1.0 1024M"
)

for PROFILE in "PROFILE_A_BASELINE" "PROFILE_B_MEDIUM" "PROFILE_C_SEVERE"; do
    PARAMS=(${PROFILES[$PROFILE]})
    CPUS=${PARAMS[0]}
    MEM=${PARAMS[1]}

    echo "=========================================="
    echo " RESTARTING DOCKER WITH PROFILE: $PROFILE ($CPUS CPUs, $MEM RAM)"
    echo "=========================================="

    CPU_LIMIT=$CPUS MEM_LIMIT=$MEM docker compose -f docker/docker-compose.yml up -d --force-recreate
    docker compose -f docker/docker-compose.yml ps

    echo "Waiting for PostgreSQL..."
    until docker exec benchmark_pg pg_isready -U postgres -d benchmark_db > /dev/null 2>&1; do
        sleep 2
    done

    echo "Waiting for MongoDB..."
    until docker exec benchmark_mongo mongosh --quiet --eval "db.adminCommand({ ping: 1 })" > /dev/null 2>&1; do
        sleep 2
    done

    echo "Waiting for Prometheus..."
    until curl --fail --silent http://localhost:9090/-/ready > /dev/null; do
        sleep 2
    done

    for ENGINE in "mongo" "postgres"; do
        for QUERY in "Q1" "Q2"; do
            for CONCURRENCY in 1 10 50; do
                for RUN in 1 2 3; do

                    echo "--> Iteration: $ENGINE | $PROFILE | $QUERY | Conc: $CONCURRENCY | Run: $RUN"

                    # 1. Flush de caché del Host
                    echo "Clearing Host Cache..."
                    sync; sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'

                    # 2. Warmup (60s)
                    echo "Warming up (60s)..."
                    python3 benchmark/runner.py --engine "$ENGINE" --query-id "$QUERY" --profile "$PROFILE" --concurrency "$CONCURRENCY" --run-number "$RUN" --duration 60 > /dev/null

                    # 3. Medición (300s)
                    echo "Measuring (300s)..."
                    python3 benchmark/runner.py --engine "$ENGINE" --query-id "$QUERY" --profile "$PROFILE" --concurrency "$CONCURRENCY" --run-number "$RUN" --duration 300 --output-csv "$RESULTS_CSV"

                    # 4. Cooldown (60s)
                    echo "Cooldown (60s)..."
                    sleep 60
                done
            done
        done
    done
done

echo "Matriz completada exitosamente. Resultados guardados en $RESULTS_CSV"