from jobdigest.cli import build_parser


def test_cli_parser_has_contacts_flags():
    parser = build_parser()
    args = parser.parse_args(
        ["contacts", "--db", "data/jobs.db", "--limit", "3", "--job-id", "9", "--job-id", "12"]
    )
    assert args.command == "contacts"
    assert args.db == "data/jobs.db"
    assert args.limit == 3
    assert args.job_id == [9, 12]


def test_cli_parser_contacts_defaults():
    parser = build_parser()
    args = parser.parse_args(["contacts"])
    assert args.command == "contacts"
    assert args.db is None
    assert args.limit is None
    assert args.job_id is None
