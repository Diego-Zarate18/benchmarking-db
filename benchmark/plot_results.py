#!/usr/bin/env python3
"""Generates benchmark visualization plots for the IT2iD report."""

import os
import sys
import pandas as pd
import matplotlib.pyplot as plt

RESULTS_CSV = "results/benchmark_results.csv"
OUTPUT_DIR = "results/plots"

def main():
    if not os.path.exists(RESULTS_CSV) or os.path.getsize(RESULTS_CSV) == 0:
        print(f"Error: {RESULTS_CSV} no existe o está vacío.", file=sys.stderr)
        return 1

    df = pd.read_csv(RESULTS_CSV)
    if df.empty or len(df) == 0:
        print("El archivo CSV no contiene registros para graficar.", file=sys.stderr)
        return 1

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    plt.style.use('ggplot')

    # Filtrar solo ejecuciones con estatus ok o spill
    df_valid = df[df['status'].isin(['ok', 'spill'])]
    if df_valid.empty:
        df_valid = df

    # Agrupar repeticiones por mediana/media
    grouped = df_valid.groupby(['engine', 'profile', 'query_id', 'concurrency']).agg({
        'throughput_qps': 'mean',
        'latency_p50_ms': 'median',
        'latency_p95_ms': 'median',
        'latency_p99_ms': 'median',
        'cpu_throttled_seconds': 'mean',
        'max_rss_mb': 'max'
    }).reset_index()

    # 1. Gráfica: P99 vs Concurrencia por Query y Perfil
    for q_id in grouped['query_id'].unique():
        plt.figure(figsize=(10, 6))
        sub = grouped[grouped['query_id'] == q_id]
        for (engine, profile), group in sub.groupby(['engine', 'profile']):
            plt.plot(group['concurrency'], group['latency_p99_ms'], marker='o', label=f"{engine} - {profile}")
        plt.title(f"Latencia P99 vs Concurrencia ({q_id})")
        plt.xlabel("Concurrencia (clientes)")
        plt.ylabel("Latencia P99 (ms)")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()
        out_file = os.path.join(OUTPUT_DIR, f"p99_vs_concurrency_{q_id}.png")
        plt.savefig(out_file)
        plt.close()
        print(f"Generado: {out_file}")

    # 2. Gráfica: Throttling CFS vs Throughput (QPS)
    plt.figure(figsize=(10, 6))
    for engine, group in grouped.groupby('engine'):
        plt.scatter(group['throughput_qps'], group['cpu_throttled_seconds'], label=engine, s=80, alpha=0.8)
    plt.title("Tiempo de Throttling CFS vs Throughput (QPS)")
    plt.xlabel("Throughput (QPS)")
    plt.ylabel("Throttling de CPU (segundos)")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    out_file = os.path.join(OUTPUT_DIR, "throttling_vs_qps.png")
    plt.savefig(out_file)
    plt.close()
    print(f"Generado: {out_file}")

    # 3. Gráfica: Huella de memoria Max RSS
    plt.figure(figsize=(10, 6))
    rss_pivot = grouped.groupby(['profile', 'engine'])['max_rss_mb'].mean().unstack()
    rss_pivot.plot(kind='bar', figsize=(10, 6), rot=0)
    plt.title("Pico Máximo de Memoria RSS por Perfil y Motor")
    plt.xlabel("Perfil de Recursos")
    plt.ylabel("Max RSS (MB)")
    plt.grid(True, axis='y')
    plt.tight_layout()
    out_file = os.path.join(OUTPUT_DIR, "max_rss_comparison.png")
    plt.savefig(out_file)
    plt.close()
    print(f"Generado: {out_file}")

    print(f"\n¡Todas las gráficas fueron guardadas exitosamente en {OUTPUT_DIR}/!")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

