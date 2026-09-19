# envo

Envo runs a command under another environment's identity: aws
credentials resolved per profile and the repo's declared ssm
parameters materialized into the child's environment.
The first argument names the environment, everything after it
is the command.

```
$ envo qa aws s3 ls
```

## Install

```
uv tool install git+https://github.com/paunovic/envo
```

## Profiles

The profile name defaults to the environment's name, so matching
names stay zero-config; a mapping in `~/.config/envo/config.toml`
renames it:

```toml
[environments.qa]
profile = "acme-qa"
```

`envo config` opens config file in the editor, creating it first
on a fresh machine. static keys are read from `~/.aws/config`;
sso sessions, role chains and credential processes resolve through
the aws cli - an expired sso token gets the browser login
automatically.

## Declared vars

A repo declares the env vars envo materializes, each naming the
ssm parameter that holds its value:

```toml
[tool.envo.vars]
APP_DATABASE_URL = "/database/app/url/master"
```

An environment overrides with its own
`[tool.envo.environments.<env>.vars]` table. `envo localhost` runs
with only `ENVO_ENVIRONMENT` set - direnv owns local.

`envo --no-vars <environment> <command...>` skips materialization
entirely: `ENVO_ENVIRONMENT` is still set and the profile's
credentials are configured, but the declared vars never reach ssm -
envo prints one notice to stderr so the leaner environment is
never silent.

## Refresh and eval

`envo refresh qa` logs an sso profile in first and verifies the
result, printing the account and user id.
`envo eval qa` prints the same set a run would inject, as shell
exports for prompt integration.

## Development

```
uv sync
uv run ruff check src tests
uv run ty check src
uv run pytest tests
```
