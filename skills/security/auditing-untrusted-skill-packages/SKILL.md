---
name: auditing-untrusted-skill-packages
description: Audit a third-party skill/CLI package for telemetry, data exfiltration, and unsafe install behavior BEFORE installing it. Use when a user asks to install a skill from an external URL or zip, especially when it contains minified/obfuscated JS. Covers static inspection of bundled scripts, locating telemetry endpoints and reported fields, and detecting install scripts that self-update.
---

# Auditing Untrusted Skill Packages

Use when a user asks to **install a skill from an external URL or archive** (e.g. a vendor
download link). Goal: determine what the code actually does *before* it runs, and report
privacy/security impact clearly so the user can make an informed choice.

## Trigger conditions

- "Install this skill: <url>"
- "Here's a zip, add it to my skills"
- Any third-party package containing large minified/obfuscated `.js`/`.cjs`
- Any `install.sh` that fetches remote config

## Step 1 — Download without executing

```bash
cd /tmp && curl -sL -o pkg.zip "<url>" -w "HTTP:%{http_code} SIZE:%{size_download}\n"
```

**Never** run `install.sh` or `npm install` at this stage. Download only.

## Step 2 — List contents (environment pitfall)

This NAS image has **no `unzip` and no `file`**. Use Python's `zipfile`:

```python
import zipfile
z = zipfile.ZipFile('/tmp/pkg.zip')
for n in z.namelist(): print(n)
print(z.read('SKILL.md').decode('utf-8','replace'))
```

Read `SKILL.md`, `scripts/install.sh`, and any `references/` docs in full.
**`install.sh` is the highest-risk file** — look for `curl`/`wget` to a remote endpoint
that pulls a config or newer version and **overwrites** the local skill. If present, note
that the code actually run may differ from the code audited.

## Step 3 — Locate all network destinations first (highest signal/lowest effort)

```python
import re
hosts = sorted(set(re.findall(r'https?://([a-zA-Z0-9\.\-]+)', cli)))
print(hosts)
```

Classify each host:
- **Official vendor domain** → expected API traffic
- **Unknown analytics/telemetry domain** → investigate
- Compare against the `require("...")` list to rule out `child_process` / `exec` / `spawn`
  (arbitrary command execution is the worst-case finding; check this early)

## Step 4 — Find the telemetry SDK and its reported fields

Telemetry SDKs (jstrace/jssdk/otel variants) are greppable by their distinctive markers:

```python
for kw in ['jstrace', 'jssdk', '/api/v1/', 'uploadChannel', 'upath',
           'addAttributes({', 'setSpanAttribute', 'raw_query']:
    for m in re.finditer(re.escape(kw), cli):
        i = m.start()
        print(repr(cli[max(0,i-300):i+300]))
```

Then extract the **attribute key names**, which reveal exactly what data is collected:

```python
attrs = set(re.findall(r'addAttributes\(\{([^}]{0,300})\}\)', cli))
for a in sorted(attrs): print(a)
```

**What to flag as a privacy problem** (these are the fields that exceed what a tool needs):
- `raw_query` / `assistant_query` — the user's actual question text (check for truncation,
  e.g. a `1024` byte constant passed to a truncation helper)
- `session_id` — links activity back to the agent session
- `work_cwd`, `download_output_path`, `create_folder_dir_path` — local filesystem paths
- `machine` / `platform`/`arch` — host fingerprint
- `device_id`, `account_id`, `login_token_hash` — identity linkage
- `search_keyword` — what the user searched for

Also decode `\uXXXX` escapes to read minified Chinese strings:
```python
def dec(s): return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1),16)), s)
```

## Step 5 — Check for environment sniffing

Grep for the agent's own env vars (e.g. `HERMES_SESSION_ID`, `HERMES_INTERACTIVE`).
If the package special-cases your runtime, it is aware of and adapting to the host — report it.

## Step 6 — Check side-effect files

Look for hardcoded output paths outside the skill dir (e.g. `/tmp/<vendor>-deliver/...`,
`deliver.json`). These are integration mechanisms, not malice, but the user should know.

## Reporting format

Lead with the conclusion, then:

1. **What the package contains** (file list, size of obfuscated blobs, CLI commands)
2. **Findings table** — field name → what it leaks → severity
3. **Fair assessment** — distinguish vendor analytics from malware. If it's
   `jstrace`/`jssdk`-style vendor telemetry with no `exec`, say so explicitly rather than
   implying it's a trojan. Accuracy over alarm.
4. **Options** — let the user decide:
   - Install as-is (accept the telemetry)
   - Install + block telemetry hosts in `/etc/hosts` (telemetry is usually async, so
     functionality survives) — requires user confirmation, it's a system change
   - Don't install
5. **State clearly what you cannot do for them** — e.g. OAuth/QR-code account
   authorization must be performed by the user; you cannot authorize on their behalf.

## Pitfalls

- **Obfuscated ≠ malicious.** Say "vendor analytics" when that's what it is. Overstating
  threat harms trust.
- **Never execute to "test it".** Static inspection only until the user approves.
- **An `install.sh` that re-downloads means you audited the wrong code.** Flag this
  prominently — it's the single most important structural finding.
- Match variable names like `ly="raw_query"` by grepping *both* the key string and its
  surroundings; minifiers rename vars but string literals survive.
- Truncation constants (e.g. `py=1024`) tell you the exfiltration limit — useful detail
  for the report.
