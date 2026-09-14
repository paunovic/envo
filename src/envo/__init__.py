"""
Run a command envoing another environment's identity.

The first argument names the environment, everything after it is the
command, verbatim. A non-local environment resolves its profile from
the user's envo config, reads the profile's static credentials from
~/.aws/config, materializes any repo-declared ssm vars, and runs the
command with ENVO_ENVIRONMENT set; localhost runs it directly.
Injected values ride the environment, never the argv - ps must not
see credentials.

"""

import configparser
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "envo" / "config.toml"

USAGE = """usage: envo <environment> <command> [args...]
       envo eval <environment>
       envo config"""


def configured_profiles() -> dict[str, str]:
    """
    Map environment names to aws profiles from the user's config.

    A missing config or a missing profile entry means the profile
    shares the environment's name - the common case stays zero-config.

    """
    if not CONFIG_PATH.is_file():
        return {}

    try:
        document = tomllib.loads(CONFIG_PATH.read_text())
    except tomllib.TOMLDecodeError:
        return {}

    declared = document.get("environments", {})
    if not isinstance(declared, dict):
        return {}

    mapping: dict[str, str] = {}
    for name, entry in declared.items():
        profile = entry.get("profile", name) if isinstance(entry, dict) else name
        mapping[name] = profile

    return mapping


def aws_config_path() -> Path:
    """
    Locate the aws config file, honoring its own override variable.

    """
    override = os.environ.get("AWS_CONFIG_FILE")
    if override:
        return Path(override)

    return Path.home() / ".aws" / "config"


def _parsed_profile(profile: str) -> configparser.SectionProxy:
    config = configparser.ConfigParser(interpolation=None)
    config.read(aws_config_path())

    section_name = f"profile {profile}"
    if not config.has_section(section_name):
        raise RuntimeError(f"no profile {profile!r} in {aws_config_path()}")

    return config[section_name]


def exported_credentials(profile: str) -> dict[str, str]:
    """
    Resolve a profile's credentials through the aws cli.

    Covers every shape envo does not parse itself - sso sessions,
    role chains, credential processes - reusing the cli's own token
    cache and refresh.

    """
    result = subprocess.run(
        [
            "aws",
            "configure",
            "export-credentials",
            "--profile",
            profile,
            "--format",
            "env-no-export",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"resolving credentials for profile {profile!r} failed:"
            f" {result.stderr.strip()}",
        )

    credentials: dict[str, str] = {}
    for line in result.stdout.splitlines():
        entry = line.removeprefix("export ").strip()
        key, separator, value = entry.partition("=")
        if separator and key.startswith("AWS_"):
            credentials[key] = value

    if "AWS_ACCESS_KEY_ID" not in credentials or "AWS_SECRET_ACCESS_KEY" not in credentials:
        raise RuntimeError(
            f"the aws cli returned no credentials for profile {profile!r}",
        )

    return credentials


def profile_credentials(profile: str) -> dict[str, str]:
    """
    Read a profile's credentials: static keys from the aws config
    directly, anything else through the aws cli's resolution.

    An sso profile whose token expired gets the cli's device-code
    login first - it prints the authorization url to this console
    and waits for the browser.

    """
    section = _parsed_profile(profile)

    access_key = section.get("aws_access_key_id", fallback=None)
    secret_key = section.get("aws_secret_access_key", fallback=None)
    if access_key is not None and secret_key is not None:
        return {
            "AWS_ACCESS_KEY_ID": access_key,
            "AWS_SECRET_ACCESS_KEY": secret_key,
        }

    try:
        return exported_credentials(profile)
    except RuntimeError:
        if not any(key.startswith("sso_") for key in section.keys()):
            raise

    login = subprocess.run(
        ["aws", "sso", "login", "--profile", profile],
        check=False,
    )
    if login.returncode != 0:
        raise RuntimeError(f"aws sso login for profile {profile!r} failed")

    return exported_credentials(profile)


def repo_variables(environment: str) -> dict[str, str]:
    """
    Collect the env vars the nearest repo declares, each naming the
    ssm parameter that holds its value.

    [tool.envo.vars] applies to every environment - most parameters
    live at the same path everywhere - and an environment's own
    [tool.envo.environments.<env>.vars] entry overrides or extends it.

    """
    directory = Path.cwd().resolve()
    while True:
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                document = tomllib.loads(pyproject.read_text())
            except tomllib.TOMLDecodeError:
                document = {}

            envo = document.get("tool", {}).get("envo", {})
            if not isinstance(envo, dict):
                envo = {}

            shared = envo.get("vars", {})
            declared = envo.get("environments", {})
            overrides = {}
            if isinstance(declared, dict):
                entry = declared.get(environment)
                if isinstance(entry, dict):
                    overrides = entry.get("vars", {})

            if isinstance(shared, dict) or isinstance(overrides, dict):
                merged = {**shared, **overrides}
                return {str(k): str(v) for k, v in merged.items()}

        parent = directory.parent
        if parent == directory:
            return {}
        directory = parent


def materialize(
    credentials: dict[str, str],
    variables: dict[str, str],
) -> dict[str, str]:
    """
    Resolve each declared var's ssm parameter under the environment's
    own credentials.

    """
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
    """
    Build the env vars a run under environment injects.

    """
    if environment == "localhost":
        return {"ENVO_ENVIRONMENT": "localhost"}

    profile = configured_profiles().get(environment, environment)
    credentials = profile_credentials(profile)
    resolved = materialize(credentials, repo_variables(environment))
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
    """
    Emit the environment as shell exports, for prompts.

    """
    if environment == "localhost":
        print(f"export ENVO_ENVIRONMENT={environment}")
        return 0

    for key, value in environment_for(environment).items():
        print(f"export {key}={shlex.quote(value)}")

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
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.touch(exist_ok=True)
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        return run_argv([editor, str(CONFIG_PATH)])

    if environment == "eval":
        if not command:
            print("envo: eval needs an environment", file=sys.stderr)
            return 2
        try:
            return print_eval(command[0])
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
