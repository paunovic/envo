# envo

envo runs a command under another aws environment: the profile
credentials get resolved, the ssm parameters a repo declares become
env vars, and the command runs with all of it injected. The first
argument names the environment, everything after it is the command.

```
$ envo qa aws s3 ls
```

## Install

```
uv tool install git+https://github.com/paunovic/envo
```

## Profiles

The profile defaults to the environment name. A different one goes
in `~/.config/envo/config.toml`:

```toml
[environments.qa]
profile = "acme-qa"
```

`envo config` opens the config file in your editor, creating it
first on a fresh machine. Static keys come straight from
`~/.aws/config`. sso sessions, role chains and credential processes
go through the aws cli. An expired sso token gets the browser login.

## Declared vars

A repo declares the env vars envo fills in, each naming the ssm
parameter holding the value:

```toml
[tool.envo.vars]
APP_DATABASE_URL = "/database/app/url/master"
```

An environment overrides through its own
`[tool.envo.environments.<env>.vars]` table. `envo localhost` runs
with only `ENVO_ENVIRONMENT` set. direnv owns local.

`envo --no-vars <environment> <command...>` skips variable
resolution. `ENVO_ENVIRONMENT` is still set, credentials still
apply, nothing comes out of ssm.

## Refresh and eval

`envo refresh qa` logs an sso profile in first and verifies the
result, printing the account and user id. `envo eval qa` prints the
same set a run would inject, as shell exports for prompt
integration.

## Development

```
uv sync
uv run ruff check src tests
uv run ty check src
uv run pytest tests
```
