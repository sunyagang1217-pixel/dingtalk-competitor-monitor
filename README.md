# DingTalk Competitor Monitor

[简体中文](README.zh-CN.md) | English

A reusable Codex Skill that guides a user from industry scoping to a verified, deduplicated competitor digest delivered to a DingTalk group. It generates a complete local Python project instead of returning disconnected code snippets.

![DingTalk Competitor Monitor workflow](docs/skill-overview.png)

## What it does

- Defines the industry boundary, regions, topics, competitors, brand aliases, related entities and priorities through a guided conversation.
- Monitors domestic and global public signals for any industry, not just a fixed competitor list.
- Collects Google News, Bing, Baidu, 360 Search and WeChat public-account search as peer discovery sources.
- Searches WeChat articles with every competitor name, alias and confirmed related entity, including mentions from non-official accounts, and fetches three pages by default.
- Uses brand names, confirmed aliases, parent companies, operating entities and responsible people across all five sources without mandatory industry terms. Related-entity-only titles require context that links the entity to the brand.
- Records success, failure, blocking, partial success or non-applicability for every brand and source, including candidate counts, page counts and sanitized errors.
- Keeps candidates whose search snippets have no date, but requires the real publication date to be verified from the original source.
- Allows an empty digest only after every critical brand meets the minimum source coverage. With all five sources enabled, the default is at least three successful sources for brands with priority 3 or higher.
- Retains the complete candidate pool for review before applying the final digest limit, and prioritizes personnel changes while preserving uncertainty in unconfirmed reports.
- Uses search results only for discovery, then requires the original article, public-account post, official page or public notice to be opened and verified.
- Separates facts from inference and supports both an analysis digest and a concise breaking-news format.
- Prevents repeated delivery with normalized title fingerprints and SQLite state.
- Carries verified important items to a chosen delivery date and requires them to be reverified before sending.
- Provides a Chinese terminal wizard for DingTalk Webhook and signing-secret validation.
- Stores local credentials in macOS Keychain and keeps them out of chat, project files, logs and Git.
- Previews the full destination, message and mention behavior before a real test or first digest is sent.
- Creates a Codex Desktop schedule only after both real-message checks have succeeded.
- Verifies committed delivery records after sending and supports a later result check in the same scheduled task to detect skipped or unfinished runs.

## How it works

| Stage | Outcome |
| --- | --- |
| 1. Scope | Confirm the industry, regions, competitors, aliases, related entities, priorities, source coverage, schedule and digest format. |
| 2. Generate | Create a Python project with five-source collection, per-brand source status, verification, carryover, sending, deduplication and tests. |
| 3. Configure | Validate the DingTalk Webhook and signing secret in a friendly Chinese terminal flow. |
| 4. Verify | Preview and explicitly approve one connection test and one analyzed digest. |
| 5. Automate | Schedule weekday or weekly runs in Codex Desktop, either as drafts or authorized sends. |

## Quick start

### Requirements

- Codex Desktop, Codex CLI or the Codex IDE extension with local Skill support.
- macOS and Python 3.11 or later for the generated project.
- Access to a DingTalk group where you can configure a custom robot.
- Access to GitHub; this repository is public.

### Install for your user account

Clone this repository into your personal Codex Skill directory:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
git clone https://github.com/sunyagang1217-pixel/dingtalk-competitor-monitor.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/dingtalk-competitor-monitor"
```

Codex detects Skill changes automatically. Restart Codex if the Skill does not appear.

### Start the guided workflow

Invoke the Skill explicitly:

```text
Use $dingtalk-competitor-monitor to help me build a competitor-monitoring
robot for my industry and guide me one step at a time.
```

The Skill will first ask about scope and scheduling. It will show the complete derived specification and wait for confirmation before generating files.

When the generated project is ready, run its Chinese credential wizard in your own terminal:

```bash
./scripts/configure_dingtalk.sh
```

Do not paste the Webhook, access token or signing secret into chat. The wizard hides input, validates common copy mistakes and stores both values in macOS Keychain.

## Generated project

The scaffolded project includes:

| Component | Purpose |
| --- | --- |
| `config/monitoring.json` | Industry scope, competitors, aliases, related entities, priorities, regions, pagination and coverage thresholds. |
| `config/analysis_prompt.md` | Original-source verification and digest-writing rules. |
| `src/competitor_monitor_bot/result_check.py` | Read-only checks of the digest date, send timestamp and recorded article count, without credentials or sending. |
| `data/pending_articles.json` | Verified important items intentionally deferred to a future digest. |
| `src/competitor_monitor_bot/` | Five-source collection, per-brand source status, analysis, carryover, DingTalk signing, dispatch and SQLite state. |
| `scripts/configure_dingtalk.sh` | Chinese credential setup and validation wizard. |
| `tests/` | Unit tests for configuration, signing, analysis, collection, dispatch and deduplication. |

Typical no-send commands in a generated project:

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli check-config
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli collect-json \
  --output data/analysis.json
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli analysis-preview \
  --input data/analysis.json
```

