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
 * SpOdy - command-line driver.
 *
 * Subcommand dispatch. Each subcommand reads a TOML input file describing
 * one simulation (or batch of simulations) and writes results to a directory.
 * The Python GUI under python/ generates the input TOML and parses the
 * output files; it never calls into spody-core directly.
 *
 * Subcommands:
 *   spody propagate  <input.toml> [--out <dir>]
 *   spody batch      <input.toml> [--out <dir>]
 *   spody validate   <input.toml>
 *   spody convert    ephemeris <dir> <de> <date1> [date2 ...]
 *   spody convert    harmonics_icgem <input.gfc> <output.tab> [--max-degree N]
 *   spody info
 */
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
  #include <direct.h>
  #define spody_chdir _chdir
#else
  #include <unistd.h>
  #define spody_chdir chdir
#endif

#ifdef SPODY_HAVE_OPENMP
  #include <omp.h>
#endif

#include "spody_core.h"
#include "app_diagnostics.h"
#include "app_io.h"
#include "toml_input.h"
#include "sim_setup.h"
#include "sim_run.h"

/* Monotonic wall-clock seconds. Resolution + thread-safety: with
 * OpenMP linked in we get omp_get_wtime, the only portable wall-time
 * primitive that is safe to call from inside a parallel region.
 * Without OpenMP we fall back to clock() (CPU seconds, equivalent to
 * wall on a single-threaded run); that path is only hit for builds
 * that explicitly opt out via -DSPODY_WITH_OPENMP=OFF. */
static double now_seconds(void) {
#ifdef SPODY_HAVE_OPENMP
    return omp_get_wtime();
#else
    return (double)clock() / (double)CLOCKS_PER_SEC;
#endif
}

#define SPODY_APP_VERSION "0.1.2-beta"

/* One-screen summary of a parsed config, printed after a successful validate.
 * Useful as a sanity check that the file says what the user thinks it says. */
static void print_config_summary(const InputConfig *cfg) {
    spody_log_printf("OK\n");
    spody_log_printf("  simulation       : %s\n", cfg->sim_name);
    spody_log_printf("  et_start         : %.6e s past J2000\n", cfg->et_start_s);
    spody_log_printf("  duration         : %.3e s  (%.3f days)\n",
           cfg->duration_s, cfg->duration_s / 86400.0);
    spody_log_printf("  central body     : %s\n",
           spody_central_body_name(cfg->central_body));
    spody_log_printf("  harmonics file   : %s  (N=%d)\n",
           cfg->harmonics_file, cfg->harmonics_degree);
    spody_log_printf("  ephemeris file   : %s\n", cfg->ephemeris_file);
    spody_log_printf("  third bodies     : ");
    if (cfg->n_third_bodies == 0) {
        spody_log_printf("(none)\n");
    } else {
        for (int i = 0; i < cfg->n_third_bodies; ++i) {
            spody_log_printf("%s%s", cfg->third_body_names[i],
                   (i + 1 < cfg->n_third_bodies) ? ", " : "\n");
        }
    }
    spody_log_printf("  SRP              : %s",
           cfg->enable_srp ? "enabled" : "disabled");
    if (cfg->enable_srp) {
        if (cfg->debris_mode) {
            spody_log_printf("  (A/m=%.6f m^2/kg, Cr=%.3f)",
                   cfg->srp_area_m2, cfg->srp_cr);
        } else {
            spody_log_printf("  (A=%.3f m^2, Cr=%.3f)",
                   cfg->srp_area_m2, cfg->srp_cr);
        }
    }
    spody_log_printf("\n");
    if (cfg->debris_mode) {
        spody_log_printf("  object           : debris (A/m=%.6f m^2/kg)\n",
               cfg->srp_area_m2);
    } else {
        spody_log_printf("  spacecraft mass  : %.3f kg\n", cfg->mass_kg);
    }
    spody_log_printf("  integrator       : rkdp45  rel_tol=%.0e  h=[%.1e, %.1e] s "
           "(init %.3f s)\n",
           cfg->rel_tol, cfg->h_min_s, cfg->h_max_s, cfg->h_init_s);
    spody_log_printf("  output mode      : %s",
           cfg->output_mode == SPODY_OUT_FIXED ? "fixed" : "step");
    if (cfg->output_mode == SPODY_OUT_FIXED) {
        spody_log_printf("  (interval %.3f s)", cfg->output_interval_s);
    }
    spody_log_printf("\n");
    if (cfg->csv_file[0]) spody_log_printf("  output csv       : %s\n", cfg->csv_file);
    if (cfg->bin_file[0]) spody_log_printf("  output binary    : %s\n", cfg->bin_file);
    if (cfg->accelerations_file[0])
        spody_log_printf("  accelerations    : %s\n", cfg->accelerations_file);
    if (cfg->events_log[0])
        spody_log_printf("  events log       : %s\n", cfg->events_log);
    spody_log_printf("  events           : impact (always on)%s",
           cfg->eclipse_event_enabled ? "" : "\n");
    if (cfg->eclipse_event_enabled)
        spody_log_printf(", eclipse (threshold %.3f)\n", cfg->eclipse_threshold);
    if (cfg->batch) {
        spody_log_printf("  batch            : %s  (%d cases x %d columns, threads=%d)\n",
               cfg->batch->name, cfg->batch->n_cases, cfg->batch->n_columns,
               cfg->batch->thread_number);
        spody_log_printf("  batch output_dir : %s\n", cfg->batch->output_dir);
        spody_log_printf("  batch cases_file : %s\n", cfg->batch->cases_file);
    }
}

