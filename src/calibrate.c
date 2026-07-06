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
 * `spody calibrate` implementation. Method and I/O contract are
 * documented in calibrate.h; the shape of the code mirrors
 * cmd_propagate/cmd_batch in main.c (load -> validate -> run-dir ->
 * shared setup -> per-arc workers), with the per-window drag OFF/ON
 * arc pair replacing the single propagation.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "spody_core.h"
#include "spody_time.h"
#include "app_diagnostics.h"
#include "app_io.h"
#include "toml_input.h"
#include "sim_setup.h"
#include "sim_run.h"
#include "calibrate.h"

/* Load a SPDYOUT_ v1 (state_dim 6) binary into parallel arrays:
 * t[n] (seconds, 0-anchored) and y[n][6] (km / km/s, ICRF). Caller
 * frees both on success; nothing is left allocated on failure.
 * `what` names the file's role in error messages. */
static int read_spdyout(const char *path, const char *what,
                        double **t_out, double (**y_out)[6],
                        size_t *n_out, SpodyError *err) {
    *t_out = NULL; *y_out = NULL; *n_out = 0;

    FILE *fp = fopen(path, "rb");
    if (!fp) {
        spody_error_set(err, SPODY_ERR_IO,
                "cannot open %s '%s'", what, path);
        return SPODY_ERR_IO;
    }
    char     magic[8];
    uint32_t hdr[4];
    if (fread(magic, 1, 8, fp) != 8 || memcmp(magic, "SPDYOUT_", 8) != 0 ||
        fread(hdr, sizeof(uint32_t), 4, fp) != 4 ||
        hdr[0] != 1u || hdr[1] != 6u) {
        fclose(fp);
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "%s '%s' is not a SPDYOUT_ v1 state_dim=6 binary",
                what, path);
        return SPODY_ERR_BAD_VALUE;
    }
    long fsize = 0;
    if (fseek(fp, 0, SEEK_END) != 0 || (fsize = ftell(fp)) < 0) {
        fclose(fp);
        spody_error_set(err, SPODY_ERR_IO,
                "cannot size %s '%s'", what, path);
        return SPODY_ERR_IO;
    }
    const long rec_sz = (long)(7 * sizeof(double));
    if (fsize < 24 + rec_sz || (fsize - 24) % rec_sz != 0) {
        fclose(fp);
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "%s '%s': truncated record block (%ld bytes)",
                what, path, fsize);
        return SPODY_ERR_BAD_VALUE;
    }
    size_t n = (size_t)((fsize - 24) / rec_sz);
    fseek(fp, 24, SEEK_SET);

    double  *t = (double  *)malloc(n * sizeof(double));
    double (*y)[6] = (double (*)[6])malloc(n * sizeof *y);
    if (!t || !y) {
        free(t); free(y); fclose(fp);
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "out of memory reading %s '%s' (%zu records)",
                what, path, n);
        return SPODY_ERR_INTERNAL;
    }
    for (size_t i = 0; i < n; ++i) {
        double rec[7];
        if (fread(rec, sizeof(double), 7, fp) != 7) {
            free(t); free(y); fclose(fp);
            spody_error_set(err, SPODY_ERR_IO,
                    "short read at record %zu of %s '%s'", i, what, path);
            return SPODY_ERR_IO;
        }
        t[i] = rec[0];
        memcpy(y[i], rec + 1, 6 * sizeof(double));
    }
    fclose(fp);
    *t_out = t; *y_out = y; *n_out = n;
    return SPODY_OK;
}

/* Advance from anchor index a to the window's end index e (fitted
 * samples are a+1..e): the span grows to ~win_s but never below
 * SPODY_CAL_MIN_WINDOW_SAMPLES samples, and a runt tail shorter than
 * that minimum is folded into this window instead of becoming an
 * under-determined fit of its own. */
static size_t window_end(const double *t, size_t n, size_t a, double win_s) {
    size_t e = a + 1;
    while (e + 1 < n && (t[e + 1] - t[a] <= win_s ||
                         e - a < SPODY_CAL_MIN_WINDOW_SAMPLES)) ++e;
    if (e < n - 1 && n - 1 - e < SPODY_CAL_MIN_WINDOW_SAMPLES) e = n - 1;
    return e;
}

/* One internal propagation arc: the base cfg with the IC re-anchored
 * on a reference record, drag toggled, step-mode binary to bin_path.
 * The SimulationShared (built once from the base cfg, drag enabled)
 * is reused across every arc; a drag-off worker simply skips the
 * atmosphere handles. */