`collect-json` records enabled sources, every brand/source attempt, critical-brand coverage and sanitized failure reasons. A candidate without a search date is retained with `published_at_precision: "missing"`; every candidate remains unverified until its real source, date and content type are corrected from the original, so raw candidates cannot pass `analysis-preview`. These commands do not send DingTalk messages; real sends remain protected by explicit confirmation in the guided workflow.

## Verify delivery results

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli check-result
```

The command checks today's SQLite records in the configured timezone without credentials, network requests or writes. `status=sent`, `complete=true` and exit code 0 confirm consistent delivery records; an empty digest is valid only when every critical brand met its configured minimum source coverage. Missing records, unfinished claims, inconsistent records and unreadable databases fail the check. A chat marked complete is insufficient evidence. `send-analysis` also returns a `delivery_check` and a nonzero exit code when verification fails. This verifies recorded delivery, not whether group members read the message.

Set the optional `schedule.result_check_time` to add a later check, for example a 10:30 send and a 10:50 read-only check. A normal check stays quiet; missing or abnormal results are reported according to the user's notification preferences. Checks never resend automatically. One heartbeat per chat handles both times using `phase`; omitting the field or setting it to null retains only the check after sending. Draft-only tasks must not require a sent record. Both triggers need the computer and Codex to remain running; this does not guarantee checks while offline.

Updating the Skill does not overwrite existing generated robots. Migrate their code, configuration and scheduled instructions separately while preserving credentials, SQLite history and carryover queues.

The automation prompt should explicitly identify a heartbeat and require a read-only `check-result` as its first task, rather than ending with a conversational acknowledgment. Keeping the automation conversation and model stable is recommended; a model-switch notice alone does not establish the cause of a missed run. Prompt changes still require validation on a real scheduled trigger. Migrate to a dedicated conversation by updating the existing task to avoid duplicate delivery.

When a user explicitly requests a same-day supplement for missed news, use the same `--supplement-id` with `analysis-preview`, `send-analysis` and `check-result`. Supplements have separate delivery records, preserve the primary digest, and share article deduplication across both histories. Retrying the same batch does not resend; no new articles means no supplemental message. Scheduled tasks must not create supplement batches automatically.

## Safety model

| Boundary | Behavior |
| --- | --- |
| Credentials | Never request or store Webhooks, tokens or signing secrets in chat, files, screenshots, logs or Git. |
| Sources | Treat all five sources as peers and never bypass login or security checks. Record every brand/source attempt and stop when every source fails. |
| Empty digest | Every critical brand must meet the configured number of fully successful sources; `partial` does not count as full success. |
| Verification | Remove unreadable, out-of-window or unsupported claims; record the real public-account name and content type. |
| Content | Separate facts from interpretation, label brand claims, and identify the nature and subject of court or regulatory notices. |
| Carryover | Reverify every due item and never silently remove it; mark it `sent` only after DingTalk succeeds. |
| Sending | Show the bound-group target, full title, full body and mention behavior before a real message. |
| Mentions | Do not mention anyone by default. |
| Deduplication | Record state only after successful delivery; suppress same-day reruns and previously sent articles. |
| Automation | Do not enable unattended sending unless the user explicitly authorizes the confirmed scope and schedule. |

## Repository structure

```text
.
├── SKILL.md                         # Runtime workflow for Codex
├── agents/openai.yaml               # Skill display metadata
├── assets/project-template/         # Generated Python project template
├── references/                      # Conversation, safety and automation rules
├── scripts/scaffold_project.py      # Deterministic project generator
└── docs/skill-overview.png          # Workflow overview used in this README
```

## Development and validation

Run the project-template test suite from the repository root:

```bash
PYTHONPATH=assets/project-template/src python3 -m unittest discover \
  -s assets/project-template/tests -v
python3 -m unittest discover -s tests -v
python3 -m compileall -q assets/project-template/src \
  assets/project-template/tests scripts tests
```

Also validate the Skill directory with the `quick_validate.py` script bundled with the Codex `skill-creator` Skill after changing `SKILL.md` or `agents/openai.yaml`.

## Official references

- [Build skills for ChatGPT and Codex](https://learn.chatgpt.com/docs/build-skills)
- [DingTalk robot overview](https://open.dingtalk.com/document/orgapp/robot-overview)
- [DingTalk custom robot access](https://open.dingtalk.com/document/robots/custom-robot-access)
