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
 * Dynamics-model registry for the spody application.
 *
 * The integrator (spody-core) is already model-agnostic: it consumes
 * any spody_rhs_fn + opaque user pointer. What lives here is the
 * application-side selector that decides which RHS + which set of
 * TOML sections to parse and validate.
 *
 * Today's only fully-implemented model is "high_fidelity" (Cowell
 * formulation around a central body with harmonics, third bodies, SRP,
 * EOP/IAU 2006 when Earth-centred). "cr3bp" is registered as a tag
 * placeholder so the parser, validator, and worker builder can dispatch
 * to a clean "not yet implemented" error; the actual RHS + per-model
 * TOML schema will land when the model is defined.
 *
 * Adding a new model is two local edits:
 *   1. one extra enum value here
 *   2. one extra row in `_registry[]` in dynamics_model.c
 * Once the model is concretely implemented, flip its `implemented` flag
 * and route the validator / sim_setup branches accordingly.
 */
#ifndef SPODY_DYNAMICS_MODEL_H
#define SPODY_DYNAMICS_MODEL_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    SPODY_DYN_HIGH_FIDELITY = 0,   /* Cowell: central body + harmonics +
                                    * third bodies + SRP (+ EOP/IAU 2006
                                    * when Earth). Fully implemented. */
    SPODY_DYN_CR3BP         = 1    /* Circular Restricted 3-Body Problem
                                    * in the synodic frame. Scaffolding
                                    * only -- TOML parser, validator and
                                    * sim_setup return "not implemented". */
} SpodyDynamicsModel;

typedef struct {
    SpodyDynamicsModel  model;
    const char         *name;        /* TOML string, static */
    int                 implemented; /* 1 = fully wired; 0 = tag-only */
} SpodyDynamicsModelSpec;

/* Look up the spec for a model tag. Returns NULL only for an
 * out-of-range enum value (defensive). */
const SpodyDynamicsModelSpec *spody_dynamics_model_get(SpodyDynamicsModel m);

/* Resolve a TOML string (case-sensitive) to its enum tag. Returns 0
 * on success and writes *out, -1 on unknown name. */
int spody_dynamics_model_from_name(const char *name, SpodyDynamicsModel *out);

/* Append a comma-separated list of registered names (each quoted, no
 * trailing punctuation) to `buf`. Implemented-only entries are tagged
 * with a trailing "*" so error messages can hint which models are
 * actually wired today vs which are tag placeholders. Returns the
 * number of bytes written, excluding the NUL terminator. */
size_t spody_dynamics_model_known_names(char *buf, size_t bufsz);

/* Convenience accessor returning the registered name or "?" for an
 * unknown enum value. */
const char *spody_dynamics_model_name(SpodyDynamicsModel m);

#ifdef __cplusplus
}
#endif

#endif /* SPODY_DYNAMICS_MODEL_H */
