from eval.run_eval import DEFAULT_CASES_PATH, DEFAULT_FIXTURES_DIR, aggregate, evaluate_case, load_cases


def test_fixture_eval_cases_pass() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    results = [evaluate_case(case, DEFAULT_FIXTURES_DIR) for case in cases]
    overall = aggregate(results)

    assert overall["cases"] == len(cases)
    assert overall["passed"] == len(cases)
    assert overall["pass_rate"] == 1.0
    assert overall["finding_recall"] == 1.0
    assert overall["approval_match_rate"] == 1.0


def test_fixture_files_exist() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    for case in cases:
        assert (DEFAULT_FIXTURES_DIR / case["fixture"]).exists()
