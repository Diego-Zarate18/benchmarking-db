# Reporte de Métricas: MongoDB vs PostgreSQL JSONB bajo Contención
**Ingeniería en Tecnologías de la Información e Innovación Digital (IT2iD) - 2026**  
**Materia:** Bases de Datos en la Nube  
**Autores:** [Nombres del equipo]  
**Fecha:** [Fecha de entrega]

---

## 1. Especificaciones del Host y Entorno Docker
*Detallar las características del entorno físico y virtualizado donde se ejecutó el estudio para garantizar reproducibilidad.*

- **CPU Host:** [Ej. Intel Core i7-12700H / AMD Ryzen 7 5800H - Núcleos / Hilos]
- **Memoria RAM Host:** [Ej. 16 GB DDR4 / 32 GB DDR5]
- **Almacenamiento:** [Ej. NVMe SSD PCIe 4.0]
- **Sistema Operativo / Kernel:** [Ej. Ubuntu 22.04 LTS sobre WSL2 / Kernel 5.15.x]
- **Versión de Docker Engine & Compose:** [Ej. Docker 26.x, Docker Compose v2.x]
- **Versión del Engine cgroups:** cgroups v2 (`/sys/fs/cgroup`)
- **Versión exacta de los motores:**
  - **MongoDB:** `mongo:latest` [Anotar versión exacta: `mongosh --version`]
  - **PostgreSQL:** `postgres:latest` [Anotar versión exacta: `postgres --version`]
- **Stack de Telemetría:** cAdvisor (`latest`), Node Exporter (`latest`), Prometheus (`latest`, scrape interval 1s)

---

## 2. Método de Medición y Verificación del Baseline
*Describir el protocolo seguido y confirmar la paridad de condiciones entre ambos motores.*

### Verificación del Dataset (500,000 registros)
- **Generador:** `dataset/synthetic_data_generator.py` (Semilla: `--seed 42`).
- **Validación de conteos en frío:**
  - PostgreSQL: `SELECT COUNT(*) FROM orders;` $\rightarrow$ `500,000`
  - MongoDB: `db.orders.countDocuments()` $\rightarrow$ `500,000`
- **Índices verificados:**
  - PostgreSQL: `orders_pkey` (UUID), `orders_timestamp_idx` (timestamp B-Tree).
  - MongoDB: `_id_`, `timestamp_idx`, `order_id_idx`.

### Ciclo de Vida de cada Celda
Para cada una de las 36 celdas de la matriz (2 motores $\times$ 3 perfiles $\times$ 2 queries $\times$ 3 concurrencias):
1. **Flush de caché:** `sync; echo 3 > /proc/sys/vm/drop_caches` en el host antes de cada iteración.
2. **Warmup:** 60 segundos de consultas continuas sin recolectar métricas.
3. **Medición activa:** 300 segundos continuos con recolección de percentiles de latencia, QPS, throttling CFS y memoria RSS.
4. **Cooldown:** 60 segundos de reposo para estabilizar buffers del motor y cgroups.
5. **Repeticiones:** 3 ejecuciones independientes por celda (resumidas mediante mediana de latencias y media de throughput).

---

## 3. Resultados Consolidados
*Tablas resumen extraídas de `results/benchmark_results.csv` respondiendo a Throughput, P50, P95, P99 y tiempo de Throttling.*

### Resumen Q1: Unwind, Filter & Grouping (Saturación CPU)
| Motor | Perfil | Concurrencia | Throughput (QPS) | P50 (ms) | P95 (ms) | P99 (ms) | Throttled (s) | Max RSS (MB) | Estado |
|---|---|---|---|---|---|---|---|---|---|
| PostgreSQL | Baseline (4 CPU, 4G) | 1 | | | | | | | ok |
| PostgreSQL | Baseline (4 CPU, 4G) | 10 | | | | | | | ok |
| PostgreSQL | Baseline (4 CPU, 4G) | 50 | | | | | | | ok |
| PostgreSQL | Medium (2 CPU, 2G) | 1 | | | | | | | ok |
| PostgreSQL | Medium (2 CPU, 2G) | 10 | | | | | | | ok |
| PostgreSQL | Medium (2 CPU, 2G) | 50 | | | | | | | ok |
| PostgreSQL | Severe (1 CPU, 1G) | 1 | | | | | | | ok |
| PostgreSQL | Severe (1 CPU, 1G) | 10 | | | | | | | ok |
| PostgreSQL | Severe (1 CPU, 1G) | 50 | | | | | | | ok |
| MongoDB | Baseline (4 CPU, 4G) | 1 | | | | | | | ok |
| MongoDB | Baseline (4 CPU, 4G) | 10 | | | | | | | ok |
| MongoDB | Baseline (4 CPU, 4G) | 50 | | | | | | | ok |
| MongoDB | Medium (2 CPU, 2G) | 1 | | | | | | | ok |
| MongoDB | Medium (2 CPU, 2G) | 10 | | | | | | | ok |
| MongoDB | Medium (2 CPU, 2G) | 50 | | | | | | | ok |
| MongoDB | Severe (1 CPU, 1G) | 1 | | | | | | | ok |
| MongoDB | Severe (1 CPU, 1G) | 10 | | | | | | | ok |
| MongoDB | Severe (1 CPU, 1G) | 50 | | | | | | | ok |

