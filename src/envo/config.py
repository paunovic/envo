"""
Configuration reading for envo: the user's envo config, aws profile
credentials, and the repo's declared materialization variables.

"""

import configparser
import os
import subprocess
import tomllib
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "envo" / "config.toml"


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