static int run_arc(const InputConfig *base, const SimulationShared *shared,
                   double et_start_abs, double duration_s,
                   const double y0[6], int drag_on,
                   const char *bin_path, SpodyError *err) {
    InputConfig cfg   = *base;
    cfg.et_start_s    = et_start_abs;
    cfg.duration_s    = duration_s;
    cfg.init_kind     = SPODY_INIT_CARTESIAN;
    cfg.initial_frame = SPODY_FRAME_CENTRAL_INERTIAL;
    memcpy(cfg.position_km,  y0,     3 * sizeof(double));
    memcpy(cfg.velocity_kms, y0 + 3, 3 * sizeof(double));
    cfg.enable_drag   = drag_on;
    cfg.output_mode   = SPODY_OUT_STEP;
    snprintf(cfg.bin_file, sizeof cfg.bin_file, "%s", bin_path);
    cfg.csv_file[0]           = '\0';
    cfg.log_file[0]           = '\0';
    cfg.accelerations_file[0] = '\0';
    cfg.events_log[0]         = '\0';

    SimulationWorker w;
    if (spody_build_worker(&cfg, shared, &w, err) != SPODY_OK) return 1;
    int rc = spody_run_simulation(&cfg, &w, NULL, err);
    spody_free_worker(&w);
    return rc == SPODY_OK ? 0 : 1;
}

