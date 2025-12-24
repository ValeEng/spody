# SpOdy
**Simultaneous Propagation of Orbital DYnamics**

SpOdy is a high-performance, high-precision **orbital dynamics propagator**
designed for the **simultaneous integration of multiple space objects**.

All objects are propagated within a single simulation framework, while
**each trajectory remains dynamically independent**. This design enables
efficient batch propagation of satellites, debris, and constellations
without introducing mutual dynamical coupling.

---

## Key Features

- **Simultaneous propagation of multiple objects**
  - Multiple spacecraft and space objects propagated in the same run
  - Independent orbital dynamics for each object

- **High-precision orbital dynamics**
  - High-order numerical integration schemes
  - Accurate short- and long-term propagation

- **High-performance design**
  - Optimized for large numbers of independent trajectories
  - Suitable for constellation- and debris-scale simulations

- **Flexible dynamical modeling**
  - Central-body gravity
  - Third-body perturbations (applied independently)
  - Modular and extensible force-model architecture

- **Open and modular**
  - Clean and extensible software design
  - Easy integration into external pipelines

---

## Typical Applications

- Satellite constellation propagation
- Orbital debris evolution
- Large-scale batch orbit propagation
- Mission analysis and trade studies
- Independent multi-object simulations

---

## Design Philosophy

SpOdy focuses on **system-level efficiency without dynamical coupling**.

By propagating multiple objects simultaneously while keeping their dynamics
independent, SpOdy enables:
- Efficient large-scale simulations
- Consistent configuration and modeling
- Straightforward extension toward future coupled dynamics

---

## Future Extensions

While current versions propagate objects independently, the architecture is
designed to allow future extensions toward:
- Coupled multi-body dynamics
- Mutual perturbations
- Fully interacting N-body systems

---

## Disclaimer

This software is intended for research, educational, and engineering purposes
only and is provided "as is", without warranty of any kind.

SpOdy propagates multiple space objects within a common simulation framework;
however, each object is propagated with **independent dynamics**. The software
does not model mutual gravitational interactions, close-approach effects,
collisions, or any form of dynamical coupling between objects.

The accuracy, completeness, and suitability of the results are not guaranteed in all cases.
Users must independently verify and validate all outputs before use in
operational, mission-critical, or safety-critical contexts. The authors
disclaim any liability arising from the use of this software.

---

## Acknowledgements

SpOdy has been developed independently. Some implementation 
choices and algorithmic approaches are inspired by established concepts 
and design patterns used in the **General Mission Analysis Tool (GMAT)**.


GMAT is an open-source mission analysis system developed by NASA and released
under the Apache License 2.0.

