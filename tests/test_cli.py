from app.cli import main


def test_json_only_cli(capsys) -> None:
    exit_code = main(["diagnose", "为什么 CPU 很高？", "--json-only"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "为什么 CPU 很高？" in captured.out
