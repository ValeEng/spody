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
#include "dynamics_model.h"

#include <stdio.h>
#include <string.h>

static const SpodyDynamicsModelSpec _registry[] = {
    {
        .model       = SPODY_DYN_HIGH_FIDELITY,
        .name        = "high_fidelity",
        .implemented = 1,
    },
    {
        .model       = SPODY_DYN_CR3BP,
        .name        = "cr3bp",
        .implemented = 1,
    },
};
static const size_t _registry_n = sizeof _registry / sizeof _registry[0];

const SpodyDynamicsModelSpec *spody_dynamics_model_get(SpodyDynamicsModel m) {
    for (size_t i = 0; i < _registry_n; ++i) {
        if (_registry[i].model == m) return &_registry[i];
    }
    return NULL;
}

int spody_dynamics_model_from_name(const char *name, SpodyDynamicsModel *out) {
    if (!name || !out) return -1;
    for (size_t i = 0; i < _registry_n; ++i) {
        if (strcmp(_registry[i].name, name) == 0) {
            *out = _registry[i].model;
            return 0;
        }
    }
    return -1;
}

size_t spody_dynamics_model_known_names(char *buf, size_t bufsz) {
    if (!buf || bufsz == 0) return 0;
    buf[0] = '\0';
    size_t pos = 0;
    for (size_t i = 0; i < _registry_n && pos < bufsz; ++i) {
        int n = snprintf(buf + pos, bufsz - pos,
                         "%s'%s'%s",
                         i ? ", " : "",
                         _registry[i].name,
                         _registry[i].implemented ? "" : " (not implemented)");
        if (n <= 0) break;
        pos += (size_t)n;
    }
    return pos;
}

const char *spody_dynamics_model_name(SpodyDynamicsModel m) {
    const SpodyDynamicsModelSpec *s = spody_dynamics_model_get(m);
    return s ? s->name : "?";
}
