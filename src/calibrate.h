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
 * `spody calibrate` -- fit a time-varying density-scale table k(t)
 * against a reference trajectory.
 *
 * Consumes a drag-enabled scenario TOML plus a SPDYOUT_ reference
 * binary (from `spody convert gps/glonass/oem`; SP3 references carry
 * no velocities and are rejected) and produces the `mjd,k` node file
 * that [force_model].density_scale_file consumes, closing the
 * calibration loop entirely inside the engine.
 *
 * Method (the manual's ch. 11 ballistic-fit, made local in time):
 * the reference span is cut into sliding windows of --window hours.
 * Each window re-anchors the initial state on the reference record at
 * the window start and propagates twice, drag OFF and drag ON (k=1).
 * Both trajectories are resampled onto the reference epochs (cubic
 * Hermite between accepted integrator steps) and the residuals are
 * projected on the in-track axis of the reference's RIC triad.
 * Because in-track drift is linear in the density scale, the
 * least-squares k for the window is closed-form:
 *
 *     dI  = I_on - I_off            (per-epoch drag signal)
 *     k*  = -sum(I_off * dI) / sum(dI^2)
 *
 * The window's node epoch is its centre (UTC MJD). Windows where the
 * drag signal is below SPODY_CAL_MIN_DELTA_RMS_KM, or where the fit
 * lands on a non-positive k (maneuver in the span, wrong ballistic
 * coefficient sign of effect), are skipped with a warning -- the
 * piecewise-linear evaluator bridges the gap between the surviving
 * nodes.
 *
 * Contract shared with the whole diff-validation workflow: the
 * reference's 0-anchored time axis starts at the TOML's
 * [simulation].et_start_s. The TOML's [initial_state] and duration
 * are ignored (each window anchors on the reference itself; the span
 * is the reference span).
 *
 * Outputs, inside a fresh timestamped run folder under the TOML's
 * output_dir: the per-window off/on trajectory binaries, a snapshot
 * of the source TOML, and `<ts>_k_nodes.csv`. A per-window report and
 * the pooled (constant-k equivalent) fit go to stdout.
 */
#ifndef SPODY_CALIBRATE_H
#define SPODY_CALIBRATE_H

#ifdef __cplusplus
extern "C" {
#endif

/* Run the whole calibration; returns a process exit code (0 = OK).
 * window_h <= 0 selects the default SPODY_CAL_WINDOW_DEFAULT_H. */
int spody_calibrate_run(const char *toml_path,
                        const char *reference_bin,
                        double window_h);

#ifdef __cplusplus
}
#endif

#endif /* SPODY_CALIBRATE_H */
