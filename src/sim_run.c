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
 * Propagation driver. See sim_run.h for the sampling/output contract.
 *
 * Hot loop layout (fixed mode):
 *
 *   emit_traj  (t = 0, y0)
 *   emit_accel (t = 0, y0)               -- if accelerations enabled
 *   t_next = dt
 *   while (integ.t < t_end):
 *       clip integ.h to land on t_end if it would overshoot
 *       onestep
 *       while t_next <= integ.t:
 *           y_q = hermite(integ, t_next)
 *           emit_traj  (t_next, y_q)
 *           emit_accel (t_next, y_q)      -- if accelerations enabled
 *           t_next += dt
 *       impact_check(refined) on central + every third body
 *       if any event triggered -> log + STOP
 *
 * Step mode skips the dense-output drain: emit_traj / emit_accel run
 * once per accepted step on integ.y, the impact check runs on the same
 * state.
 *
 * IMPACT predicate uses the per-thread ephemeris cache built into
 * spody_get_ephposition: the integrator's last RHS stage (FSAL, c=1.0)
 * has just queried each body at the same `et`, so the event check is
 * a cache hit -- no extra Chebyshev evaluation per step.
 */
#include "sim_run.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "spody_events.h"        /* SpodyEvent, EventRecord, refined check */
#include "spody_forcemodels.h"   /* ForceBreakdown, spody_force_breakdown  */
#include "toml_input.h"          /* spody_lookup_body_by_naif              */
#include "app_diagnostics.h"     /* spody_log_printf                       */

/* --------------------------------------------------------------------------
 * Trajectory writer (CSV + binary)
 * -------------------------------------------------------------------------- */

#define SPODY_BIN_MAGIC   "SPDYOUT_"   /* 8 bytes, no NUL */
#define SPODY_BIN_VERSION 1u
#define SPODY_BIN_STATE_DIM 6u

static int write_csv_header(FILE *fp) {
    return fprintf(fp,
        "# t [s], x [km], y [km], z [km], vx [km/s], vy [km/s], vz [km/s]\n") < 0
        ? -1 : 0;
}

static int write_bin_header(FILE *fp) {
    if (fwrite(SPODY_BIN_MAGIC, 1, 8, fp) != 8) return -1;
    uint32_t hdr[4] = {
        SPODY_BIN_VERSION,
        SPODY_BIN_STATE_DIM,
        0u,
        0u
    };
    if (fwrite(hdr, sizeof(uint32_t), 4, fp) != 4) return -1;
    return 0;
}

