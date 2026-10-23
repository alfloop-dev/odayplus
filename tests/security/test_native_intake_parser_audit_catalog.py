"""Bind the native parser's audit writers to the catalog without importing them."""

import ast
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml"


def _written_actions():
    paths = sorted((ROOT / "modules/listing/application").glob("native_intake_parser*.py"))
    assert paths, "The audit gate must read actual native parser sources"
    trees = [ast.parse(path.read_text(encoding="utf-8"), filename=str(path)) for path in paths]
    parents = {child: node for tree in trees for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)}
    calls = [node for tree in trees for node in ast.walk(tree) if isinstance(node, ast.Call)]
    audit_calls = [call for call in calls
        if isinstance(call.func, ast.Attribute) and call.func.attr == "_audit"]

    def values(expression, resolving=frozenset()):
        """Resolve only the writers' literals, branches, local bindings and wrapper arguments."""
        assert expression not in resolving, "Cyclic audit action binding"
        resolving = resolving | {expression}

        def combine(expressions):
            assert expressions, "Audit action binding has no values"
            return set().union(*(values(item, resolving) for item in expressions))

        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return {expression.value}
        if isinstance(expression, ast.IfExp):
            return combine([expression.body, expression.orelse])
        if isinstance(expression, ast.Name):
            scope = parents[expression]
            while not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(scope, ast.For) and isinstance(scope.target, (ast.Tuple, ast.List)):
                    indexes = [index for index, target in enumerate(scope.target.elts)
                        if isinstance(target, ast.Name) and target.id == expression.id]
                    if indexes:
                        assert len(indexes) == 1 and isinstance(scope.iter, (ast.Tuple, ast.List))
                        assert all(isinstance(row, (ast.Tuple, ast.List))
                            and len(row.elts) == len(scope.target.elts) for row in scope.iter.elts)
                        return combine([row.elts[indexes[0]] for row in scope.iter.elts])
                assert scope in parents, f"Unresolved audit action: {ast.unparse(expression)}"
                scope = parents[scope]
            arguments = [arg.arg for arg in (*scope.args.posonlyargs, *scope.args.args)]
            if expression.id in arguments:
                # The parser's shared writer forwards its action parameter unchanged.
                assert scope.name == "_audit" and arguments[0] == "self"
                position = arguments.index(expression.id) - 1
                assert position >= 0 and audit_calls, "Audit wrapper has no actual callers"
                expressions = []
                for call in audit_calls:
                    keywords = {kw.arg: kw.value for kw in call.keywords}
                    assert expression.id in keywords or len(call.args) > position
                    expressions.append(keywords[expression.id] if expression.id in keywords
                        else call.args[position])
                return combine(expressions)
            assignments = [node.value for node in ast.walk(scope)
                if isinstance(node, ast.Assign) and node.lineno < expression.lineno
                and any(isinstance(target, ast.Name) and target.id == expression.id
                    for target in node.targets)]
            assert len(assignments) == 1, f"Ambiguous audit action binding: {expression.id}"
            return combine(assignments)
        raise AssertionError(f"Unsupported audit action expression: {ast.unparse(expression)}")

    actions = set()
    for call in calls:
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "persist"
                and isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "audit_sink"):
            continue
        assert call.args and isinstance(call.args[0], ast.Dict), "Inspect the actual persisted audit record"
        record = call.args[0]
        assert all(isinstance(key, ast.Constant) and isinstance(key.value, str) for key in record.keys)
        expressions = [value for key, value in zip(record.keys, record.values, strict=True)
            if key.value == "action"]
        assert len(expressions) == 1, "Audit record must declare exactly one action"
        actions.update(values(expressions[0]))
    assert actions, "The audit gate must find actual native parser writes"
    return actions


def test_actual_native_parser_audit_writes_are_declared():
    addendum = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    base = yaml.safe_load((CATALOG.parent / addendum["extends"]).read_text(encoding="utf-8"))
    declared = Counter()
    catalog = {}
    for document in (base, addendum):
        declared.update(document.get("rules", {}).get("state_contract_labels", {}).get("audit_action_codes", []))
        for entry in (*document.get("catalog", []), *document.get("catalog_additions", [])):
            catalog[entry["event_type"]] = entry
    event_actions = {f"{event_type}.v{entry['event_version']}" for event_type, entry in catalog.items()}
    actions = _written_actions()
    missing = actions - (declared.keys() | event_actions)
    assert not missing, "Undeclared actual native parser audit actions: " + ", ".join(sorted(missing))
    duplicates = {action for action in actions if declared[action] > 1}
    assert not duplicates, "Duplicate native parser audit declarations: " + ", ".join(sorted(duplicates))
