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
 * Central-body registry for the spody application.
 *
 * Mirrors the Python-side `_KNOWN_BODIES` in
 * spody_gui/central_bodies.py: one static table per supported body
 * gives the app everything it needs to set up a propagation against
 * that body (NAIF id, GM, mean radius, ICRF<->body-fixed rotation
 * provider).
 *
 * The enum tag (SpodyCentralBody) lives here so it can be referenced
 * from toml_input.h (parser output) and sim_setup.c (worker setup)
 * without either of them owning the body-specific knowledge.
 *
 * Adding a new central body (Earth, Mars, ...) is three local edits:
 *   1. one extra enum value here
 *   2. one extra row in `_registry[]` in central_body.c
 *   3. a matching `spody_bf_rotation_<body>` provider in spody-core
 */
#ifndef SPODY_CENTRAL_BODY_H
#define SPODY_CENTRAL_BODY_H

#include <stddef.h>

#include "spody_forcemodels.h"   /* spody_bf_rotation_fn */

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    SPODY_CENTRAL_MOON = 0
} SpodyCentralBody;

/* Everything the app needs about a central body. Constructed exactly
 * once per body in central_body.c -- callers receive a const pointer
 * into the static registry, which outlives any InputConfig that
 * stored the enum tag. */
typedef struct {
    SpodyCentralBody     body;
    const char          *name;        /* "Moon" -- static string */
    int                  naif;        /* NAIF body id (Moon = 301) */
    double               mu;          /* GM, km^3/s^2 */
    double               radius_km;   /* mean radius, also impact radius */
    spody_bf_rotation_fn bf_rotation; /* ICRF<->body-fixed rotation
                                       * provider; NULL when the body
                                       * has no model registered yet */
} SpodyCentralBodySpec;

/* Look up the full spec for a central-body tag. Returns NULL only for
 * an out-of-range enum value (defensive -- parse_central_body in
 * toml_input.c rejects unknown names upstream). */
const SpodyCentralBodySpec *spody_central_body_get(SpodyCentralBody body);

/* Resolve a name (e.g. "Moon", case-sensitive) to its enum tag.
 * Returns 0 on success and writes *out, -1 on unknown name. Used by
 * the TOML parser to validate force_model.central_body. */
int spody_central_body_from_name(const char *name, SpodyCentralBody *out);

/* Append a comma-separated list of registered names (each quoted, no
 * trailing punctuation) to `buf`. Returns the number of bytes
 * written, excluding the NUL terminator. Used to build helpful
 * "(known: 'Moon')" diagnostic suffixes when the parser rejects an
 * unsupported value. */
size_t spody_central_body_known_names(char *buf, size_t bufsz);

/* Convenience accessors -- equivalent to spody_central_body_get(body)
 * followed by ->name / ->naif, returning "?" / -1 for unknown enum
 * values so the callers (logging, validation) don't have to handle
 * NULL. */
const char *spody_central_body_name(SpodyCentralBody body);
int         spody_central_body_naif(SpodyCentralBody body);

#ifdef __cplusplus
}
#endif

#endif /* SPODY_CENTRAL_BODY_H */