static int emit_trajectory(FILE *csv, FILE *bin, double t, const double y[6]) {
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
 * Accelerations writer
 *
 * Records are `ForceBreakdown` structs from spody-core, written verbatim
 * (one struct per record, including the n_third counter and the
 * SPODY_FM_MAX_THIRD per-body slots even when only the first n_third are
 * populated). The reader can mmap the data section as a flat
 * `ForceBreakdown[]` array.
 * -------------------------------------------------------------------------- */

#define SPODY_ACC_MAGIC   "SPDYACC_"
#define SPODY_ACC_VERSION 1u

static int write_acc_header(FILE *fp) {
    if (fwrite(SPODY_ACC_MAGIC, 1, 8, fp) != 8) return -1;
    uint32_t hdr[4] = {
        SPODY_ACC_VERSION,
        (uint32_t)sizeof(ForceBreakdown),   /* record_size in bytes */
        0u,
        0u
    };
    if (fwrite(hdr, sizeof(uint32_t), 4, fp) != 4) return -1;
    return 0;
}

static int emit_breakdown(FILE *fp, const ForceModelContext *ctx,
                          double t, const double *y) {
    if (!fp) return 0;
    ForceBreakdown bd;
    spody_force_breakdown(ctx, t, y, &bd);
    if (fwrite(&bd, sizeof bd, 1, fp) != 1) return -1;
    return 0;
}

/* --------------------------------------------------------------------------
 * Events writer
 *
 * One `EventRecord` per trigger, written verbatim. The events log is
 * opt-in (cfg->events_log non-empty) but the IMPACT check is always on
 * -- if disabled, triggers still stop the propagation, they just are
 * not recorded.
 * -------------------------------------------------------------------------- */

#define SPODY_EVT_MAGIC   "SPDYEVT_"
#define SPODY_EVT_VERSION 1u

static int write_evt_header(FILE *fp) {
    if (fwrite(SPODY_EVT_MAGIC, 1, 8, fp) != 8) return -1;
    uint32_t hdr[4] = {
        SPODY_EVT_VERSION,
        (uint32_t)sizeof(EventRecord),
        0u,
        0u
    };
    if (fwrite(hdr, sizeof(uint32_t), 4, fp) != 4) return -1;
    return 0;
}

static int emit_event(FILE *fp, const SpodyEvent *ev) {
    if (!fp) return 0;
    EventRecord r;
    r.t           = ev->t_trigger;
    r.kind        = (int)ev->kind;
    r.naif_id     = ev->naif_id;
    r.radius_km   = ev->radius_km;
    r.distance_km = ev->distance_at_trigger;
    for (int i = 0; i < 6; ++i) r.y[i] = ev->y_trigger[i];
    if (fwrite(&r, sizeof r, 1, fp) != 1) return -1;
    return 0;
}

/* --------------------------------------------------------------------------
 * Event list
 *
 * IMPACT is always on: one SpodyEvent per body in the force model
 *   - central body : threshold = ctx->R_central
 *   - each third   : threshold from BODY_TABLE (mean equatorial radius)
 * action = LOG_AND_STOP (an impact ends the propagation).
 *
 * ECLIPSE is opt-in (cfg->eclipse_event_enabled): one SpodyEvent on the
 * central body as occulter, threshold = cfg->eclipse_threshold,
 * action = LOG (informational, propagation continues).
 *
 * Third bodies with no known radius are skipped silently (the validator
 * already gates names against BODY_TABLE). Heap-owned, freed in cleanup.
 * -------------------------------------------------------------------------- */
static int build_events(const InputConfig *cfg, const SimulationWorker *w,
                        SpodyEvent **out_events, int *out_n,
                        SpodyError *err) {
    *out_events = NULL;
    *out_n      = 0;

    int cap = 1 + w->n_third + (cfg->eclipse_event_enabled ? 1 : 0);
    SpodyEvent *ev = (SpodyEvent *)calloc((size_t)cap, sizeof *ev);
    if (!ev) {
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "out of memory allocating event array (%d entries)", cap);
        return SPODY_ERR_INTERNAL;
    }

    int n = 0;
    /* IMPACT: central body. */
    ev[n] = spody_event_impact(w->ctx.naif_central, w->ctx.R_central,
                               SPODY_EVENT_ACTION_LOG_AND_STOP);
    n++;

    /* IMPACT: third bodies. */
    for (int i = 0; i < w->n_third; ++i) {
        double r_km = 0.0;
        if (spody_lookup_body_by_naif(w->third_naif[i], NULL, NULL, &r_km) != 0
            || r_km <= 0.0) {
            continue;
        }
        ev[n] = spody_event_impact(w->third_naif[i], r_km,
                                   SPODY_EVENT_ACTION_LOG_AND_STOP);
        n++;
    }

    /* ECLIPSE: central body as occulter, informational (LOG only). */
    if (cfg->eclipse_event_enabled) {
        ev[n] = spody_event_eclipse(w->ctx.naif_central, w->ctx.R_central,
                                    cfg->eclipse_threshold,
                                    SPODY_EVENT_ACTION_LOG);
        n++;
    }

    *out_events = ev;
    *out_n      = n;
    return SPODY_OK;
}

/* Run the refined check on every configured event after an accepted
 * step. Kind-agnostic and action-aware:
 *   - the per-kind latch lives inside spody_event_check_refined (this
 *     loop has no special-case for already-fired events);
 *   - the per-event action drives behaviour on a new fire:
 *       LOG          -> write an EventRecord, keep propagating
 *       STOP         -> stop, no log entry
 *       LOG_AND_STOP -> write an EventRecord, stop
 *
 * Today the only kind is IMPACT, but altitude / eclipse / apsis entries
 * plug in unchanged: ev->kind selects the predicate, ev->action selects
 * the consequence.
 *
 * Returns:
 *    0 -> nothing to do (no new fire, or all new fires were LOG-only)
 *    1 -> a STOP-class event fired; events[*first_idx] is the first one
 *         (in array order, central body first by construction)
 *   -1 -> I/O error while writing the events log */
