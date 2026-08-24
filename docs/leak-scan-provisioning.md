# Provisioning the leak scan's deny list

The leak gate has five rules. Four need no configuration. The fifth, `deny-name`,
needs the employee names it is looking for — and **those names are themselves the
confidential data**, so they are not in this repository and must never be added to
it.

This document says what the secret is called, what shape it holds, and how to
rebuild it. **It deliberately contains no name.** If you are looking for the
current contents, they are only in the secret; that is the design, not an omission.

It exists because a gate whose configuration only one person knows how to
reconstruct is a gate that quietly expires the first time that person is
unavailable.

---

## The secret

| | |
|---|---|
| **Name** | `LEAK_SCAN_DENY_TOML` |
| **Scope** | repository secret on `The-Baltimore-Banner/engagement-kernel` |
| **Format** | a TOML document with a `[deny]` table |
| **Read by** | the `leak-scan` job in `.github/workflows/ci.yml` |

### Shape

```toml
[deny]
names = ["Firstname Lastname", "Another Person"]
```

`hostnames` is also accepted and is **not** needed: the four hostname terms that
matter are already in the committed `tools/leak_scan.toml`, because a hostname is
not confidential in the way a name is. Anything listed here is *added* to the
committed config rather than replacing it.

### What to put in `names`

Match the forms that actually get pasted, not just the formal one. A name reaches
this repository by someone copying a file, a comment or a commit trailer across
from the private tree, so the useful entries are the spellings that appear there:
how people sign commits, how they are referred to in code comments, and any handle
used in place of a full name.

Terms are matched case-insensitively as bounded substrings. Short or common terms
will produce false positives on ordinary prose — prefer the full form.

---

## Setting or rotating it

Run this **in your own terminal**, not through an agent session and not through a
shell whose transcript is retained. The whole point is that the names do not end up
anywhere but the secret.

```bash
cat > /tmp/deny.toml <<'EOF'
[deny]
names = ["Firstname Lastname"]
EOF

gh secret set LEAK_SCAN_DENY_TOML \
  --repo The-Baltimore-Banner/engagement-kernel < /tmp/deny.toml

shred -u /tmp/deny.toml
```

Setting the secret replaces it wholesale — there is no append. To add one name,
rebuild the whole document.

### Confirming it worked

You cannot read a secret back, so confirm through the gate instead. Push any branch
and look at the `leak-scan` job:

```
leak-scan: OK no findings in N file(s) (config: ..., /home/runner/work/_temp/leak-scan-deny.toml)
leak-scan: deny terms loaded: 4 hostname term(s), 3 name term(s)
```

The second line is the confirmation. A non-zero name count means the rule has
terms and can fire. **If the secret is missing, empty or malformed the job fails**
with `the deny list loaded 0 name term(s)` — by design, and see below for why that
failure had to be built rather than inherited.

---

## Why the job asserts a term count

Until this was wired up, the `leak-scan` job ran with no deny file at all, so
`deny-name` compiled zero patterns and matched nothing — for months, reporting
green throughout.

Pointing the scanner at a secret does not close that on its own, because the
failure returns the moment the secret is unset or fumbled:

| Deny file | Names loaded | Raises on its own? |
|---|---|---|
| missing | — | **yes** |
| empty file | 0 | no |
| bare `[deny]` table | 0 | no |
| `names = []` | 0 | no |

An unset secret writes an *empty file*: the path exists, the missing-file guard
never fires, and the rule is silently inert behind a green check. A clean scan and
an inert rule both exit 0, so nothing about the scanner's result distinguishes
them.

That is why the job passes `--require-deny-names 1`, and why the failure is exit 2
— "the scan did not complete" — rather than a finding. Nothing was found wrong with
the tree; the scan was not in a position to say so.

`tests/test_leak_scan_deny_names.py` holds the controls, including the pair that
matters: the same file is a finding with a term loaded and invisible without one.

---

## Where it does not apply

**Fork pull requests.** Actions secrets are not available to `pull_request` runs
from forks, so once this repository is public `deny-name` will not fire on fork
contributions. The job emits a warning annotation saying so rather than failing
them — a fork cannot supply the secret, and failing it would block every outside
contribution.

That is acceptable against the threat model. The risk this rule addresses is a
maintainer copying code across from the private tree and bringing a name with it,
which is an in-repo branch push by someone with write access — and those runs do
receive the secret. It is written down because a rule that silently does not apply
on one trigger is exactly how the original gap was born.

---

## What not to do

- **Do not put names in `tools/leak_scan.toml`.** It keeps `names = []` and the
  comment explaining why, and a test asserts it.
- **Do not commit hashes of names either.** Human names are low-entropy and
  trivially enumerable against a hash, so a committed digest publishes what the
  rule protects.
- **Do not relax `--require-deny-names` to make a red job green.** The red is the
  gate telling you it has nothing to work with.
