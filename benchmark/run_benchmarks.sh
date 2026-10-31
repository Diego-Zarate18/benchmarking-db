#!/usr/bin/env bash
set -e

RESULTS_CSV="results/benchmark_results.csv"
WARMUP_DURATION="${WARMUP_DURATION:-60}"
BENCH_DURATION="${BENCH_DURATION:-300}"
COOLDOWN_DURATION="${COOLDOWN_DURATION:-60}"
WORK_MEM="${WORK_MEM:-4MB}"
ALLOW_DISK_USE_FLAG="${ALLOW_DISK_USE_FLAG:-}"
RESUME_FROM="${RESUME_FROM:-}"
RESUME_REACHED=0

# Inicializar cabecera del CSV si no existe o esta vacio
if [ ! -f "$RESULTS_CSV" ] || [ ! -s "$RESULTS_CSV" ]; then
    mkdir -p "$(dirname "$RESULTS_CSV")"
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
                    ITERATION_KEY="${PROFILE}|${ENGINE}|${QUERY}|${CONCURRENCY}|${RUN}"
                    if [ -n "$RESUME_FROM" ] && [ "$RESUME_REACHED" -eq 0 ]; then
                        if [ "$ITERATION_KEY" != "$RESUME_FROM" ]; then
                            continue
                        fi
                        RESUME_REACHED=1
                    fi

                    echo "--> Iteration: $ENGINE | $PROFILE | $QUERY | Conc: $CONCURRENCY | Run: $RUN"

                    # 1. Flush de caché del Host
                    echo "Clearing Host Cache..."
                    sync; sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'

                    EXTRA_ARGS=()
                    if [ "$ENGINE" = "postgres" ] && [ "$QUERY" = "Q2" ]; then
                        EXTRA_ARGS+=(--work-mem "$WORK_MEM")
                    fi
                    if [ "$ENGINE" = "mongo" ] && [ "$QUERY" = "Q2" ] && [ -n "$ALLOW_DISK_USE_FLAG" ]; then
                        EXTRA_ARGS+=(--allow-disk-use)
                    fi

                    # 2. Warmup
                    echo "Warming up (${WARMUP_DURATION}s)..."
                    python3 benchmark/runner.py --engine "$ENGINE" --query-id "$QUERY" --profile "$PROFILE" --concurrency "$CONCURRENCY" --run-number "$RUN" --duration "$WARMUP_DURATION" "${EXTRA_ARGS[@]}" --no-output > /dev/null

                    # 3. Medición
                    echo "Measuring (${BENCH_DURATION}s)..."
                    python3 benchmark/runner.py --engine "$ENGINE" --query-id "$QUERY" --profile "$PROFILE" --concurrency "$CONCURRENCY" --run-number "$RUN" --duration "$BENCH_DURATION" "${EXTRA_ARGS[@]}" --output-csv "$RESULTS_CSV"

                    # 4. Cooldown
                    echo "Cooldown (${COOLDOWN_DURATION}s)..."
                    sleep "$COOLDOWN_DURATION"
                done
            done
        done
    done
done

if [ -n "$RESUME_FROM" ] && [ "$RESUME_REACHED" -eq 0 ]; then
    echo "Resume point not found: $RESUME_FROM" >&2
    exit 1
fi

echo "Matriz completada exitosamente. Resultados guardados en $RESULTS_CSV"