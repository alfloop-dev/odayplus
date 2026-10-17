"""Exercise real CP-SAT and CVXPY/HiGHS models in both library import orders."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize("first", ["cvxpy", "ortools"])
def test_native_solvers_coexist_and_solve_mixed_integer_models(first):
    imports = (
        "import cvxpy as cp; from ortools.sat.python import cp_model"
        if first == "cvxpy" else
        "from ortools.sat.python import cp_model; import cvxpy as cp"
    )
    code = imports + """
import json
model = cp_model.CpModel()
choice = model.new_bool_var('choice')
model.maximize(choice)
solver = cp_model.CpSolver()
status = solver.solve(model)
assert status == cp_model.OPTIMAL
assert cp.HIGHS in cp.installed_solvers()
x = cp.Variable(boolean=True)
problem = cp.Problem(cp.Minimize(x), [x >= 1])
problem.solve(solver=cp.HIGHS)
print(json.dumps({'cp_sat_status': solver.status_name(status), 'cp_sat_value': solver.value(choice),
                  'cvxpy_status': problem.status, 'cvxpy_value': problem.value,
                  'cvxpy_solver': problem.solver_stats.solver_name}))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "cp_sat_status": "OPTIMAL", "cp_sat_value": 1,
        "cvxpy_status": "optimal", "cvxpy_value": 1.0, "cvxpy_solver": "HIGHS",
    }
