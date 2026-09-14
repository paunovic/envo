"""
Run a command under another environment's identity.

The first argument names the environment, everything after it is the
command, verbatim. A non-local environment resolves its profile from
the user's envo config, reads the profile's credentials from
~/.aws/config (falling back to the aws cli for sso and role
chains), materializes any repo-declared ssm vars, and runs the
command with ENVO_ENVIRONMENT set; localhost runs it directly.

"""

from envo.cli import main

__all__ = ["main"]
