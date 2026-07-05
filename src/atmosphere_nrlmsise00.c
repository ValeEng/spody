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
 * atmosphere_nrlmsise00.c -- Earth density callback for the drag force.
 *
 * Assembles the NRLMSISE-00 inputs from what the engine already
 * provides and evaluates the drag variant (GTD7D, anomalous oxygen
 * included):
 *
 *   position  : body-fixed (ITRF) km -> geodetic lat/lon/alt on the
 *               WGS-84 ellipsoid (spody_bf_to_geodetic)
 *   time      : ET -> UTC MJD -> day-of-year + UT seconds
 *               (spody_et_to_mjd_utc + spody_mjd_to_doy); local solar
 *               time = sec/3600 + lon/15, the consistency relation the
 *               model documentation prescribes
 *   activity  : previous-day F10.7, 81-day centered average and the
 *               7-element 3-hour Ap history from the CelesTrak table
 *               (spody_space_weather_msis_inputs); the Ap-history mode
 *               (Fortran SW(9) = -1) is the storm-time-accurate form
 *               and our table always carries the 3h bins.
 *
 * Output converts the model's native g/cm^3 to the kg/m^3 the drag
 * force expects. Failure (space weather outside the table) returns
 * non-zero and the drag force contributes zero acceleration for that
 * evaluation -- sim_setup pre-checks the run window against the table
 * horizon so this cannot happen silently in a normal run.
 */
#include "atmosphere_nrlmsise00.h"

#include "spody_const.h"
#include "spody_forcemodels.h"
#include "spody_math.h"
#include "spody_nrlmsise00.h"
#include "spody_time.h"

static int nrlmsise00_density(const ForceModelContext *ctx, double et,
                              const double r_bf_km[3],
                              double *rho_kg_m3_out) {
    double lat_rad, lon_rad, alt_km;
    double f107_prev, f107a, ap7[7];
    double mjd, sec;
    int doy = 0, k;
    SpodyNrlmsise00Input inp;
    SpodyNrlmsise00Output out;

    if (!ctx || !ctx->space_weather || !rho_kg_m3_out) return -1;

    spody_bf_to_geodetic(r_bf_km, WGS84_A_KM, WGS84_INV_F,
                         &lat_rad, &lon_rad, &alt_km);
    if (alt_km < 0.0) alt_km = 0.0;   /* impact-grazing evaluations */

    if (spody_space_weather_msis_inputs(ctx->space_weather, et,
                                        &f107_prev, &f107a, ap7) != 0)
        return -1;

    mjd = spody_et_to_mjd_utc(et);
    spody_mjd_to_doy(mjd, NULL, &doy, &sec);

    inp.doy      = doy;
    inp.sec      = sec;
    inp.alt_km   = alt_km;
    inp.glat_deg = lat_rad * RAD2DEG;
    inp.glon_deg = lon_rad * RAD2DEG;
    inp.lst_hr   = sec / 3600.0 + inp.glon_deg / 15.0;
    inp.f107a    = f107a;
    inp.f107     = f107_prev;
    inp.ap       = ap7[0];
    for (k = 0; k < 7; ++k)
        inp.ap_array[k] = ap7[k];
    inp.use_ap_array = 1;

    spody_nrlmsise00_gtd7d(&inp, &out);
    *rho_kg_m3_out = out.d[5] * 1000.0;   /* g/cm^3 -> kg/m^3 */
    return 0;
}

SpodyAtmosphere spody_atmosphere_nrlmsise00 = { nrlmsise00_density, NULL };
