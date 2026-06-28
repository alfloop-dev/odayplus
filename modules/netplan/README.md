# NetPlan Module

NetPlan implements network scenario building, constrained optimization,
alternative plans, infeasibility diagnosis, approval lifecycle, execution, and
outcome tracking.

The module delegates numeric search to `solver.netplan` and owns the durable
scenario aggregate plus audit-bearing status history. The UI-facing summary
fields map to `NetPlanScenarioCard`: objective value, action counts,
budget usage, expected gross margin, risk, binding constraints, solver status,
alternative availability, and approval status.
