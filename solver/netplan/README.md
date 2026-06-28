# NetPlan Solver

Network planning optimization models and scenario diagnostics.

The first implementation is CP-SAT-compatible without taking an OR-Tools
runtime dependency: it enumerates the discrete action domain for each planning
entity, applies hard constraints before scoring, ranks the feasible portfolios,
and returns the same operational fields expected from a CP-SAT run: solver
status, objective value, binding constraints, alternatives, and infeasibility
diagnostics.
