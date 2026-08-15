from models.shared_ml import OptunaSearchRunner, ParameterSpec


def test_optuna_finds_a_better_parameter_and_records_trials() -> None:
    result = OptunaSearchRunner().run(
        objective=lambda params: (float(params["depth"]) - 4.0) ** 2,
        search_space=(ParameterSpec(name="depth", kind="int", low=1, high=8),),
        n_trials=20,
        study_name="depth-search",
        seed=7,
    )

    assert result.engine == "optuna"
    assert result.best_value <= 1.0
    assert abs(result.best_params["depth"] - 4) <= 1
    assert len(result.trials) == 20
