# Native solver compatibility profile

The prepared runtime pins OR-Tools 9.15, CVXPY 1.7 and standalone HiGHS 1.12. OR-Tools 9.15 bundles HiGHS 1.12; loading a different `libhighs` with the same SONAME in one Python process can make either native binding unavailable. The same symbol failure is tracked in the [OR-Tools upstream issue](https://github.com/google/or-tools/issues/5246); its bundled version is listed in the [official 9.15 release](https://github.com/google/or-tools/releases/tag/v9.15).

This project reproduced the failure with its frozen CVXPY 1.9.2 / highspy 1.15.1 / OR-Tools 9.15.6755 profile in fresh processes. CVXPY 1.8.2 alone did not fix it. The compatible prepared profile uses CVXPY 1.7.5 and highspy 1.12.0 with the existing OR-Tools version, within the project's declared minimum CVXPY 1.7 requirement. Both import orders then execute real CP-SAT and CVXPY/HiGHS binary integer models. PriceOps retains its HiGHS MIP selection; this is not a synthetic response or a solver fallback.

`tests/integration/test_native_solver_compatibility.py` runs each order in a fresh process, verifies HiGHS availability and checks the actual solver status, objective and solver name. The broader canonical wiring and production solver tests exercise real business models. The SBOM and OSS NOTICE are regenerated from the final lock/installed profile, with no human exemption or approval edited.

CVXPY's optional GLOP/PDLP interfaces report that OR-Tools 9.15 is outside their supported range. The production NetPlan executor uses OR-Tools CP-SAT and PriceOps retains CVXPY/HiGHS; this profile does not claim CVXPY GLOP/PDLP support. Do not suppress native-load errors or rely on a preferred import order. Any solver dependency upgrade must repeat both-order execution and the production business-model tests before changing the prepared profile.

This is implementation and synthetic verification evidence. Production model/source/PIT admission, independent review, the release-authority register and live runtime acceptance are separate requirements and remain mandatory. No production dependency deployment or release approval is performed by this preparation.
