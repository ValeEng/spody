/*
 * Copyright 2026 ValeEng
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
/*
 * Propagation driver: integrates an already-set-up Simulation forward
 * by cfg->duration_s, writing one (or both) of the requested output
 * files along the way.
 *
 * Sampling behaviour
 *   - SPODY_OUT_FIXED: emit at t = 0, dt, 2*dt, ... ; the trailing
 *     duration_s is always emitted exactly (even when it is not a
 *     multiple of dt), so the output always contains the endpoint.
 *     States between integrator-accepted steps are evaluated via
 *     spody_dense_eval.
 *   - SPODY_OUT_STEP : emit one record per accepted integrator step
 *     plus the initial state at t = 0.
 *
 * Output formats
 *   CSV : a header line followed by `%.15e`-formatted comma-separated
 *         values per record (t, x, y, z, vx, vy, vz).
 *   BIN : a 24-byte header (magic 'SPDYOUT_', format_version, state_dim,
 *         then 8 bytes reserved) followed by raw doubles
 *         (t, x, y, z, vx, vy, vz) per record. No record count up
 *         front -- derive it from the file size.
 */
#ifndef SPODY_SIM_RUN_H
#define SPODY_SIM_RUN_H

#include <stdio.h>
#include <stdint.h>

#include "app_diagnostics.h"
#include "sim_setup.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Aggregated events sink for batch runs.
 *
 * cmd_batch opens ONE file `<batch_subdir>/<batch_name>_events.bin` with
 * magic 'SPDYEVTB' (v1, BatchEventRecord = 88 bytes) and passes a
 * BatchEventSink to spody_run_simulation for every case. Each event fired
 * inside that case is then appended as a BatchEventRecord to the shared
 * file under an OpenMP critical, with `case_idx` set to the row index of
 * the case in cases_file (0-based, matches the order in cases.csv).
 *
 * Single-propagate runs pass `batch_sink = NULL` and the legacy per-run
 * EventRecord file (magic 'SPDYEVT_') driven by cfg->events_log keeps
 * working unchanged. The two paths are independent: in batch mode the
 * caller MUST blank cfg_i->events_log so spody.exe does not open a
 * per-case file alongside the aggregated one. */
typedef struct {
    FILE   *fp;          /* shared by all threads; header already written */
    int32_t case_idx;    /* 0-based; per-thread (set before each spody_run_simulation call) */
} BatchEventSink;

int spody_run_simulation(const InputConfig *cfg, SimulationWorker *w,
                          BatchEventSink *batch_sink,
                          SpodyError *err);

/* Open + initialise the aggregated events file at `path`. Writes the
 * SPDYEVTB header (magic + version + record_size). Returns 0 on success,
 * -1 on fopen / fwrite failure; on failure *out_fp is set to NULL. */
int spody_open_batch_events(const char *path, FILE **out_fp);

#ifdef __cplusplus
}
#endif

#endif /* SPODY_SIM_RUN_H */
