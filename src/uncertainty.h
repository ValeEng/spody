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
 * `spody uncertainty montecarlo` -- propagate the uncertainty of a
 * scenario by Monte Carlo.
 *
 * Input: a `<name>.uq.toml` file (SpodyUqConfig, toml_input.h) that
 * points at a propagate scenario and states how its initial state and
 * force parameters are dispersed. Case 0 is the nominal scenario; cases
 * 1..samples draw their dispersions from Philox streams
 * (seed, case, substream), one substream per dispersed quantity, so a
 * case is the same whatever the number of cases or threads.
 */
#ifndef SPODY_UNCERTAINTY_H
#define SPODY_UNCERTAINTY_H

#ifdef __cplusplus
extern "C" {
#endif

/* Run the whole command; returns a process exit code (0 = OK).
 * samples_only = 1 stops after writing the drawn samples. */
int spody_uncertainty_montecarlo_run(const char *uq_path, int samples_only);

#ifdef __cplusplus
}
#endif

#endif /* SPODY_UNCERTAINTY_H */
