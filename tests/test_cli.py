from opsmitra.cli import build_parser


def test_cli_exposes_version_and_run_command():
    parser = build_parser()

    parsed = parser.parse_args(["run", "--source", "local", "--window-minutes", "60", "--dry-run"])

    assert parsed.command == "run"
    assert parsed.source == "local"
    assert parsed.window_minutes == 60
    assert parsed.dry_run is True
