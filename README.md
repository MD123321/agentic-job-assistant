# Agentic Job Assistant

A privacy-first AI system for discovering relevant roles and helping a user complete application forms without handing control to the model.

This public repository is a sanitized showcase of a larger private system. It contains the application code, tests and fictional examples only. Search results, application history, candidate documents and personal configuration are intentionally excluded.

## What it does

### Job discovery agent

- Runs multiple configurable search tracks and removes duplicate or non-actionable results.
- Uses the OpenAI Responses API with Structured Outputs as a strict relevance and quality gate.
- Produces a daily report and can deliver it by email.
- Fails closed when location, timing, experience level or model output is uncertain.
- Keeps aggregate diagnostics without storing prompts, API keys or candidate details.

### Local application assistant

- A Manifest V3 Chrome extension reads the largest visible application form after a user click.
- A localhost Python service combines deterministic rules with structured AI interpretation.
- Contact details and candidate documents remain local and are never included in the model prompt.
- Generated text and uncertain answers require review.
- Consent, legal declarations and final submission are always manual.
- CV or transcript upload happens only after an explicit user action.

## Architecture

```text
Public job pages
    -> deterministic URL and duplicate filters
    -> structured AI relevance gate
    -> report and optional email

User click in Chrome
    -> visible form descriptors
    -> localhost rule engine
    -> structured AI interpretation
    -> validated preview
    -> user-approved field filling
    -> manual submission
```

## Why it is agentic

The model does not simply generate text. It interprets uncertain inputs, proposes structured actions and operates inside explicit boundaries. Local code validates every action against known field IDs, allowed profile keys, visible options and safety rules before anything can be filled.

## Privacy and safety choices

- Real candidate data lives in git-ignored local files.
- The browser extension never receives an OpenAI API key.
- The localhost service binds only to `127.0.0.1`, restricts the Chrome extension origin and requires a random local token.
- Webpage text is treated as untrusted input and cannot override system instructions.
- Unknown fields, invalid options and invented profile keys are rejected.
- The extension never clicks submit.

## Run the tests

Python 3.11 or newer and Node.js are recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m unittest discover -s tests -v
python -m unittest discover -s application_assistant/tests -p 'test_*.py' -v
node --test application_assistant/tests/browser_extension.test.js
```

The repository currently includes 93 automated tests across the search agent, AI validation, local service and browser extension.

## Run the job discovery agent

Copy the fictional example profile and adjust it locally:

```bash
cp config/profile.example.toml config/profile.toml
python -m src.main
```

The job agent can run without email. API keys and email credentials are supplied only through environment variables. No real configuration or output is included in this repository.

## Run the application assistant

See [`application_assistant/README.md`](application_assistant/README.md) for the local setup and safe test flow. The included candidate profile is fictional, and the test site blocks submission.

## Repository boundaries

This showcase intentionally excludes:

- job reports and discovered listings
- application history and status logs
- real candidate profiles
- CVs, transcripts and cover letters
- API keys, email credentials and local service tokens
- the private production workflow

