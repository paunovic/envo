"""
Run a command under another environment.

First argument is the environment, the rest is the command. A
non-local environment gets profile credentials from the envo config
and ~/.aws/config (aws cli for sso and role chains) plus any
repo-declared ssm vars. localhost runs the command as-is.

"""

from envo.cli import main

__all__ = ["main"]
