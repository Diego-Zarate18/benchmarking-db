# Benchmark MongoDB vs PostgreSQL

Este proyecto compara consultas sobre 500,000 ordenes almacenadas en MongoDB y PostgreSQL JSONB bajo tres perfiles de recursos.

## Requisitos

- Linux/WSL2 con Docker Compose
- Python 3.10 o posterior
- `curl` y `sudo` (el script limpia la cache del host)

Instala las dependencias desde la raiz del repositorio:

```bash
python3 -m pip install -r requirements.txt
```

## 1. Iniciar las bases

```bash
docker compose -f docker/docker-compose.yml up -d postgres mongodb
docker compose -f docker/docker-compose.yml ps
```

Comprueba que acepten conexiones:

```bash
docker exec benchmark_pg pg_isready -U postgres -d benchmark_db
docker exec benchmark_mongo mongosh --quiet --eval "db.adminCommand({ ping: 1 })"
```

## 2. Cargar el dataset

El generador crea la tabla `orders`, inserta en lotes de 10,000, crea los indices y muestra el progreso:

```bash
python3 dataset/synthetic_data_generator.py --count 500000
```

Para una carga reproducible se puede anadir, por ejemplo, `--seed 42`. Las URLs pueden sobrescribirse con `--mongo-url` y `--pg-url`.

Valida los conteos:

```bash
docker exec benchmark_pg psql -U postgres -d benchmark_db -c "SELECT COUNT(*) FROM orders;"
docker exec benchmark_mongo mongosh benchmark_db --quiet --eval "db.orders.countDocuments()"
```

Ambos comandos deben devolver `500000`.

Valida los indices:

```bash
docker exec benchmark_pg psql -U postgres -d benchmark_db -c "\di orders*"
docker exec benchmark_mongo mongosh benchmark_db --quiet --eval "db.orders.getIndexes()"
```

## 3. Ejecutar una prueba corta

Levanta todos los servicios de observabilidad:

```bash
docker compose -f docker/docker-compose.yml up -d
```

Ejecuta una prueba de cada motor antes de la matriz completa:

```bash
python3 benchmark/runner.py --engine postgres --query-id Q2 --profile TEST --concurrency 1 --run-number 1 --duration 10
python3 benchmark/runner.py --engine mongo --query-id Q2 --profile TEST --concurrency 1 --run-number 1 --duration 10
```

Cada ejecucion agrega una fila a [results/benchmark_results.csv](results/benchmark_results.csv).

## 4. Ejecutar la matriz completa

Desde la raiz:

```bash
bash benchmark/run_benchmarks.sh
```

La matriz contiene 3 perfiles, 2 motores, 2 consultas, 3 niveles de concurrencia y 3 repeticiones. Cada combinacion usa 60 segundos de warmup, 300 segundos de medicion y 60 segundos de cooldown.

El script recrea los contenedores con los limites del perfil y espera a PostgreSQL, MongoDB y Prometheus antes de comenzar. El usuario debe poder ejecutar `sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'`.

## Interpretacion de `disk_spill`

La columna `disk_spill` no asume spill por usar un limite pequeno:

- `observed`: en PostgreSQL Q2 aumento `pg_stat_database.temp_bytes` durante al menos una consulta. Es evidencia de uso de archivos temporales, aunque la metrica es global a la base y puede incluir actividad concurrente.
- `not_observed`: PostgreSQL Q2 fue medido y no aumento `temp_bytes`.
- `not_measured`: MongoDB, Q1 o cualquier consulta que no sea PostgreSQL Q2.

`--allow-disk-use` solo permite a MongoDB usar disco; no prueba que haya ocurrido spill. `work_mem=4MB` aumenta la probabilidad de spill en PostgreSQL, pero no lo demuestra.

## 5. Planes de ejecucion y graficas

Despues de cargar el dataset, puedes guardar los planes de ejecucion:

```bash
python3 benchmark/explain_queries.py
```

Los archivos se guardan en `results/explains/`. Para generar las graficas del CSV:

```bash
python3 benchmark/plot_results.py
```

Las imagenes se guardan en `results/plots/`.

Si `results/benchmark_results.csv` contiene pruebas anteriores, respaldalo o eliminalo antes de ejecutar una nueva matriz para no mezclar experimentos:

```bash
cp results/benchmark_results.csv results/benchmark_results.previous.csv
rm results/benchmark_results.csv
```

## Consultas y resultados

Las consultas ejecutadas por el runner estan definidas en [benchmark/runner.py](benchmark/runner.py), y las versiones de referencia estan en [queries/](queries/). Los resultados se guardan en [results/benchmark_results.csv](results/benchmark_results.csv).

El CSV incluye throughput, percentiles p50/p95/p99, throttling de CPU, RSS maximo, estado de spill y estado de la ejecucion.
