/*
 * Propagation driver. See sim_run.h for the sampling/output contract.
 *
 * Hot loop layout (fixed mode):
 *
 *   emit (t = 0, y0)
 *   t_next = dt
 *   while (integ.t < t_end):
 *       clip integ.h to land on t_end if it would overshoot
 *       onestep
 *       while t_next <= integ.t:
 *           theta = (t_next - integ.t_old) / integ.h_old
 *           dense_eval(integ, theta, y_q)
 *           emit (t_next, y_q)
 *           t_next += dt
 *   if last emit != t_end: emit (t_end, integ.y)
 *
 * Step mode skips the dense-output drain and just writes integ.y after
 * every accepted step.
 */
#include "sim_run.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* --------------------------------------------------------------------------
 * Writers
 * -------------------------------------------------------------------------- */

#define SPODY_BIN_MAGIC "SPDYOUT_"   /* 8 bytes, no NUL */
#define SPODY_BIN_VERSION 1u
#define SPODY_BIN_STATE_DIM 6u

static int write_csv_header(FILE *fp) {
    return fprintf(fp,
        "# t [s], x [km], y [km], z [km], vx [km/s], vy [km/s], vz [km/s]\n") < 0
        ? -1 : 0;
}

/* 24-byte binary header. Little-endian (native on every CI target). */
static int write_bin_header(FILE *fp) {
    if (fwrite(SPODY_BIN_MAGIC, 1, 8, fp) != 8) return -1;
    uint32_t hdr[4] = {
        SPODY_BIN_VERSION,    /* format_version              */
        SPODY_BIN_STATE_DIM,  /* state_dim (doubles after t) */
        0u,                   /* reserved                    */
        0u                    /* reserved                    */
    };
    if (fwrite(hdr, sizeof(uint32_t), 4, fp) != 4) return -1;
    return 0;
}

static int emit_record(FILE *csv, FILE *bin, double t, const double y[6]) {
    if (csv) {
        if (fprintf(csv,
                    "%.15e,%.15e,%.15e,%.15e,%.15e,%.15e,%.15e\n",
                    t, y[0], y[1], y[2], y[3], y[4], y[5]) < 0) {
            return -1;
        }
    }
    if (bin) {
        double rec[7] = { t, y[0], y[1], y[2], y[3], y[4], y[5] };
        if (fwrite(rec, sizeof(double), 7, bin) != 7) return -1;
    }
    return 0;
}

/* --------------------------------------------------------------------------
 * Main driver
 * -------------------------------------------------------------------------- */

int spody_run_simulation(const InputConfig *cfg, SimulationWorker *w,
                          SpodyError *err) {
    spody_error_clear(err);

    FILE *csv = NULL;
    FILE *bin = NULL;
    int rc = SPODY_OK;

    /* Open requested output files, fail fast if anything is wrong. */
    if (cfg->csv_file[0]) {
        csv = fopen(cfg->csv_file, "w");
        if (!csv) {
            spody_error_set(err, SPODY_ERR_IO,
                    "cannot open csv_file for write: '%s'", cfg->csv_file);
            rc = SPODY_ERR_IO; goto cleanup;
        }
        /* Large stdio buffer -- CSV writes dominate wall time otherwise. */
        setvbuf(csv, NULL, _IOFBF, 1u << 20); // 1 MiB buffer --> 1u << 20
        if (write_csv_header(csv) < 0) {
            spody_error_set(err, SPODY_ERR_IO, "csv header write failed: '%s'",
                    cfg->csv_file);
            rc = SPODY_ERR_IO; goto cleanup;
        }
    }
    if (cfg->bin_file[0]) {
        bin = fopen(cfg->bin_file, "wb");
        if (!bin) {
            spody_error_set(err, SPODY_ERR_IO,
                    "cannot open bin_file for write: '%s'", cfg->bin_file);
            rc = SPODY_ERR_IO; goto cleanup;
        }
        setvbuf(bin, NULL, _IOFBF, 1u << 20);
        if (write_bin_header(bin) < 0) {
            spody_error_set(err, SPODY_ERR_IO, "binary header write failed: '%s'",
                    cfg->bin_file);
            rc = SPODY_ERR_IO; goto cleanup;
        }
    }

    /* Initial state always lands in the output (both modes). */
    if (emit_record(csv, bin, w->integ.t, w->integ.y) < 0) {
        spody_error_set(err, SPODY_ERR_IO, "write failed on initial record");
        rc = SPODY_ERR_IO; goto cleanup;
    }

    const double t_end = cfg->duration_s;
    const double eps   = 1.0e-9;   /* absolute slack on grid-edge comparisons */

    if (cfg->output_mode == SPODY_OUT_FIXED) {
        const double dt = cfg->output_interval_s;
        double t_next   = dt;
        double t_last_emitted = w->integ.t;

        while (w->integ.t < t_end - eps) {
            /* Clip the proposed step so we never overshoot the endpoint --
             * mirrors the behaviour of spody_propagate_untilend. */
            double h_remain = t_end - w->integ.t;
            if (w->integ.h > h_remain) w->integ.h = h_remain;

            int s = spody_propagate_onestep(&w->integ);
            if (s != SPODY_INTEG_OK) {
                spody_error_set(err, SPODY_ERR_INTERNAL,
                        "integrator failed (rc=%d) at t=%.6g s, h=%.6g s",
                        s, w->integ.t, w->integ.h_old);
                rc = SPODY_ERR_INTERNAL; goto cleanup;
            }

            /* Drain every grid sample that fell into the just-completed
             * interval [t_old, t]. Cubic Hermite on (r, v) via spody-core. */
            while (t_next <= w->integ.t + eps && t_next <= t_end + eps) {
                double y_q[6];
                spody_hermite_dense_rv6(t_next,
                                        w->integ.t_old, w->integ.y_old,
                                        w->integ.t,     w->integ.y,
                                        y_q);
                if (emit_record(csv, bin, t_next, y_q) < 0) {
                    spody_error_set(err, SPODY_ERR_IO, "write failed at t=%.6g s",
                            t_next);
                    rc = SPODY_ERR_IO; goto cleanup;
                }
                t_last_emitted = t_next;
                t_next += dt;
            }
        }

        /* If duration_s is not a clean multiple of dt, append the endpoint
         * so the user always sees the final integrator state. */
        if (t_end - t_last_emitted > eps) {
            if (emit_record(csv, bin, w->integ.t, w->integ.y) < 0) {
                spody_error_set(err, SPODY_ERR_IO,
                        "write failed on endpoint record");
                rc = SPODY_ERR_IO; goto cleanup;
            }
        }
    } else {
        /* STEP mode: one record per accepted integrator step. */
        while (w->integ.t < t_end - eps) {
            double h_remain = t_end - w->integ.t;
            if (w->integ.h > h_remain) w->integ.h = h_remain;

            int s = spody_propagate_onestep(&w->integ);
            if (s != SPODY_INTEG_OK) {
                spody_error_set(err, SPODY_ERR_INTERNAL,
                        "integrator failed (rc=%d) at t=%.6g s, h=%.6g s",
                        s, w->integ.t, w->integ.h_old);
                rc = SPODY_ERR_INTERNAL; goto cleanup;
            }
            if (emit_record(csv, bin, w->integ.t, w->integ.y) < 0) {
                spody_error_set(err, SPODY_ERR_IO, "write failed at t=%.6g s",
                        w->integ.t);
                rc = SPODY_ERR_IO; goto cleanup;
            }
        }
    }

cleanup:
    if (csv) fclose(csv);
    if (bin) fclose(bin);
    return rc;
}
