from cli import build_parser, render


def test_default_greeting():
    assert render(build_parser().parse_args(["ada"])) == "Hello, ada!"


def test_upper_and_repeat():
    args = build_parser().parse_args(["ada", "--upper", "--repeat", "2"])
    assert render(args) == "HELLO, ADA!\nHELLO, ADA!"


def test_readme_has_an_options_section():
    assert "## Options" in open("README.md", encoding="utf-8").read()
