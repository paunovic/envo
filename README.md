# envo

Run a command envoing another environment's identity — credentials
resolved per profile, naming via `ENVO_ENVIRONMENT`.

```
envo qa seed app --yes
envo localhost forge smoke
envo refresh qa
envo config
```

`envo config` opens the config file itself in `$EDITOR` (falling back
to `$VISUAL`, then vi), creating it first on a fresh machine.

## credentials

envo reads a profile's static credentials (`aws_access_key_id` and
`aws_secret_access_key`) from `~/.aws/config` - honoring
`AWS_CONFIG_FILE` - and injects them through the child's environment,
never its argv: `ps` must not see keys. `envo eval <environment>`
prints the same set as shell exports for prompt integration.

A profile without static keys resolves through the aws cli
(`aws configure export-credentials`), which covers sso sessions, role
chains and credential processes with its own token cache. An sso
profile whose token expired gets `aws sso login` first - the cli
prints the authorization url to the console and waits for the browser
- then the export retries.

`envo refresh <environment>` pre-warms and verifies credentials
outside a command run: an sso profile gets `aws sso login` first -
proactive, so an expired token never trips a real command - then the
resolved credentials are proven with `aws sts get-caller-identity`,
which prints the account and user id. Static-key profiles skip the
login and only verify; their rotation stays manual in the config
file.

The first argument is the environment, everything after it is the
command, verbatim - the `env`/`timeout` convention.

An environment's aws profile comes from your own config at
`~/.config/envo/config.toml` - a per-developer fact that never
touches the repo:

```toml
[environments.qa]
profile = "marko-qa"
```

No entry means the profile shares the environment's name, so matching
names stay zero-config.

A repo declares the env vars envo materializes, each naming the
ssm parameter that holds its value - organizational truth, unlike
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

`envo qa <command>` resolves each parameter under the environment's
own credentials and hands the command the resolved values in its
environment. `envo localhost` never materializes - direnv owns local.

## install

```
uv tool install git+https://github.com/paunovic/envo
```
