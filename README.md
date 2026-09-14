# envo

envo runs a command wearing another environment's identity: the aws
profile's credentials are resolved, the repo's declared ssm
parameters materialize, and everything reaches the child through
its environment - never its argv, so `ps` never sees a key. The
first argument names the environment, everything after it is the
command, verbatim - the `env`/`timeout` convention.

## Install

```
uv tool install git+https://github.com/paunovic/envo
```

## Usage

```
envo <environment> <command> [args...]
envo eval <environment>
envo refresh <environment>
envo config
```

No arguments or `-h`/`--help` prints this usage.

## What envo needs

An aws profile for the environment, in `~/.aws/config`
(`AWS_CONFIG_FILE` honored) - a static key pair, an sso session, a
role chain all work. The profile name defaults to the environment's
name, so matching names stay zero-config; a mapping in
`~/.config/envo/config.toml` renames it:

```toml
[environments.qa]
profile = "marko-qa"
```

A repo declares the env vars envo materializes, each naming the ssm
parameter that holds its value - organizational truth, unlike
profiles. The shared table applies to every environment, since most
parameters live at the same path everywhere:

```toml
[tool.envo.vars]
SQUAD_APP_DATABASE_URL = "/database/app/url/master"
```

An environment overrides or extends with its own table:

```toml
[tool.envo.environments.staging.vars]
SQUAD_APP_DATABASE_URL = "/database/staging/url/master"
```

## How envo runs a command

```
$ envo qa seed app --yes
```

envo resolves the profile's credentials, fetches the declared
parameters with `aws ssm get-parameters` in batches of ten under
the environment's own credentials - the cli charges one boot per
call, so one call per batch beats one per var - sets
`ENVO_ENVIRONMENT=qa`, and execs the command with everything merged
into its environment. The process is replaced, not wrapped: the
child is the command, with no envo in between. A parameter the
environment does not have names its variable and fails the run.

`envo localhost <command>` skips all resolution and runs with only
`ENVO_ENVIRONMENT` set - direnv owns local. The `local` spelling is
rejected with a hint toward `localhost`.

## How credentials resolve

A static key pair (`aws_access_key_id` and `aws_secret_access_key`)
is read straight from the aws config. Anything else goes through
the cli's own resolution, `aws configure export-credentials`, which
covers sso sessions, role chains and credential processes with its
token cache. An sso profile whose token expired gets
`aws sso login` first - the cli prints the authorization url to
this console and waits for the browser - then the export retries.

## Pre-warming with envo refresh

```
$ envo refresh qa
```

An sso profile logs in before anything can fail - proactive, so an
expired token never trips a real command - and the resolved
credentials are then proven with `aws sts get-caller-identity`,
which prints the account and user id. Static-key profiles skip the
login and only verify; their rotation stays manual in the config
file.

## Exports for prompts

```
$ envo eval qa
export AWS_ACCESS_KEY_ID=AKIA-qa-key
```

`eval` prints the same set a run would inject as shell exports, for
prompt integration; `envo eval localhost` prints only the
`ENVO_ENVIRONMENT` export.

## Editing the mapping

`envo config` opens `~/.config/envo/config.toml` in `$EDITOR`
(falling back to `$VISUAL`, then vi), creating the file first on a
fresh machine.

## Development

```
uv sync
uv run ruff check src tests
uv run ty check src
uv run pytest tests
```
