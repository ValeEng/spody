/*
 * SpOdy - command-line driver.
 *
 * Subcommand dispatch. Each subcommand reads a TOML input file describing
 * one simulation (or batch of simulations) and writes results to a directory.
 * The Python GUI under python/ generates the input TOML and parses the
 * output files; it never calls into spody-core directly.
 *
 * Subcommands (planned):
 *   spody propagate  <input.toml> [--out <dir>]
 *   spody validate   <input.toml>
 *   spody info
 *
 * This is the initial scaffolding -- each handler is a stub.
 */
#include <stdio.h>
#include <string.h>

#include "spody_core.h"
#include "app_diagnostics.h"
#include "app_io.h"
#include "toml_input.h"
#include "sim_setup.h"
#include "sim_run.h"

#define SPODY_APP_VERSION "0.1.0"

/* One-screen summary of a parsed config, printed after a successful validate.
 * Useful as a sanity check that the file says what the user thinks it says. */
static void print_config_summary(const InputConfig *cfg) {
    spody_log_printf("OK\n");
    spody_log_printf("  simulation       : %s\n", cfg->sim_name);
    spody_log_printf("  et_start         : %.6e s past J2000\n", cfg->et_start_s);
    spody_log_printf("  duration         : %.3e s  (%.3f days)\n",
           cfg->duration_s, cfg->duration_s / 86400.0);
    spody_log_printf("  central body     : %s\n",
           cfg->central_body == SPODY_CENTRAL_MOON ? "Moon" : "?");
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
        spody_log_printf("  (A=%.3f m^2, Cr=%.3f)", cfg->srp_area_m2, cfg->srp_cr);
    }
    spody_log_printf("\n");
    spody_log_printf("  spacecraft mass  : %.3f kg\n", cfg->mass_kg);
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
     * Lets the user reuse the same TOML for ad-hoc runs without editing it. */
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
    int rc = spody_run_simulation(&cfg, &w, &err);
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

    /* Parallel batch is not built yet; reject anything > 1 explicitly so
     * the user knows it's not silently ignored. */
    if (cfg.batch->thread_number != 1) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "batch.thread_number = %d: parallel execution requires "
                "an OpenMP build (not yet available, use thread_number = 1)",
                cfg.batch->thread_number);
        spody_error_print(&err);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }

    /* Create <output_dir>/batch (output_dir itself must already exist). */
    char batch_subdir[SPODY_MAX_PATH];
    if (spody_io_prepare_batch_subdir(cfg.batch->output_dir, batch_subdir,
                                      sizeof batch_subdir, &err) != SPODY_OK) {
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

    clock_t t0 = clock();
    int failed_at = -1;     /* -1 = all OK; else index of failing case */
    for (int i = 0; i < cfg.batch->n_cases; ++i) {
        const char *id = cfg.batch->case_ids[i];
        InputConfig  cfg_i;
        spody_apply_batch_case(&cfg, cfg.batch, i, &cfg_i);
        spody_io_case_output_paths(&cfg_i, cfg.batch, batch_subdir, i);

        spody_log_printf("  [%d/%d] %s: ", i + 1, cfg.batch->n_cases, id);
        fflush(stdout);

        SimulationWorker w;
        if (spody_build_worker(&cfg_i, &shared, &w, &err) != SPODY_OK) {
            spody_log_printf("setup failed -- %s\n", err.msg);
            failed_at = i;
            break;
        }

        clock_t ct0 = clock();
        int rc = spody_run_simulation(&cfg_i, &w, &err);
        double cw = (double)(clock() - ct0) / (double)CLOCKS_PER_SEC;
        spody_free_worker(&w);

        if (rc != SPODY_OK) {
            spody_log_printf("FAILED after %.2f s -- %s\n", cw, err.msg);
            failed_at = i;
            break;
        }
        spody_log_printf("done in %.2f s\n", cw);
    }
    double wall_s = (double)(clock() - t0) / (double)CLOCKS_PER_SEC;

    spody_free_shared(&shared);

    if (failed_at >= 0) {
        spody_log_printf("\nbatch stopped at case %d/%d after %.2f s total.\n",
               failed_at + 1, cfg.batch->n_cases, wall_s);
        spody_log_close_mirror(); spody_free_input(&cfg);
        return 1;
    }
    spody_log_printf("\nbatch done: %d/%d cases in %.2f s total.\n",
           cfg.batch->n_cases, cfg.batch->n_cases, wall_s);

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
        "  info                                    print version and capabilities\n"
        "\n",
        SPODY_APP_VERSION, prog);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        usage(argv[0]);
        return 1;
    }
    const char *cmd = argv[1];
    if      (strcmp(cmd, "propagate") == 0) return cmd_propagate(argc - 1, argv + 1);
    else if (strcmp(cmd, "batch")     == 0) return cmd_batch    (argc - 1, argv + 1);
    else if (strcmp(cmd, "validate")  == 0) return cmd_validate (argc - 1, argv + 1);
    else if (strcmp(cmd, "info")      == 0) return cmd_info     (argc - 1, argv + 1);
    else if (strcmp(cmd, "-h") == 0 || strcmp(cmd, "--help") == 0) {
        usage(argv[0]);
        return 0;
    }
    fprintf(stderr, "unknown command: %s\n\n", cmd);
    usage(argv[0]);
    return 1;
}