static int cmd_propagate(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
                "usage: spody propagate <input.toml> [--out <dir>]\n");
        return 1;
    }
    const char *toml_path = argv[1];
    const char *out_dir   = NULL;
    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--out") == 0 && i + 1 < argc) {
            out_dir = argv[++i];
        } else {
            fprintf(stderr, "propagate: unrecognised arg '%s'\n", argv[i]);
            return 1;
        }
    }

    InputConfig cfg;
    SpodyError  err;

    if (spody_load_input(toml_path, &cfg, &err) != SPODY_OK) {
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    /* This subcommand is single-scenario; reject TOML files carrying a
     * [batch] section so the user gets a clear pointer to `spody batch`. */
    if (cfg.batch) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "[batch] section present; use 'spody batch' for batch inputs");
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    if (spody_validate_input(&cfg, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    /* --out redirects every output file (CSV / binary / log / accelerations
     * / events) to that directory, keeping only the basename from the TOML.
     * Lets the user reuse the same TOML for ad-hoc runs without editing it.
     * Acts as an escape hatch: when given, the per-run timestamp folder
     * is disabled and the user is on their own about disambiguation. */
    if (out_dir) {
        struct { char *dst; size_t cap; } slots[] = {
            { cfg.csv_file,           sizeof cfg.csv_file           },
            { cfg.bin_file,           sizeof cfg.bin_file           },
            { cfg.log_file,           sizeof cfg.log_file           },
            { cfg.accelerations_file, sizeof cfg.accelerations_file },
            { cfg.events_log,         sizeof cfg.events_log         },
        };
        for (size_t i = 0; i < sizeof slots / sizeof slots[0]; ++i) {
            if (slots[i].dst[0]) {
                char tmp[SPODY_MAX_PATH];
                snprintf(tmp, sizeof tmp, "%s/%s",
                         out_dir, spody_io_basename(slots[i].dst));
                snprintf(slots[i].dst, slots[i].cap, "%s", tmp);
            }
        }
    } else if (cfg.output_dir[0]) {
        /* Default behaviour when [output].output_dir is set: create a
         * fresh timestamp subfolder under it, snapshot the source TOML
         * inside, and rewrite every output path to live in that folder.
         * Each run is then fully self-contained -- inputs + outputs in
         * the same dir, named by the moment the run started. */
        char run_subdir[SPODY_MAX_PATH];
        if (spody_io_make_run_subdir(cfg.output_dir, run_subdir,
                                     sizeof run_subdir, &err) != SPODY_OK) {
            spody_error_print(&err);
            spody_log_close_mirror(); spody_free_input(&cfg);
            return 1;
        }
        spody_io_rewrite_outputs_to_run_subdir(&cfg, run_subdir);
        char toml_copy[SPODY_MAX_PATH];
        snprintf(toml_copy, sizeof toml_copy, "%s/input.toml", run_subdir);
        if (spody_io_copy_file(toml_path, toml_copy, &err) != SPODY_OK) {
            spody_error_print(&err);
            spody_log_close_mirror(); spody_free_input(&cfg);
            return 1;
        }
        spody_log_printf("  run dir    : %s\n", run_subdir);
    }

    /* Open the tee log mirror if requested. Done BEFORE the banner so the
     * "spody propagate: ..." line and everything that follows is captured. */
    if (cfg.log_file[0]) {
        char log_path[SPODY_MAX_PATH];
        spody_io_timestamp_filename(cfg.log_file, log_path, sizeof log_path);
        if (spody_log_open_mirror(log_path) != 0) {
            spody_error_set(&err, SPODY_ERR_IO,
                    "cannot open log_file '%s'", log_path);
            spody_error_print(&err);
            spody_log_close_mirror(); spody_free_input(&cfg);
            return 1;
        }
    }

    spody_log_printf("spody propagate: %s\n", toml_path);
    spody_log_printf("  duration   : %.3e s (%.3f days)\n",
           cfg.duration_s, cfg.duration_s / 86400.0);
    spody_log_printf("  mode       : %s",
           cfg.output_mode == SPODY_OUT_FIXED ? "fixed" : "step");
    if (cfg.output_mode == SPODY_OUT_FIXED) {
        spody_log_printf("  (every %.3f s)", cfg.output_interval_s);
    }
    spody_log_printf("\n");
    if (cfg.csv_file[0]) spody_log_printf("  -> CSV     : %s\n", cfg.csv_file);
    if (cfg.bin_file[0]) spody_log_printf("  -> binary  : %s\n", cfg.bin_file);
    if (cfg.accelerations_file[0])
        spody_log_printf("  -> accel   : %s\n", cfg.accelerations_file);
    if (cfg.events_log[0])
        spody_log_printf("  -> events  : %s\n", cfg.events_log);

    /* Two-phase setup mirrors spody-core's shared/per-thread contract:
     * SimulationShared owns the read-only file-mapped data; one or more
     * SimulationWorker(s) hold the per-thread handles + integrator state.
     * For now a single worker; future batch mode will reuse `shared`. */
    SimulationShared shared;
    if (spody_build_shared(&cfg, &shared, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    SimulationWorker w;
    if (spody_build_worker(&cfg, &shared, &w, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_free_shared(&shared);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    clock_t t0 = clock();
    int rc = spody_run_simulation(&cfg, &w, NULL, &err);
    double wall_s = (double)(clock() - t0) / (double)CLOCKS_PER_SEC;

    if (rc != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_free_worker(&w);
        spody_free_shared(&shared);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    spody_log_printf("  done in %.3f s (final state at t=%.6g s)\n",
           wall_s, w.integ.t);
    spody_free_worker(&w);
    spody_free_shared(&shared);
    spody_log_close_mirror(); spody_free_input(&cfg);
    return 0;
}

static int cmd_batch(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
                "usage: spody batch <input.toml> [--out <dir>]\n");
        return 1;
    }
    const char *toml_path = argv[1];
    /* --out is accepted for forward-compatibility but ignored today:
     * per-case output naming uses [batch].output_dir from the TOML. */
    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--out") == 0 && i + 1 < argc) {
            ++i;
        } else {
            fprintf(stderr, "batch: unrecognised arg '%s'\n", argv[i]);
            return 1;
        }
    }

    InputConfig cfg;
    SpodyError  err;

    if (spody_load_input(toml_path, &cfg, &err) != SPODY_OK) {
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    /* This subcommand requires a [batch] section; reject single-scenario
     * inputs with a clear pointer to `spody propagate`. */
    if (!cfg.batch) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "no [batch] section; use 'spody propagate' for single inputs");
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    if (spody_validate_input(&cfg, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    /* Resolve the thread count: capped at omp_get_max_threads on
     * OpenMP builds, hard-rejected above 1 on non-OpenMP builds. */
    int n_threads = cfg.batch->thread_number;
    if (n_threads < 1) n_threads = 1;
#ifdef SPODY_HAVE_OPENMP
    int max_threads = omp_get_max_threads();
    if (n_threads > max_threads) {
        spody_log_printf("  threads   : %d requested, capped at %d "
                         "(omp_get_max_threads)\n", n_threads, max_threads);
        n_threads = max_threads;
    }
#else
    if (n_threads != 1) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "batch.thread_number = %d: this binary was built without "
                "OpenMP (-DSPODY_WITH_OPENMP=OFF); use thread_number = 1.",
                cfg.batch->thread_number);
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }
#endif

    /* Create <output_dir>/<UTC-ISO8601>/ for this batch run and snapshot
     * the source TOML inside it. Same self-contained per-run layout as
     * single-propagate, just with the batch's own output_dir as parent. */
    char batch_subdir[SPODY_MAX_PATH];
    if (spody_io_make_run_subdir(cfg.batch->output_dir, batch_subdir,
                                 sizeof batch_subdir, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }
    char toml_copy[SPODY_MAX_PATH];
    snprintf(toml_copy, sizeof toml_copy, "%s/input.toml", batch_subdir);
    if (spody_io_copy_file(toml_path, toml_copy, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    /* Open the batch-level tee log inside output/batch/, named after the
     * batch + timestamp. Only when cfg.log_file is enabled in the TOML
     * (presence as toggle, same convention as csv/bin). */
    if (cfg.log_file[0]) {
        char log_path[SPODY_MAX_PATH];
        spody_io_batch_log_path(cfg.batch, batch_subdir, log_path, sizeof log_path);
        if (spody_log_open_mirror(log_path) != 0) {
            spody_error_set(&err, SPODY_ERR_IO,
                    "cannot open batch log_file '%s'", log_path);
            spody_error_print(&err);
            spody_log_close_mirror(); spody_free_input(&cfg);
            return 1;
        }
    }

    spody_log_printf("spody batch: %s\n", toml_path);
    spody_log_printf("  name      : %s\n", cfg.batch->name);
    spody_log_printf("  cases     : %d  (%d columns)\n",
           cfg.batch->n_cases, cfg.batch->n_columns);
    spody_log_printf("  output    : %s\n", batch_subdir);
#ifdef SPODY_HAVE_OPENMP
    spody_log_printf("  threads   : %d  (OpenMP)\n", n_threads);
#else
    spody_log_printf("  threads   : 1   (no OpenMP)\n");
#endif

    /* SimulationShared is opened ONCE, reused across every case --
     * exactly what the two-phase setup was designed for. */
    SimulationShared shared;
    if (spody_build_shared(&cfg, &shared, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    /* Aggregated events sink: when the user enabled events_log in the
     * TOML, open ONE file `<batch_subdir>/<batch_name>_events.bin` and
     * share it across all per-case workers. spody_run_simulation
     * appends BatchEventRecord rows (case_idx + EventRecord) under an
     * OpenMP critical, so concurrent cases don't interleave bytes.
     * Single per-case _events.bin files are suppressed below by
     * blanking cfg_i->events_log right after the case-output paths
     * are computed. */
    FILE *batch_events_fp = NULL;
    char  batch_events_path[SPODY_MAX_PATH] = {0};
    if (cfg.events_log[0]) {
        snprintf(batch_events_path, sizeof batch_events_path,
                 "%s/%s_events.bin", batch_subdir, cfg.batch->name);
        if (spody_open_batch_events(batch_events_path, &batch_events_fp) != 0) {
            spody_error_set(&err, SPODY_ERR_IO,
                    "cannot open aggregated events file '%s'",
                    batch_events_path);
            spody_error_print(&err);
            spody_free_shared(&shared);
            spody_log_close_mirror(); spody_free_input(&cfg);
            return 1;
        }
        spody_log_printf("  events    : %s  (aggregated)\n", batch_events_path);
    }

    /* Per-case status + error message. Allocated once before the
     * loop so the parallel section only writes to disjoint slots
     * (no realloc, no shared mutation hazard). The message slot is
     * sized after SpodyError.msg so any error string the engine can
     * produce fits without truncation. */
    enum { CASE_MSG_MAX = sizeof(((SpodyError *)0)->msg) };
    int   n_cases     = cfg.batch->n_cases;
    int  *case_failed = (int  *)calloc((size_t)n_cases, sizeof(int));
    char *case_errmsg = (char *)calloc((size_t)n_cases, CASE_MSG_MAX);
    if (!case_failed || !case_errmsg) {
        spody_log_printf("\nout of memory.\n");
        free(case_failed); free(case_errmsg);
        spody_free_shared(&shared);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    /* Semantics with parallel execution: every case is run to
     * completion, even if earlier cases fail. This is more useful
     * than the sequential "break on first failure" -- in a 100-case
     * sweep, knowing WHICH cases fail (not just the first) is what
     * the user wants for diagnosis. The final summary repeats the
     * full list of failed cases with their error messages. */
    double t0 = now_seconds();

    /* MSVC's bundled OpenMP is stuck at 2.0, which requires the
     * loop counter to be declared OUTSIDE the for-init clause
     * (C89 form). GCC/Clang accept either form, so the C89 style
     * is the portable choice. */
    int i;
#ifdef SPODY_HAVE_OPENMP
    /* parallel for     = combined directive: create the team + split
     *                    the loop iterations among it.
     * schedule(dyn,1)  = each thread grabs 1 iteration at a time
     *                    from a shared queue; mandatory because per-
     *                    case runtimes vary a lot (different
     *                    harmonics + step counts) and static would
     *                    leave threads idle at the tail.
     * num_threads(N)   = honour the TOML's batch.thread_number;
     *                    without this, OMP_NUM_THREADS / the host
     *                    core count would win, silently ignoring
     *                    what the user wrote. */
    #pragma omp parallel for schedule(dynamic, 1) num_threads(n_threads)
#endif
    for (i = 0; i < n_cases; ++i) {
        const char *id = cfg.batch->case_ids[i];
        InputConfig  cfg_i;
        SpodyError   err_i = {0};
        spody_apply_batch_case(&cfg, cfg.batch, i, &cfg_i);
        spody_io_case_output_paths(&cfg_i, cfg.batch, batch_subdir, i);
        /* In batch mode events are aggregated into batch_events_fp;
         * blank the per-case slot so spody_run_simulation does NOT
         * open a second file alongside it. */
        if (batch_events_fp) cfg_i.events_log[0] = '\0';

        SimulationWorker w;
        if (spody_build_worker(&cfg_i, &shared, &w, &err_i) != SPODY_OK) {
            case_failed[i] = 1;
            snprintf(&case_errmsg[i * CASE_MSG_MAX], CASE_MSG_MAX,
                     "setup: %s", err_i.msg);
            #pragma omp critical(log)
            spody_log_printf("  [%d/%d] %s: SETUP FAILED -- %s\n",
                             i + 1, n_cases, id, err_i.msg);
            continue;
        }

        /* Per-thread sink: the FILE* is shared, the case_idx is
         * per-iteration. Declaring local_sink inside the loop body
         * gives every iteration (and so every thread executing it)
         * its own auto-storage copy -- no race on case_idx. */
        BatchEventSink local_sink = { batch_events_fp, (int32_t)i };
        BatchEventSink *sink_ptr  = batch_events_fp ? &local_sink : NULL;

        double ct0 = now_seconds();
        int rc = spody_run_simulation(&cfg_i, &w, sink_ptr, &err_i);
        double cw = now_seconds() - ct0;
        spody_free_worker(&w);

        if (rc != SPODY_OK) {
            case_failed[i] = 1;
            snprintf(&case_errmsg[i * CASE_MSG_MAX], CASE_MSG_MAX,
                     "%s", err_i.msg);
            #pragma omp critical(log)
            spody_log_printf("  [%d/%d] %s: FAILED after %.2f s -- %s\n",
                             i + 1, n_cases, id, cw, err_i.msg);
        } else {
            #pragma omp critical(log)
            spody_log_printf("  [%d/%d] %s: done in %.2f s\n",
                             i + 1, n_cases, id, cw);
        }
    }
    double wall_s = now_seconds() - t0;

    /* Close the aggregated events file before tearing down anything
     * else; both success and failure paths below need to flush the
     * trailing records, so do it in one place. */
    if (batch_events_fp) {
        fclose(batch_events_fp);
        batch_events_fp = NULL;
    }

    spody_free_shared(&shared);

    /* Tally + final summary. Lists every failed case at the bottom
     * with its error message, so the user has a grep-friendly view
     * without scrolling back through interleaved per-case lines. */
    int n_failed = 0;
    for (int i = 0; i < n_cases; ++i) {
        if (case_failed[i]) ++n_failed;
    }

    if (n_failed > 0) {
        spody_log_printf("\nbatch finished: %d/%d OK, %d failed "
                         "in %.2f s total (wall).\n",
                         n_cases - n_failed, n_cases, n_failed, wall_s);
        spody_log_printf("failed cases:\n");
        for (int i = 0; i < n_cases; ++i) {
            if (!case_failed[i]) continue;
            spody_log_printf("  [%d/%d] %s: %s\n",
                             i + 1, n_cases,
                             cfg.batch->case_ids[i],
                             &case_errmsg[i * CASE_MSG_MAX]);
        }
        free(case_failed); free(case_errmsg);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }
    spody_log_printf("\nbatch done: %d/%d cases in %.2f s total (wall).\n",
           n_cases, n_cases, wall_s);
    free(case_failed); free(case_errmsg);

    spody_log_close_mirror(); spody_free_input(&cfg);
    return 0;
}

static int cmd_validate(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: spody validate <input.toml>\n");
        return 1;
    }
    const char *toml_path = argv[1];

    InputConfig cfg;
    SpodyError  err;
    if (spody_load_input(toml_path, &cfg, &err) != SPODY_OK) {
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }
    if (spody_validate_input(&cfg, &err) != SPODY_OK) {
        /* The validator does not stamp err.file -- inherit the TOML path
         * so error messages stay anchored on the input. */
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }
    print_config_summary(&cfg);
    spody_log_close_mirror(); spody_free_input(&cfg);
    return 0;
}

static int cmd_info(int argc, char **argv) {
    (void)argc; (void)argv;
    spody_log_printf("SpOdy app  : %s\n", SPODY_APP_VERSION);
    spody_log_printf("spody-core : %s  (git %s, built %s)\n",
           spody_version(), spody_git_hash(), spody_build_timestamp());
    return 0;
}

/* Compute the smallest harmonics-expansion degree at which the
 * ACCELERATION at a given orbit position is already within double-
 * precision noise of the full-file answer.
 *
 *   spody maxhgdegree <harmonics_file> <x_km> <y_km> <z_km>
 *
 * Uses spody-core's existing harmonic-gravity evaluator
 * (`spody_get_hgaccbodyfixed_hpc`) at progressively higher truncation
 * degrees, then finds the smallest n where
 *
 *     |a(n) - a(file_N)| / |a(file_N)| < EPSILON
 *
 * with EPSILON ~ 2.22e-16 (IEEE 754 double ULP). That n is the
 * acceleration-convergence threshold: above it, the propagator can
 * only burn CPU, not gain accuracy. Driving this off the real
 * accelerations (rather than a coefficient RSS proxy) gives the
 * answer the integrator actually cares about, including the
 * radial-derivative factor (n+1) and the per-degree directionality
 * the analytic shortcut would miss.
 *
 * Coordinate frame: the input position is treated as **already in
 * the body-fixed frame** of the central body. The GUI sanity-check
 * passes the ICRF state magnitude directly -- the convergence-vs-n
 * behaviour is dominated by `r = |pos|`, not by the body-fixed
 * latitude / longitude (those would shift the absolute acceleration
 * magnitude, but the cutoff degree is essentially the same within
 * 1-2 across the lunar surface).
 *
 * Output is `key: value\n` lines on stdout so the GUI can grep them
 * with no clever parsing:
 *
 *   numerical_max: 543
 *   model_max:     1200
 *   r_km:          1787.402300
 *   R_body_km:     1737.400000
 *
 * Exit code 0 on success; non-zero with an error on stderr otherwise.
 */
static int cmd_maxhgdegree(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr,
            "usage: spody maxhgdegree <harmonics_file> <x_km> <y_km> <z_km>\n");
        return 1;
    }
    const char *harmonics_file = argv[1];
    double pos[3] = {
        strtod(argv[2], NULL),
        strtod(argv[3], NULL),
        strtod(argv[4], NULL),
    };
    double r_km = sqrt(pos[0]*pos[0] + pos[1]*pos[1] + pos[2]*pos[2]);
    if (!(r_km > 0.0)) {
        fprintf(stderr, "maxhgdegree: position has zero (or invalid) magnitude\n");
        return 1;
    }

    /* Peek at the file's header line to find its intrinsic max degree
     * (`spody_load_HarmonicGravityData` refuses to load above the
     * file cap, so we cannot pass an arbitrary upper bound). The
     * format is comma-separated: R_ref, GM, <skip>, file_N, ... */
    FILE *fp = fopen(harmonics_file, "r");
    if (!fp) {
        fprintf(stderr, "maxhgdegree: cannot open '%s'\n", harmonics_file);
        return 1;
    }
    char head[256];
    if (!fgets(head, sizeof head, fp)) {
        fclose(fp);
        fprintf(stderr, "maxhgdegree: empty harmonics file '%s'\n",
                harmonics_file);
        return 1;
    }
    fclose(fp);
    (void)strtok(head, ",");      /* R_ref     */
    (void)strtok(NULL, ",");      /* GM        */
    (void)strtok(NULL, ",");      /* skip slot */
    char *tN = strtok(NULL, ",");
    int file_N = tN ? (int)strtod(tN, NULL) : 0;
    if (file_N < 2) {
        fprintf(stderr, "maxhgdegree: could not parse file degree from header\n");
        return 1;
    }

    /* Load coefficients up to the file's own cap. This is the slow
     * step (~1-2 s on GRGM1200B); the GUI hides it behind a modal. */
    HarmonicGravityData hgd;
    if (spody_load_HarmonicGravityData(&hgd, harmonics_file, file_N) != 0) {
        fprintf(stderr,
            "maxhgdegree: failed to load harmonics file at degree %d\n",
            file_N);
        return 1;
    }

    /* Setup the gravity evaluator ONCE at the file's full degree so
     * all internal buffers (A_row0..2, real, imag) are sized for the
     * worst case. We then truncate per-iteration by mutating hgd.N --
     * the evaluator reads hg->hgd->N every call and stops the inner
     * loop there, but the over-sized buffers are still valid. */
    HarmonicGravity hg;
    spody_setup_HarmonicGravity(&hg, &hgd);

    /* Allocate per-degree acceleration magnitude scratch. acc_mag[n]
     * is the |a(n)| computed with the field truncated at degree n. */
    double *acc_mag = (double *)calloc((size_t)file_N + 1, sizeof(double));
    if (!acc_mag) {
        fprintf(stderr, "maxhgdegree: out of memory\n");
        spody_free_HarmonicGravity(&hg);
        spody_free_HarmonicGravityData(&hgd);
        return 1;
    }

    /* Sweep degrees. spody-core's accel function takes a non-const
     * HarmonicGravity but only reads hg->hgd->N -- mutating the
     * underlying hgd.N before each call is the cheapest truncation. */
    int original_N = hgd.N;
    for (int n = 2; n <= original_N; n++) {
        hgd.N = n;
        double acc[3];
        spody_get_hgaccbodyfixed_hpc(&hg, pos, acc);
        acc_mag[n] = sqrt(acc[0]*acc[0] + acc[1]*acc[1] + acc[2]*acc[2]);
    }
    hgd.N = original_N;  /* restore so the cleanup path sees the full N */

    /* Find the smallest n at which the truncated-acceleration is
     * within ULP of the full-file answer. Walks forward from n=2;
     * the FIRST converged degree is what we want (the user can
     * always pick a larger N at the cost of CPU, but anything
     * BELOW `suggested` produces measurably different accelerations). */
    double acc_ref = acc_mag[original_N];
    int suggested = original_N;
    if (acc_ref > 0.0) {
        for (int n = 2; n <= original_N; n++) {
            double diff = fabs(acc_mag[n] - acc_ref);
            if (diff < DBL_EPSILON * acc_ref) {
                suggested = n;
                break;
            }
        }
    }
    free(acc_mag);

    double R_body = hgd.R_ref;

    spody_free_HarmonicGravity(&hg);
    spody_free_HarmonicGravityData(&hgd);

    /* GUI-friendly parseable output. */
    printf("numerical_max: %d\n", suggested);
    printf("model_max:     %d\n", file_N);
    printf("r_km:          %.6f\n", r_km);
    printf("R_body_km:     %.6f\n", R_body);
    return 0;
}

/* Convert raw external data into spody's native binary / .tab formats.
 *
 * Subforms:
 *   spody convert ephemeris       <folder> <de_family> <date1> [date2 ...]
 *   spody convert harmonics_icgem <input.gfc> <output.tab> [--max-degree N]
 *
 * `ephemeris`: `folder` holds the JPL ASCII chunks
 * (header.<de_family> + ascpXXXXX.<de_family>); the output
 * `de<de_family>.spody` is written back into the same folder. The list
 * of dates names the chunks to include (without the "ascp" prefix and
 * ".<de>" suffix), e.g.
 *   spody convert ephemeris ./data/DE440 440 01950 02050
 *
 * `harmonics_icgem`: read an ICGEM .gfc spherical-harmonic gravity
 * file (the standard format used by EGM2008, EGM2020, EIGEN-6C4,
 * GOCE-DIR, GRACE GGM05C, etc.) and write a .tab file in the
 * GRGM1200B-compatible CSV layout consumed by
 * spody_load_HarmonicGravityData. With `--max-degree N` the output is
 * truncated; without it the full model is kept (the harmonics loader
 * truncates again at read time per the user's TOML).
 *   spody convert harmonics_icgem ./data/EGM2008/EGM2008.gfc \
 *                                 ./data/EGM2008/egm2008.tab
 *
 * Both subforms are exposed primarily so the Python setup wizard can
 * produce the binary / .tab assets without shipping its own converter.
 */
static int cmd_convert(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
            "usage: spody convert ephemeris       <folder> <de_family> "
            "<date1> [date2 ...]\n"
            "       spody convert harmonics_icgem <input.gfc> <output.tab> "
            "[--max-degree N]\n");
        return 1;
    }

    /* ---- ephemeris ----------------------------------------------- */
    if (strcmp(argv[1], "ephemeris") == 0) {
        if (argc < 5) {
            fprintf(stderr,
                "convert ephemeris: need <folder>, <de_family>, and >= 1 date.\n");
            return 1;
        }
        const char *folder = argv[2];
        const char *de     = argv[3];
        int n_files        = argc - 4;
        const char **dates = (const char **)(argv + 4);

        spody_log_printf("spody convert ephemeris: %s (DE%s, %d chunk%s)\n",
            folder, de, n_files, n_files == 1 ? "" : "s");
        for (int i = 0; i < n_files; ++i) {
            spody_log_printf("  - ascp%s.%s\n", dates[i], de);
        }

        /* `spody_createfile_MappedEphemerisData` builds its file paths
         * as `./<path>/...` (CWD-relative by design, see the function
         * in spody-core/src/spody_ephemeris.c). That breaks when
         * `folder` is absolute or contains a drive letter. Sidestep by
         * chdir'ing INTO the folder and passing "." -- works for both
         * relative and absolute inputs without touching spody-core. */
        if (spody_chdir(folder) != 0) {
            fprintf(stderr,
                "convert ephemeris: cannot enter folder '%s'\n", folder);
            return 1;
        }
        int rc = spody_createfile_MappedEphemerisData(".", dates, n_files, de);
        if (rc != 0) {
            fprintf(stderr,
                "convert ephemeris: spody_createfile_MappedEphemerisData "
                "returned %d\n", rc);
            return 1;
        }
        spody_log_printf("OK -- wrote %s/de%s.spody\n", folder, de);
        return 0;
    }

    /* ---- harmonics_icgem ----------------------------------------- */
    if (strcmp(argv[1], "harmonics_icgem") == 0) {
        if (argc < 4) {
            fprintf(stderr,
                "convert harmonics_icgem: need <input.gfc> and <output.tab>.\n"
                "  e.g. spody convert harmonics_icgem ./data/EGM2008/EGM2008.gfc "
                "./data/EGM2008/egm2008.tab\n");
            return 1;
        }
        const char *input_gfc  = argv[2];
        const char *output_tab = argv[3];
        int max_degree = 0;  /* 0 = keep full model */
        for (int i = 4; i < argc; ++i) {
            if (strcmp(argv[i], "--max-degree") == 0 && i + 1 < argc) {
                max_degree = (int)strtol(argv[++i], NULL, 10);
                if (max_degree < 2) {
                    fprintf(stderr,
                        "convert harmonics_icgem: --max-degree must be >= 2 "
                        "(got %d)\n", max_degree);
                    return 1;
                }
            } else {
                fprintf(stderr,
                    "convert harmonics_icgem: unrecognised arg '%s'\n", argv[i]);
                return 1;
            }
        }
        spody_log_printf("spody convert harmonics_icgem: %s -> %s%s\n",
            input_gfc, output_tab,
            max_degree > 0 ? " (truncated)" : "");
        int rc = spody_convert_icgem_to_tab(input_gfc, output_tab, max_degree);
        if (rc != 0) {
            fprintf(stderr,
                "convert harmonics_icgem: spody_convert_icgem_to_tab "
                "returned %d\n", rc);
            return 1;
        }
        spody_log_printf("OK -- wrote %s\n", output_tab);
        return 0;
    }

    fprintf(stderr,
        "convert: unknown subform '%s' (expected 'ephemeris' or "
        "'harmonics_icgem')\n", argv[1]);
    return 1;
}

static void usage(const char *prog) {
    fprintf(stderr,
        "SpOdy %s -- Simultaneous Propagation of Orbital DYnamics\n"
        "\n"
        "usage: %s <command> [options]\n"
        "\n"
        "commands:\n"
        "  propagate  <input.toml> [--out <dir>]   run a single simulation\n"
        "  batch      <input.toml>                 run a batch of simulations\n"
        "  validate   <input.toml>                 check input file (no run)\n"
        "  convert    ephemeris <dir> <de> <date1> [date2 ...]\n"
        "                                          DE ASCII -> de<X>.spody\n"
        "  convert    harmonics_icgem <input.gfc> <output.tab> [--max-degree N]\n"
        "                                          ICGEM .gfc -> GRGM-style .tab\n"
        "  info                                    print version and capabilities\n"
        "  maxhgdegree <harmonics_file> <x_km> <y_km> <z_km>\n"
        "                                          largest useful harmonics degree\n"
        "\n",
        SPODY_APP_VERSION, prog);
}

int main(int argc, char **argv) {
    /* When stdout/stderr are connected to a pipe (the typical case
     * when the GUI launches us via QProcess), the C runtime defaults
     * to full block-buffering on stdout (~4 KB), so progress lines
     * arrive at the GUI terminal pane in chunks instead of streaming
     * live. Unbuffer both so every printf flushes immediately.
     *
     * _IONBF is picked over _IOLBF because the Microsoft UCRT
     * silently treats _IOLBF as _IOFBF (documented behaviour) so the
     * line-buffered mode would not actually solve the problem on
     * Windows; also _IOLBF/_IOFBF with `size = 0` and NULL buffer is
     * undefined and crashes MSVC builds with STATUS_STACK_BUFFER_
     * OVERRUN at first stdio touch. The per-printf flush cost is
     * negligible at the log cadence we emit (tens of lines per
     * second at most). */
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);

    if (argc < 2) {
        usage(argv[0]);
        return 1;
    }
    const char *cmd = argv[1];
    if      (strcmp(cmd, "propagate") == 0) return cmd_propagate(argc - 1, argv + 1);
    else if (strcmp(cmd, "batch")     == 0) return cmd_batch    (argc - 1, argv + 1);
    else if (strcmp(cmd, "validate")  == 0) return cmd_validate (argc - 1, argv + 1);
    else if (strcmp(cmd, "convert")   == 0) return cmd_convert  (argc - 1, argv + 1);
    else if (strcmp(cmd, "info")      == 0) return cmd_info     (argc - 1, argv + 1);
    else if (strcmp(cmd, "maxhgdegree") == 0) return cmd_maxhgdegree(argc - 1, argv + 1);
    else if (strcmp(cmd, "-h") == 0 || strcmp(cmd, "--help") == 0) {
        usage(argv[0]);
        return 0;
    }
    fprintf(stderr, "unknown command: %s\n\n", cmd);
    usage(argv[0]);
    return 1;
}
