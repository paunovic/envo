import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from envo import cli, config


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list:
    calls = []

    def fake_execvpe(program: str, argv: list[str], env: dict) -> None:
        calls.append((program, argv, env))
        # execvpe replaces the process on success; in tests it raises
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_execvpe)
    return calls


@pytest.fixture
def no_user_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("envo.config.CONFIG_PATH", tmp_path / "config.toml")


@pytest.fixture
def aws_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Path:
    config = tmp_path / "aws-config"
    config.write_text(
        "[profile qa]\n"
        "region=us-east-1\n"
        "aws_access_key_id = AKIA-qa-key\n"
        "aws_secret_access_key = qa-secret\n"
        "\n"
        "[profile staging]\n"
        "aws_access_key_id = AKIA-staging-key\n"
        "aws_secret_access_key = staging-secret\n",
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    return config


def write_user_config(monkeypatch: pytest.MonkeyPatch, tmp_path, table: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(table)
    monkeypatch.setattr("envo.config.CONFIG_PATH", config)


def run_argv(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    monkeypatch.setattr(sys, "argv", ["envo", *arguments])
    try:
        return cli.main()
    except SystemExit as exit:
        return int(exit.code)


def test_a_remote_run_injects_credentials_through_the_environment(
    monkeypatch, tmp_path, executed, no_user_config, aws_config
):
    exit_code = run_argv(monkeypatch, "qa", "seed", "app", "--yes")

    assert exit_code == 0
    program, argv, env = executed[0]
    assert program == "seed"
    assert argv == ["seed", "app", "--yes"]
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA-qa-key"
    assert env["AWS_SECRET_ACCESS_KEY"] == "qa-secret"
    assert env["ENVO_ENVIRONMENT"] == "qa"


def test_credentials_never_appear_in_the_command_argv(
    monkeypatch, tmp_path, executed, no_user_config, aws_config
):
    run_argv(monkeypatch, "qa", "seed", "app")

    _, argv, _ = executed[0]
    assert not any(
        "AKIA" in part or "secret" in part or "=" in part and part.startswith(("AWS_", "SQUAD_"))
        for part in argv
    )


def test_a_configured_profile_maps_to_its_own_credentials(
    monkeypatch, tmp_path, executed, no_user_config, aws_config
):
    write_user_config(
        monkeypatch,
        tmp_path,
        "[environments.qa]\nprofile = \"staging\"\n",
    )

    run_argv(monkeypatch, "qa", "forge", "deploy")

    _, _, env = executed[0]
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA-staging-key"
    assert env["ENVO_ENVIRONMENT"] == "qa"


def test_a_missing_profile_names_itself(
    monkeypatch, tmp_path, capsys, no_user_config, aws_config
):
    exit_code = run_argv(monkeypatch, "production", "forge", "deploy")

    assert exit_code == 1
    assert "no profile 'production'" in capsys.readouterr().err


def test_localhost_runs_the_command_with_only_the_environment(
    monkeypatch, tmp_path, executed, no_user_config
):
    exit_code = run_argv(monkeypatch, "localhost", "forge", "smoke")

    assert exit_code == 0
    program, argv, env = executed[0]
    assert program == "forge"
    assert argv == ["forge", "smoke"]
    assert env["ENVO_ENVIRONMENT"] == "localhost"


def test_repo_declared_vars_materialize_under_the_credentials(
    monkeypatch, tmp_path, executed, no_user_config, aws_config
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.envo.environments.qa.vars]\n"
        'SQUAD_APP_DATABASE_URL = "/database/app/url/master"\n',
    )
    monkeypatch.chdir(repo)

    resolution_envs: list[dict] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        resolution_envs.append(kwargs["env"])
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "Parameters": [
                        {
                            "Name": "/database/app/url/master",
                            "Value": "postgresql+psycopg://app:secret@qa-db/app",
                        }
                    ],
                    "InvalidParameters": [],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = run_argv(monkeypatch, "qa", "seed", "app")

    assert exit_code == 0
    assert resolution_envs[0]["AWS_ACCESS_KEY_ID"] == "AKIA-qa-key"
    _, argv, env = executed[0]
    assert argv == ["seed", "app"]
    assert env["SQUAD_APP_DATABASE_URL"] == "postgresql+psycopg://app:secret@qa-db/app"


def test_a_failed_materialization_names_the_variable_and_parameter(
    monkeypatch, tmp_path, capsys, no_user_config, aws_config
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.envo.environments.qa.vars]\n"
        'SQUAD_APP_DATABASE_URL = "/database/missing"\n',
    )
    monkeypatch.chdir(repo)

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "Parameters": [],
                    "InvalidParameters": ["/database/missing"],
                }
            ),
            stderr="",
        ),
    )

    exit_code = run_argv(monkeypatch, "qa", "seed", "app")

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "SQUAD_APP_DATABASE_URL" in stderr
    assert "/database/missing" in stderr