int spody_calibrate_run(const char *toml_path,
                        const char *reference_bin,
                        double window_h) {
    if (window_h <= 0.0) window_h = SPODY_CAL_WINDOW_DEFAULT_H;
    const double win_s = window_h * 3600.0;

    InputConfig cfg;
    SpodyError  err;

    /* Resources released through the single cleanup block below. */
    SimulationShared shared;
    int      shared_built = 0;
    double  *rt = NULL, *ioff = NULL, *di = NULL;
    double  *node_mjd = NULL, *node_k = NULL;
    double (*ry)[6] = NULL;
    int      exit_rc = 1;

    if (spody_load_input(toml_path, &cfg, &err) != SPODY_OK) {
        spody_error_print(&err);
        spody_free_input(&cfg);
        return 1;
    }
    if (cfg.batch) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "[batch] section present; calibrate takes a single scenario");
        snprintf(err.file, sizeof err.file, "%s", toml_path);
        goto fail;
    }
    if (spody_validate_input(&cfg, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        goto fail;
    }
    if (!cfg.enable_drag) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "calibrate requires force_model.drag = true: the scenario "
                "must model drag for a density scale to be observable");
        snprintf(err.file, sizeof err.file, "%s", toml_path);
        goto fail;
    }

    /* Any pre-existing calibration is dropped: the internal drag-on
     * arcs must run at the raw k = 1 for the fit to price the
     * uncalibrated model bias. */
    if (cfg.has_density_scale || cfg.density_scale_file[0]) {
        fprintf(stderr,
            "calibrate: WARNING -- [force_model] density_scale%s in '%s' "
            "is ignored while calibrating\n",
            cfg.density_scale_file[0] ? "_file" : "", toml_path);
        cfg.density_scale         = 1.0;
        cfg.has_density_scale     = 0;
        cfg.density_scale_file[0] = '\0';
    }

    size_t rn = 0;
    if (read_spdyout(reference_bin, "reference", &rt, &ry, &rn,
                     &err) != SPODY_OK) goto fail;
    if (rn < SPODY_CAL_MIN_WINDOW_SAMPLES + 1) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "reference '%s' has %zu records; at least %d are needed "
                "for one fit window", reference_bin, rn,
                SPODY_CAL_MIN_WINDOW_SAMPLES + 1);
        goto fail;
    }
    for (size_t i = 1; i < rn; ++i) {
        if (!(rt[i] > rt[i - 1])) {
            spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                    "reference '%s': time axis not strictly ascending at "
                    "record %zu (t=%.6f after %.6f)",
                    reference_bin, i, rt[i], rt[i - 1]);
            goto fail;
        }
    }
    if (!(spody_dot3(ry[0] + 3, ry[0] + 3) > 0.0)) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "reference '%s' carries no velocities (SP3-derived "
                "position-only binary?); calibrate re-anchors each window "
                "on a full reference state -- build the reference with "
                "`spody convert gps/glonass/oem`", reference_bin);
        goto fail;
    }

    /* The calibration span IS the reference span: override the TOML's
     * duration before spody_build_shared so the space-weather horizon
     * check covers every window. The 0-anchored reference time axis
     * starts at the TOML's et_start_s (workflow-wide contract). */
    cfg.duration_s = rt[rn - 1] - rt[0];

    char run_dir[SPODY_MAX_PATH];
    const char *out_parent = cfg.output_dir[0] ? cfg.output_dir : "output";
    if (spody_io_make_run_subdir(out_parent, run_dir, sizeof run_dir,
                                 &err) != SPODY_OK) goto fail;
    char toml_copy[SPODY_MAX_PATH];
    spody_io_run_subdir_filepath(run_dir, "input.toml",
                                 toml_copy, sizeof toml_copy);
    if (spody_io_copy_file(toml_path, toml_copy, &err) != SPODY_OK) goto fail;

    size_t total_windows = 0;
    for (size_t a = 0; a + 1 < rn; a = window_end(rt, rn, a, win_s)) {
        ++total_windows;
    }

    spody_log_printf("spody calibrate: %s\n", toml_path);
    spody_log_printf("  reference  : %s  (%zu records, %.3f h, "
                     "mjd %.5f..%.5f UTC)\n",
           reference_bin, rn, cfg.duration_s / 3600.0,
           spody_et_to_mjd_utc(cfg.et_start_s),
           spody_et_to_mjd_utc(cfg.et_start_s + cfg.duration_s));
    spody_log_printf("  window     : %.2f h  (%zu window%s)\n",
           window_h, total_windows, total_windows == 1 ? "" : "s");
    spody_log_printf("  run dir    : %s\n", run_dir);
    spody_log_printf("  IC         : re-anchored per window on the "
                     "reference ([initial_state] ignored)\n");

    if (spody_build_shared(&cfg, &shared, &err) != SPODY_OK) {
        if (err.file[0] == '\0') {
            snprintf(err.file, sizeof err.file, "%s", toml_path);
        }
        goto fail;
    }
    shared_built = 1;

    ioff     = (double *)malloc(rn * sizeof(double));
    di       = (double *)malloc(rn * sizeof(double));
    node_mjd = (double *)malloc(total_windows * sizeof(double));
    node_k   = (double *)malloc(total_windows * sizeof(double));
    if (!ioff || !di || !node_mjd || !node_k) {
        spody_error_set(&err, SPODY_ERR_INTERNAL,
                "out of memory (%zu reference records)", rn);
        goto fail;
    }

    size_t  n_nodes = 0;
    size_t  wi      = 0;
    double  g_sod   = 0.0, g_sdd = 0.0;    /* pooled normal equation */
    clock_t t0      = clock();

    size_t a = 0;
    while (a + 1 < rn) {
        size_t e     = window_end(rt, rn, a, win_s);
        size_t m     = e - a;              /* fitted samples a+1..e  */
        double dur   = rt[e] - rt[a];
        double et0_w = cfg.et_start_s + rt[a];
        ++wi;

        char name[48], off_path[SPODY_MAX_PATH], on_path[SPODY_MAX_PATH];
        snprintf(name, sizeof name, "cal_w%03zu_off.bin", wi);
        spody_io_run_subdir_filepath(run_dir, name, off_path, sizeof off_path);
        snprintf(name, sizeof name, "cal_w%03zu_on.bin", wi);
        spody_io_run_subdir_filepath(run_dir, name, on_path, sizeof on_path);

        if (run_arc(&cfg, &shared, et0_w, dur, ry[a], 0, off_path,
                    &err) != 0 ||
            run_arc(&cfg, &shared, et0_w, dur, ry[a], 1, on_path,
                    &err) != 0) goto fail;

        double  *mt_off = NULL, *mt_on = NULL;
        double (*my_off)[6] = NULL, (*my_on)[6] = NULL;
        size_t   mn_off = 0, mn_on = 0;
        if (read_spdyout(off_path, "arc", &mt_off, &my_off, &mn_off,
                         &err) != SPODY_OK) goto fail;
        if (read_spdyout(on_path, "arc", &mt_on, &my_on, &mn_on,
                         &err) != SPODY_OK) {
            free(mt_off); free(my_off);
            goto fail;
        }

        /* Resample both arcs onto the reference epochs (cubic Hermite
         * between accepted steps) and project the residuals on the
         * in-track axis of the reference's RIC triad. */
        for (size_t j = 1; j <= m; ++j) {
            size_t r    = a + j;
            double tloc = rt[r] - rt[a];
            double y_off[6], y_on[6];
            size_t i0 = spody_bracket_index(mt_off, mn_off, tloc);
            spody_hermite_dense_rv6(tloc, mt_off[i0], my_off[i0],
                                    mt_off[i0 + 1], my_off[i0 + 1], y_off);
            size_t i1 = spody_bracket_index(mt_on, mn_on, tloc);
            spody_hermite_dense_rv6(tloc, mt_on[i1], my_on[i1],
                                    mt_on[i1 + 1], my_on[i1 + 1], y_on);

            double rhat[3], chat[3], ihat[3];
            double rmag = sqrt(spody_dot3(ry[r], ry[r]));
            for (int q = 0; q < 3; ++q) rhat[q] = ry[r][q] / rmag;
            spody_cross3(ry[r], ry[r] + 3, chat);
            double cmag = sqrt(spody_dot3(chat, chat));
            for (int q = 0; q < 3; ++q) chat[q] /= cmag;
            spody_cross3(chat, rhat, ihat);

            double d_off[3], d_on[3];
            for (int q = 0; q < 3; ++q) {
                d_off[q] = y_off[q] - ry[r][q];
                d_on[q]  = y_on[q]  - ry[r][q];
            }
            ioff[j - 1] = spody_dot3(d_off, ihat);
            di[j - 1]   = spody_dot3(d_on, ihat) - ioff[j - 1];
        }
        free(mt_off); free(my_off);
        free(mt_on);  free(my_on);

        double sod = 0.0, sdd = 0.0, soo = 0.0;
        for (size_t j = 0; j < m; ++j) {
            sod += ioff[j] * di[j];
            sdd += di[j]   * di[j];
            soo += ioff[j] * ioff[j];
        }
        double rms_di  = sqrt(sdd / (double)m);
        double rms_off = sqrt(soo / (double)m);
        double mjd_c   = spody_et_to_mjd_utc(et0_w + 0.5 * dur);

        if (rms_di < SPODY_CAL_MIN_DELTA_RMS_KM) {
            spody_log_printf("  [%2zu/%2zu] t=%8.2f..%8.2f h  n=%5zu  "
                "SKIPPED -- drag signal %.3g mm rms below the fit floor; "
                "widen --window\n",
                wi, total_windows, rt[a] / 3600.0, rt[e] / 3600.0, m,
                rms_di * 1.0e6);
            a = e;
            continue;
        }

        double k = -sod / sdd;
        double ssr = 0.0;
        for (size_t j = 0; j < m; ++j) {
            double v = ioff[j] + k * di[j];
            ssr += v * v;
        }
        double sigma   = (m > 1)
            ? sqrt(ssr / ((double)(m - 1) * sdd)) : 0.0;
        double rms_fit = sqrt(ssr / (double)m);

        if (!isfinite(k) || !(k > 0.0)) {
            spody_log_printf("  [%2zu/%2zu] t=%8.2f..%8.2f h  n=%5zu  "
                "SKIPPED -- fitted k=%.4f not positive (maneuver in the "
                "span, or the drag setup is off by more than a scale)\n",
                wi, total_windows, rt[a] / 3600.0, rt[e] / 3600.0, m, k);
            a = e;
            continue;
        }

        node_mjd[n_nodes] = mjd_c;
        node_k[n_nodes]   = k;
        ++n_nodes;
        g_sod += sod;
        g_sdd += sdd;

        spody_log_printf("  [%2zu/%2zu] t=%8.2f..%8.2f h  n=%5zu  "
            "k=%.4f +/- %.4f  in-track rms %.1f -> %.1f m\n",
            wi, total_windows, rt[a] / 3600.0, rt[e] / 3600.0, m,
            k, sigma, rms_off * 1.0e3, rms_fit * 1.0e3);
        a = e;
    }

    if (n_nodes == 0) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "no window produced a usable fit (every window was "
                "skipped); widen --window or check the scenario's drag "
                "setup against the reference");
        goto fail;
    }

    char nodes_path[SPODY_MAX_PATH];
    spody_io_run_subdir_filepath(run_dir, "k_nodes.csv",
                                 nodes_path, sizeof nodes_path);
    FILE *fk = fopen(nodes_path, "w");
    if (!fk) {
        spody_error_set(&err, SPODY_ERR_IO,
                "cannot open nodes file '%s'", nodes_path);
        goto fail;
    }
    fprintf(fk, "# density-scale nodes fitted by `spody calibrate`\n");
    fprintf(fk, "# scenario  : %s\n", toml_path);
    fprintf(fk, "# reference : %s\n", reference_bin);
    fprintf(fk, "# window    : %.2f h  (%zu of %zu windows kept)\n",
            window_h, n_nodes, total_windows);
    fprintf(fk, "# columns   : mjd_utc, k\n");
    for (size_t i = 0; i < n_nodes; ++i) {
        fprintf(fk, "%.8f,%.6f\n", node_mjd[i], node_k[i]);
    }
    fclose(fk);

    double wall_s = (double)(clock() - t0) / (double)CLOCKS_PER_SEC;
    spody_log_printf("\n  nodes      : %s  (%zu node%s)\n",
           nodes_path, n_nodes, n_nodes == 1 ? "" : "s");
    spody_log_printf("  pooled k   : %.4f  (constant-scale equivalent "
                     "over the whole span)\n", -g_sod / g_sdd);
    spody_log_printf("  use with   : [force_model] density_scale_file = "
                     "\"%s\"\n", nodes_path);
    spody_log_printf("  done in %.2f s\n", wall_s);
    exit_rc = 0;
    goto cleanup;

fail:
    spody_error_print(&err);

cleanup:
    free(node_k);
    free(node_mjd);
    free(di);
    free(ioff);
    free(ry);
    free(rt);
    if (shared_built) spody_free_shared(&shared);
    spody_log_close_mirror();
    spody_free_input(&cfg);
    return exit_rc;
}
