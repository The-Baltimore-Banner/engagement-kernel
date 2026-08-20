# Security policy

## Supported use

This repository is **pre-release and not yet published**. There is no release and
no supported deployment path, so there is nothing here to run in production
today. Reports are still welcome: it is cheaper to fix a design before the code
ships than after.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security report.

Use GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Include enough detail to reproduce and assess impact:
   * affected component or package
   * commit SHA if known
   * steps to reproduce
   * impact, and any mitigation you are aware of

This repository has no published security contact address yet. A monitored
address will be added here before the repository is made public. Until then,
private vulnerability reporting through the Security tab is the channel that
reaches the maintainers.

## Reporting exposed internal material

This repository is being assembled by extracting code from a private repository.
If you find anything that should not have been published -- a cloud account id, a
resource identifier, an internal hostname or endpoint, an internal ticket
reference, a personal name in test data, or a credential -- **report it the same
private way, and do not open a public issue.** Include the file and line. That
class of finding is treated as a security report, not a bug.

`tools/leak_scan.py` is the automated gate for those patterns and it runs on every
pull request, but it matches patterns and cannot recognise an internal document by
its nature. A human finding is a real finding.

## What to expect

* Acknowledgement when a maintainer can triage the report
* Coordination on disclosure timing when a fix is prepared
* Credit in the advisory when you want it, unless you ask to stay anonymous

## Non-security bugs

Use a normal issue for non-security defects, and see
[CONTRIBUTING.md](CONTRIBUTING.md) for the contribution process.