def test_declared_vars_resolve_in_one_bulk_call(
    monkeypatch, tmp_path, executed, no_user_config, aws_config
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.envo.environments.qa.vars]\n"
        'SQUAD_APP_DATABASE_URL = "/database/app/url/master"\n'
        'SQUAD_API_TOKEN = "/service/api/token"\n',
    )
    monkeypatch.chdir(repo)

    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "Parameters": [
                        {
                            "Name": "/database/app/url/master",
                            "Value": "postgresql://app@qa-db/app",
                        },
                        {
                            "Name": "/service/api/token",
                            "Value": "token-value",
                        },
                    ],
                    "InvalidParameters": [],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = run_argv(monkeypatch, "qa", "seed", "app")

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][:6] == [
        "aws",
        "ssm",
        "get-parameters",
        "--names",
        "/database/app/url/master",
        "/service/api/token",
    ]
    assert "--with-decryption" in calls[0]
    _, _, env = executed[0]
    assert env["SQUAD_APP_DATABASE_URL"] == "postgresql://app@qa-db/app"
    assert env["SQUAD_API_TOKEN"] == "token-value"


def test_more_than_ten_parameters_split_across_bulk_calls(
    monkeypatch, tmp_path, executed, no_user_config, aws_config
):
    repo = tmp_path / "repo"
    repo.mkdir()
    declarations = []
    for index in range(12):
        declarations.append(f'VAR_{index:02d} = "/bulk/param-{index:02d}"')
    (repo / "pyproject.toml").write_text(
        "[tool.envo.environments.qa.vars]\n" + "\n".join(declarations) + "\n",
    )
    monkeypatch.chdir(repo)

    chunks: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        start = argv.index("--names") + 1
        end = argv.index("--with-decryption")
        names = argv[start:end]
        chunks.append(names)
        entries = []
        for name in names:
            entries.append({"Name": name, "Value": f"value-{name}"})
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {"Parameters": entries, "InvalidParameters": []}
            ),
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = run_argv(monkeypatch, "qa", "true")

    assert exit_code == 0
    assert [len(chunk) for chunk in chunks] == [10, 2]


def test_eval_prints_the_environment_as_exports(
    monkeypatch, tmp_path, capsys, no_user_config, aws_config
):
    exit_code = run_argv(monkeypatch, "eval", "qa")

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "export AWS_ACCESS_KEY_ID=AKIA-qa-key" in out
    assert "export AWS_SECRET_ACCESS_KEY=qa-secret" in out
    assert "export ENVO_ENVIRONMENT=qa" in out


def test_a_missing_command_is_a_usage_error(
    monkeypatch, tmp_path, capsys, no_user_config
):
    exit_code = run_argv(monkeypatch, "qa")

    assert exit_code == 2
    assert "a command must follow" in capsys.readouterr().err


def test_the_local_spelling_is_rejected_with_a_hint(
    monkeypatch, tmp_path, capsys, no_user_config
):
    exit_code = run_argv(monkeypatch, "local", "forge", "smoke")

    assert exit_code == 2
    assert "localhost" in capsys.readouterr().err


def test_config_execs_the_editor_on_the_config_file(
    monkeypatch, tmp_path, executed, no_user_config
):
    monkeypatch.setenv("EDITOR", "an-editor")
    config = tmp_path / "config.toml"
    monkeypatch.setattr("envo.config.CONFIG_PATH", config)

    exit_code = run_argv(monkeypatch, "config")

    assert exit_code == 0
    assert config.is_file()
    assert executed[0][0] == "an-editor"
    assert executed[0][1] == ["an-editor", str(config)]



