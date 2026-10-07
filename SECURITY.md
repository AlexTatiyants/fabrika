# Security

Fabrika runs model agents that write code, and runs that code. Its security
model is built around one rule: nothing an agent does, or anything an agent
wrote, runs on the machine Fabrika runs on. This page says what that covers,
what it does not, and how to report a problem.

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
the **Report a vulnerability** button on this repository's **Security** tab.
Do not open a public issue for one.

Include what you did, what happened, and what you expected, with the
version or commit you ran. You will get an acknowledgement within a week, and
a fix or an explanation of why it is not one as soon as it is understood.

Only the latest commit on `main` is supported.

## What Fabrika protects

- **Agents run in sealed containers.** Every agent session and every command
  a check runs happens in a Docker container on an `--internal` network: no
  route to the host, to another feature's containers, or to the internet. See
  [docs/isolation.md](docs/isolation.md).
- **The way out is a proxy.** What genuinely needs the internet -- a package
  install, an agent calling its model provider -- goes through Fabrika's egress
  proxy, which forwards to public addresses and refuses private ones, including
  the host and anything listening on it.
- **A build never touches your working copy.** Each feature is built in its
  own git worktree on its own branch, and what you get back is a branch to
  review. The only files Fabrika writes into your repository are ones it
  proposed and you accepted -- a guide, test scaffolding, a Dockerfile -- each
  shown to you first.
- **Credentials stay on disk, private.** A provider key entered in the console
  is stored in `.factory-credentials.json` beside the config, created with mode
  `0600` and ignored by git. It is never returned to the browser.

## What it does not protect

- **The console has no authentication.** Anyone who can reach its port can
  read your repositories' code and start runs that spend your provider budget.
  Bind it to `127.0.0.1` (the documented command does) and do not expose it to
  a network you do not trust. Putting it behind a reverse proxy with its own
  authentication is the only supported way to share it.
- **The code it writes is not reviewed for you.** The review packet is an
  argument that the code does what was specified. It is not a security audit,
  and a branch should be reviewed like any other contribution before it is
  merged.
- **Model providers see what agents send them.** Prompts include the code an
  agent is working on. Use a provider and an account whose data terms you
  accept for that code.
- **Docker is the boundary.** Isolation is only as strong as the container
  runtime underneath it. Keep Docker up to date.