static int check_events(SpodyEvent *events, int n_events,
                        const ForceModelContext *ctx,
                        const IntegratorAllData *integ,
                        FILE *evt_fp, int *first_idx) {
    int stop = 0;
    for (int i = 0; i < n_events; ++i) {
        if (spody_event_check_refined(&events[i], ctx, integ) != 1) continue;

        const spody_event_action act = events[i].action;
        const int do_log  = (act == SPODY_EVENT_ACTION_LOG
                          || act == SPODY_EVENT_ACTION_LOG_AND_STOP);
        const int do_stop = (act == SPODY_EVENT_ACTION_STOP
                          || act == SPODY_EVENT_ACTION_LOG_AND_STOP);

        if (do_log && evt_fp && emit_event(evt_fp, &events[i]) < 0) return -1;
        if (do_stop && !stop) {
            *first_idx = i;
            stop = 1;
        }
    }
    return stop;
}

/* --------------------------------------------------------------------------
 * Main driver
 * -------------------------------------------------------------------------- */

int spody_run_simulation(const InputConfig *cfg, SimulationWorker *w,
                          SpodyError *err) {
    spody_error_clear(err);

    FILE *csv = NULL, *bin = NULL, *acc = NULL, *evt = NULL;
    SpodyEvent *events = NULL;
    int         n_events = 0;
    int         rc = SPODY_OK;

    /* ----- open the requested output files ----- */
    if (cfg->csv_file[0]) {
        csv = fopen(cfg->csv_file, "w");
        if (!csv) {
            spody_error_set(err, SPODY_ERR_IO,
                    "cannot open csv_file for write: '%s'", cfg->csv_file);
            rc = SPODY_ERR_IO; goto cleanup;
        }
        setvbuf(csv, NULL, _IOFBF, 1u << 20);
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
    if (cfg->accelerations_file[0]) {
        acc = fopen(cfg->accelerations_file, "wb");
        if (!acc) {
            spody_error_set(err, SPODY_ERR_IO,
                    "cannot open accelerations_file for write: '%s'",
                    cfg->accelerations_file);
            rc = SPODY_ERR_IO; goto cleanup;
        }
        setvbuf(acc, NULL, _IOFBF, 1u << 20);
        if (write_acc_header(acc) < 0) {
            spody_error_set(err, SPODY_ERR_IO,
                    "accelerations header write failed: '%s'",
                    cfg->accelerations_file);
            rc = SPODY_ERR_IO; goto cleanup;
        }
    }
    if (cfg->events_log[0]) {
        evt = fopen(cfg->events_log, "wb");
        if (!evt) {
            spody_error_set(err, SPODY_ERR_IO,
                    "cannot open events_log for write: '%s'", cfg->events_log);
            rc = SPODY_ERR_IO; goto cleanup;
        }
        /* Events are rare (one per body, max) so no big buffer needed,
         * but a small one keeps the syscall count down on writers. */
        setvbuf(evt, NULL, _IOFBF, 4096);
        if (write_evt_header(evt) < 0) {
            spody_error_set(err, SPODY_ERR_IO,
                    "events_log header write failed: '%s'", cfg->events_log);
            rc = SPODY_ERR_IO; goto cleanup;
        }
    }

    /* ----- event list: always-on IMPACT + opt-in ECLIPSE ----- */
    if ((rc = build_events(cfg, w, &events, &n_events, err)) != SPODY_OK) {
        goto cleanup;
    }

    /* ----- initial sample (both modes) ----- */
    if (emit_trajectory(csv, bin, w->integ.t, w->integ.y) < 0) {
        spody_error_set(err, SPODY_ERR_IO, "write failed on initial record");
        rc = SPODY_ERR_IO; goto cleanup;
    }
    if (acc && emit_breakdown(acc, &w->ctx, w->integ.t, w->integ.y) < 0) {
        spody_error_set(err, SPODY_ERR_IO,
                "write failed on initial accelerations record");
        rc = SPODY_ERR_IO; goto cleanup;
    }

    const double t_end = cfg->duration_s;
    const double eps   = 1.0e-9;

    if (cfg->output_mode == SPODY_OUT_FIXED) {
        const double dt = cfg->output_interval_s;
        double t_next   = dt;
        double t_last_emitted = w->integ.t;

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

            /* Drain every grid sample that fell into the just-completed
             * interval [t_old, t]. Hermite C^1 dense output on (r, v). */
            while (t_next <= w->integ.t + eps && t_next <= t_end + eps) {
                double y_q[6];
                spody_hermite_dense_rv6(t_next,
                                        w->integ.t_old, w->integ.y_old,
                                        w->integ.t,     w->integ.y,
                                        y_q);
                if (emit_trajectory(csv, bin, t_next, y_q) < 0) {
                    spody_error_set(err, SPODY_ERR_IO,
                            "trajectory write failed at t=%.6g s", t_next);
                    rc = SPODY_ERR_IO; goto cleanup;
                }
                if (acc && emit_breakdown(acc, &w->ctx, t_next, y_q) < 0) {
                    spody_error_set(err, SPODY_ERR_IO,
                            "accelerations write failed at t=%.6g s", t_next);
                    rc = SPODY_ERR_IO; goto cleanup;
                }
                t_last_emitted = t_next;
                t_next += dt;
            }

            /* IMPACT check on the just-completed step. Triggers stop the
             * propagation immediately (after logging). */
            int first = -1;
            int ev_rc = check_events(events, n_events, &w->ctx, &w->integ,
                                      evt, &first);
            if (ev_rc < 0) {
                spody_error_set(err, SPODY_ERR_IO,
                        "events_log write failed at t=%.6g s", w->integ.t);
                rc = SPODY_ERR_IO; goto cleanup;
            }
            if (ev_rc > 0) {
                spody_log_printf(
                    "  IMPACT: body NAIF=%d, t=%.3f s, |r|=%.3f km (R=%.3f km)\n",
                    events[first].naif_id, events[first].t_trigger,
                    events[first].distance_at_trigger, events[first].radius_km);
                /* Emit the trigger state as the trajectory endpoint so
                 * the output always closes at the physical end of the
                 * propagation. */
                if (emit_trajectory(csv, bin,
                                    events[first].t_trigger,
                                    events[first].y_trigger) < 0) {
                    spody_error_set(err, SPODY_ERR_IO,
                            "trajectory write failed on impact record");
                    rc = SPODY_ERR_IO; goto cleanup;
                }
                if (acc && emit_breakdown(acc, &w->ctx,
                                          events[first].t_trigger,
                                          events[first].y_trigger) < 0) {
                    spody_error_set(err, SPODY_ERR_IO,
                            "accelerations write failed on impact record");
                    rc = SPODY_ERR_IO; goto cleanup;
                }
                goto cleanup;   /* normal termination via impact */
            }
        }

        /* If duration_s is not a clean multiple of dt, append the endpoint
         * so the user always sees the final integrator state. */
        if (t_end - t_last_emitted > eps) {
            if (emit_trajectory(csv, bin, w->integ.t, w->integ.y) < 0) {
                spody_error_set(err, SPODY_ERR_IO,
                        "write failed on endpoint record");
                rc = SPODY_ERR_IO; goto cleanup;
            }
            if (acc && emit_breakdown(acc, &w->ctx,
                                      w->integ.t, w->integ.y) < 0) {
                spody_error_set(err, SPODY_ERR_IO,
                        "accelerations write failed on endpoint record");
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
            if (emit_trajectory(csv, bin, w->integ.t, w->integ.y) < 0) {
                spody_error_set(err, SPODY_ERR_IO,
                        "trajectory write failed at t=%.6g s", w->integ.t);
                rc = SPODY_ERR_IO; goto cleanup;
            }
            if (acc && emit_breakdown(acc, &w->ctx,
                                      w->integ.t, w->integ.y) < 0) {
                spody_error_set(err, SPODY_ERR_IO,
                        "accelerations write failed at t=%.6g s", w->integ.t);
                rc = SPODY_ERR_IO; goto cleanup;
            }

            int first = -1;
            int ev_rc = check_events(events, n_events, &w->ctx, &w->integ,
                                      evt, &first);
            if (ev_rc < 0) {
                spody_error_set(err, SPODY_ERR_IO,
                        "events_log write failed at t=%.6g s", w->integ.t);
                rc = SPODY_ERR_IO; goto cleanup;
            }
            if (ev_rc > 0) {
                spody_log_printf(
                    "  IMPACT: body NAIF=%d, t=%.3f s, |r|=%.3f km (R=%.3f km)\n",
                    events[first].naif_id, events[first].t_trigger,
                    events[first].distance_at_trigger, events[first].radius_km);
                goto cleanup;
            }
        }
    }

cleanup:
    if (csv) fclose(csv);
    if (bin) fclose(bin);
    if (acc) fclose(acc);
    if (evt) fclose(evt);
    free(events);
    return rc;
}