def test_shared_vars_apply_to_every_environment(
    monkeypatch, tmp_path, executed, no_user_config, aws_config
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.envo.vars]\n"
        'SQUAD_APP_DATABASE_URL = "/database/app/url/master"\n',
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "Parameters": [
                        {
                            "Name": "/database/app/url/master",
                            "Value": "postgresql://shared",
                        }
                    ],
                    "InvalidParameters": [],
                }
            ),
            stderr="",
        ),
    )

    for environment in ("qa", "staging"):
        exit_code = run_argv(monkeypatch, environment, "seed", "app")
        assert exit_code == 0

    assert executed[0][2]["SQUAD_APP_DATABASE_URL"] == "postgresql://shared"
    assert executed[1][2]["SQUAD_APP_DATABASE_URL"] == "postgresql://shared"


def test_an_environment_overrides_a_shared_var(
    monkeypatch, tmp_path, executed, no_user_config, aws_config
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.envo.vars]\n"
        'SQUAD_APP_DATABASE_URL = "/database/app/url/master"\n'
        "[tool.envo.environments.staging.vars]\n"
        'SQUAD_APP_DATABASE_URL = "/database/staging/url/master"\n',
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "Parameters": [
                        {
                            "Name": "/database/staging/url/master",
                            "Value": "postgresql://overridden",
                        }
                    ],
                    "InvalidParameters": [],
                }
            ),
            stderr="",
        ),
    )

    run_argv(monkeypatch, "staging", "seed", "app")

    assert executed[0][2]["SQUAD_APP_DATABASE_URL"] == "postgresql://overridden"