### Resumen Q2: Massive Sort & Top-K (Estrés de Buffer)
| Motor | Perfil | Concurrencia | Config Memoria | QPS | P50 (ms) | P95 (ms) | P99 (ms) | Spill a Disco | Estado |
|---|---|---|---|---|---|---|---|---|---|
| PostgreSQL | Baseline | 10 | `work_mem=4MB` | | | | | observed / not_observed | |
| PostgreSQL | Severe | 50 | `work_mem=4MB` | | | | | observed / not_observed | |
| PostgreSQL | Severe | 50 | `work_mem=64MB` | | | | | observed / not_observed | |
| MongoDB | Baseline | 10 | `allowDiskUse=false` | | | | | not_measured | |
| MongoDB | Severe | 50 | `allowDiskUse=false` | | | | | not_measured / fallo | |
| MongoDB | Severe | 50 | `allowDiskUse=true` | | | | | not_measured / ok | |

---

## 4. Análisis Gráfico
*(Gráficas generadas automáticamente mediante `python3 benchmark/plot_results.py`)*

### 4.1. Latencia P99 frente a Concurrencia
![P99 vs Concurrencia Q1](results/plots/p99_vs_concurrency_Q1.png)
![P99 vs Concurrencia Q2](results/plots/p99_vs_concurrency_Q2.png)
*Análisis de la degradación de latencia de cola (P99) conforme aumenta la contención de conexiones concurrentes.*

### 4.2. Throttling CFS frente a Rendimiento (QPS)
![Throttling vs QPS](results/plots/throttling_vs_qps.png)
*Impacto directo de la penalización de CPU (CFS quota) sobre el throughput en los perfiles Medium y Severe.*

### 4.3. Comparativa de Huella de Memoria (Max RSS)
![Max RSS Comparison](results/plots/max_rss_comparison.png)
*Comportamiento del Working Set y consumo de buffers de cada motor bajo los límites estrictos de memoria (1024 MB vs 4096 MB).*

---

## 5. Planes de Ejecución
*Comparativa técnica entre `explain executionStats` de MongoDB y `EXPLAIN (ANALYZE, BUFFERS)` de PostgreSQL (evidencias en `results/explains/`).*

### Q1: Agregación con Unwind vs JSONB Array Expansion
- **PostgreSQL (`EXPLAIN (ANALYZE, BUFFERS)`):**
  - Operadores clave: `Function Scan on jsonb_array_elements`, `HashAggregate` o `GroupAggregate`.
  - Buffers compartidos leídos (`shared hit/read`) y costo de serialización/deserialización JSONB.
- **MongoDB (`explain("executionStats")`):**
  - Stages clave: `COLLSCAN` / `FETCH`, `$unwind`, `$match`, `$group`.
  - Consumo de memoria en el stage `$group` y documentos examinados (`docsExamined` vs `nReturned`).

### Q2: Ordenamiento Masivo Top-1000
- **PostgreSQL:**
  - Método de ordenamiento: `Sort Method: top-N heapsort` (en memoria si cabe en `work_mem`) o `external merge` (spill a disco en temporales si excede `work_mem`).
- **MongoDB:**
  - Stage `SORT` con `limitAmount: 1000`.
  - Comportamiento ante el límite de 100 MB de RAM en sort en memoria (`usedDisk: true/false`).

---

## 6. Interpretación: Límites, Spill y Throttling de cgroups
*Respuestas fundamentadas con datos a las preguntas guía M1, M2 y M3:*

- **M1: ¿Cómo cambian P95 y P99 al limitar CPU y memoria?**  
  [Explicar con datos de la tabla cómo el paso de 4 CPU a 1 CPU aumenta la dispersión entre P50 y P99 debido al encolamiento de hilos y throttling de CFS].

- **M2: ¿Qué motor gestiona mejor los buffers antes de spill o OOM?**  
  [Analizar qué motor resistió mejor la concurrencia 50 en el Perfil C (1 GB RAM). Comparar el comportamiento de PostgreSQL (`work_mem` por nodo de ordenamiento) vs el buffer pool de WiredTiger].

- **M3: ¿Cuál es la huella de CPU y memoria por cada 1,000 documentos procesados?**  
  [Calcular `Max RSS / (Throughput * 300)` o CPU total consumida por millón de documentos procesados].

---

## 7. Recomendaciones Técnicas para Despliegues en la Nube
*Directrices para arquitectura de datos basadas en los hallazgos experimentales:*

1. **Cuándo utilizar MongoDB:**
   - [Recomendación basada en consultas agregadas anidadas, pipelines de desanidado y optimizaciones nativas].
2. **Cuándo utilizar PostgreSQL JSONB:**
   - [Recomendación basada en consistencia ACID, transaccionalidad combinada con datos relacionales y control granular de `work_mem`].
3. **Dimensionamiento de Recursos y cgroups:**
   - [Impacto del CPU limit en Kubernetes/Docker y ajuste del buffer pool de WiredTiger o `shared_buffers` para evitar OOM-kills].

