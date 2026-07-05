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
 * NRLMSISE-00 atmosphere wrapper: binds the engine's density model
 * (spody_nrlmsise00.h) to the generic SpodyAtmosphere callback
 * contract (spody_atmosphere.h). Registered on the Earth row of the
 * central-body registry (central_body.c); sim_setup puts it on the
 * ForceModelContext when the run enables drag.
 */
#ifndef SPODY_APP_ATMOSPHERE_NRLMSISE00_H
#define SPODY_APP_ATMOSPHERE_NRLMSISE00_H

#include "spody_atmosphere.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The one NRLMSISE-00 atmosphere instance (stateless: `state` stays
 * NULL, everything comes from the ForceModelContext). Non-const only
 * because ForceModelContext carries a mutable pointer slot. */
extern SpodyAtmosphere spody_atmosphere_nrlmsise00;

#ifdef __cplusplus
}
#endif

#endif /* SPODY_APP_ATMOSPHERE_NRLMSISE00_H */
