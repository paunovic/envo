"""
The envo command line: build the environment a command runs in and
exec it with everything injected through the environment.

"""

import json
import os
import shlex
import subprocess
import sys

from envo import config

USAGE = """usage: envo <environment> <command> [args...]
       envo eval <environment>
       envo refresh <environment>
       envo config"""


def materialize(
    credentials: dict[str, str],
    variables: dict[str, str],
) -> dict[str, str]:
    # resolve each declared var's ssm parameter under the
    # environment's own credentials
    values: dict[str, str] = {}
    for variable, parameter in sorted(variables.items()):
        result = subprocess.run(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--name",
                parameter,
                "--with-decryption",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **credentials},
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"resolving {variable} from {parameter} failed:"
                f" {result.stderr.strip()}",
            )
        values[variable] = result.stdout.strip()

    return values


def environment_for(environment: str) -> dict[str, str]:
    # build the env vars a run under environment injects
    if environment == "localhost":
        return {"ENVO_ENVIRONMENT": "localhost"}

    profile = config.configured_profiles().get(environment, environment)
    credentials = config.profile_credentials(profile)
    resolved = materialize(credentials, config.repo_variables(environment))
    return {
        **credentials,
        **resolved,
        "ENVO_ENVIRONMENT": environment,
    }


def run_argv(argv: list[str], injected: dict[str, str] | None = None) -> int:
    try:
        os.execvpe(argv[0], argv, {**os.environ, **(injected or {})})
    except FileNotFoundError as error:
        print(f"envo: cannot run {argv[0]}: {error}", file=sys.stderr)
        return 127

    return 0


def print_eval(environment: str) -> int:
    # emit the environment as shell exports, for prompts
    if environment == "localhost":
        print(f"export ENVO_ENVIRONMENT={environment}")
        return 0

    for key, value in environment_for(environment).items():
        print(f"export {key}={shlex.quote(value)}")

    return 0


def refresh(environment: str) -> int:
    # pre-warm and verify an environment's credentials outside a
    # command run; an sso profile logs in first so an expired token
    # never trips a real command
    profile = config.configured_profiles().get(environment, environment)

    if config.is_sso_profile(profile):
        config.login_profile(profile)

    credentials = config.profile_credentials(profile)
    result = subprocess.run(
        [
            "aws",
            "sts",
            "get-caller-identity",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **credentials},
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"verifying credentials for profile {profile!r} failed:"
            f" {result.stderr.strip()}",
        )

    identity = json.loads(result.stdout)
    print(
        f"envo: {environment} verified:"
        f" account {identity['Account']}, user id {identity['UserId']}",
    )
    if config.has_static_keys(profile):
        print(
            "envo: nothing else to refresh - static keys"
            " rotate by editing the aws config",
        )

    return 0


def main() -> int:
    argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0

    environment = argv[0]
    command = argv[1:]

    if environment == "config":
        # an editor wants an existing file, and the config dir may not
        # exist on a fresh machine
        config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.CONFIG_PATH.touch(exist_ok=True)
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        return run_argv([editor, str(config.CONFIG_PATH)])

    if environment == "eval":
        if not command:
            print("envo: eval needs an environment", file=sys.stderr)
            return 2
        try:
            return print_eval(command[0])
        except RuntimeError as error:
            print(f"envo: {error}", file=sys.stderr)
            return 1

    if environment == "refresh":
        if not command:
            print("envo: refresh needs an environment", file=sys.stderr)
            return 2
        try:
            return refresh(command[0])
        except RuntimeError as error:
            print(f"envo: {error}", file=sys.stderr)
            return 1

    if not command:
        print(f"envo: a command must follow the environment\n{USAGE}", file=sys.stderr)
        return 2

    if environment == "local":
        print(
            "envo: the local environment spells itself localhost",
            file=sys.stderr,
        )
        return 2

    try:
        injected = environment_for(environment)
    except RuntimeError as error:
        print(f"envo: {error}", file=sys.stderr)
        return 1

    return run_argv(command, injected)