@pytest.fixture
def sso_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Path:
    config = tmp_path / "aws-config-sso"
    config.write_text(
        "[profile ssoqa]\n"
        "sso_session = org\n"
        "sso_account_id = 123456789012\n"
        "sso_role_name = admin\n"
        "region = us-east-1\n"
        "\n"
        "[sso-session org]\n"
        "sso_start_url = https://org.awsapps.com/start\n"
        "sso_region = us-east-1\n",
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    return config


@pytest.fixture
def chain_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Path:
    config = tmp_path / "aws-config-chain"
    config.write_text(
        "[profile chained]\n"
        "source_profile = qa\n"
        "role_arn = arn:aws:iam::123456789012:role/deploy\n"
        "region = us-east-1\n",
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    return config


def fake_cli(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[tuple[str, subprocess.CompletedProcess]],
) -> list[list[str]]:
    """
    Script the aws cli: each response is (argv fragment, outcome);
    calls consume responses in order and every argv is logged.

    """
    log: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        log.append(argv)
        fragment, outcome = responses.pop(0)
        assert fragment in argv, f"unexpected call: {argv}"
        return outcome

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return log


def ok_export(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["aws"], 0, stdout=stdout, stderr="")


def failed_export(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["aws"], 254, stdout="", stderr=stderr)


def test_a_non_static_profile_resolves_through_the_cli(
    monkeypatch, tmp_path, executed, no_user_config, chain_config
):
    log = fake_cli(
        monkeypatch,
        [
            (
                "export-credentials",
                ok_export(
                    "AWS_ACCESS_KEY_ID=ASIA-chain\n"
                    "AWS_SECRET_ACCESS_KEY=chain-secret\n"
                    "AWS_SESSION_TOKEN=chain-token\n",
                ),
            ),
        ],
    )

    exit_code = run_argv(monkeypatch, "chained", "forge", "deploy")

    assert exit_code == 0
    assert ["aws", "configure", "export-credentials", "--profile", "chained", "--format", "env-no-export"] in log
    _, _, env = executed[0]
    assert env["AWS_ACCESS_KEY_ID"] == "ASIA-chain"
    assert env["AWS_SECRET_ACCESS_KEY"] == "chain-secret"
    assert env["AWS_SESSION_TOKEN"] == "chain-token"


def test_an_expired_sso_token_logs_in_then_resolves(
    monkeypatch, tmp_path, executed, no_user_config, sso_config
):
    log = fake_cli(
        monkeypatch,
        [
            ("export-credentials", failed_export("token has expired")),
            ("sso", ok_export("")),
            (
                "export-credentials",
                ok_export(
                    "export AWS_ACCESS_KEY_ID=ASIA-fresh\n"
                    "AWS_SECRET_ACCESS_KEY=fresh-secret\n"
                    "AWS_SESSION_TOKEN=fresh-token\n",
                ),
            ),
        ],
    )

    exit_code = run_argv(monkeypatch, "ssoqa", "pytest")

    assert exit_code == 0
    assert ["aws", "sso", "login", "--profile", "ssoqa"] in log
    exports = [argv for argv in log if "export-credentials" in argv]
    assert len(exports) == 2
    _, _, env = executed[0]
    assert env["AWS_ACCESS_KEY_ID"] == "ASIA-fresh"
    assert env["AWS_SESSION_TOKEN"] == "fresh-token"


def test_a_non_sso_failure_surfaces_without_login(
    monkeypatch, tmp_path, capsys, no_user_config, chain_config
):
    log = fake_cli(
        monkeypatch,
        [("export-credentials", failed_export("source profile has no credentials"))],
    )

    exit_code = run_argv(monkeypatch, "chained", "forge", "deploy")

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "resolving credentials for profile 'chained' failed" in stderr
    assert not any("login" in argv for argv in log)


def test_malformed_cli_output_names_the_profile(
    monkeypatch, tmp_path, capsys, no_user_config, chain_config
):
    fake_cli(monkeypatch, [("export-credentials", ok_export("nothing useful\n"))])

    exit_code = run_argv(monkeypatch, "chained", "forge", "deploy")

    assert exit_code == 1
    assert "no credentials for profile 'chained'" in capsys.readouterr().err


def test_refresh_forces_sso_login_then_verifies(
    monkeypatch, tmp_path, capsys, no_user_config, sso_config
):
    argvs: list[list[str]] = []
    envs: list[dict | None] = []
    responses = [
        ("sso", ok_export("")),
        (
            "export-credentials",
            ok_export(
                "AWS_ACCESS_KEY_ID=ASIA-fresh\n"
                "AWS_SECRET_ACCESS_KEY=fresh-secret\n"
                "AWS_SESSION_TOKEN=fresh-token\n",
            ),
        ),
        (
            "get-caller-identity",
            ok_export(
                '{"UserId": "AROA123456789012:marko",'
                ' "Account": "123456789012",'
                ' "Arn": "arn:aws:sts::123456789012:assumed-role/Admin/marko"}',
            ),
        ),
    ]

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        argvs.append(argv)
        envs.append(kwargs.get("env"))
        fragment, outcome = responses.pop(0)
        assert fragment in argv, f"unexpected call: {argv}"
        return outcome

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = run_argv(monkeypatch, "refresh", "ssoqa")

    out = capsys.readouterr().out
    assert exit_code == 0
    login = ["aws", "sso", "login", "--profile", "ssoqa"]
    verify = ["aws", "sts", "get-caller-identity", "--output", "json"]
    assert login in argvs
    assert verify in argvs
    assert argvs.index(login) < argvs.index(verify)

    sts_env = envs[argvs.index(verify)]
    assert sts_env is not None
    assert sts_env["AWS_ACCESS_KEY_ID"] == "ASIA-fresh"
    assert sts_env["AWS_SESSION_TOKEN"] == "fresh-token"
    assert "account 123456789012" in out
    assert "AROA123456789012:marko" in out


def test_refresh_verifies_a_static_profile_without_login(
    monkeypatch, tmp_path, capsys, no_user_config, aws_config
):
    log = fake_cli(
        monkeypatch,
        [
            (
                "get-caller-identity",
                ok_export(
                    '{"UserId": "AKIA-qa-key",'
                    ' "Account": "123456789012",'
                    ' "Arn": "arn:aws:iam::123456789012:user/marko"}',
                ),
            ),
        ],
    )

    exit_code = run_argv(monkeypatch, "refresh", "qa")

    out = capsys.readouterr().out
    assert exit_code == 0
    assert ["aws", "sts", "get-caller-identity", "--output", "json"] in log
    assert not any("login" in argv for argv in log)
    assert "account 123456789012" in out
    assert "nothing else to refresh" in out


def test_a_failed_verification_names_the_profile(
    monkeypatch, tmp_path, capsys, no_user_config, aws_config
):
    fake_cli(
        monkeypatch,
        [
            (
                "get-caller-identity",
                failed_export("the security token included in the request is invalid"),
            ),
        ],
    )

    exit_code = run_argv(monkeypatch, "refresh", "qa")

    assert exit_code == 1
    assert "verifying credentials for profile 'qa' failed" in capsys.readouterr().err
