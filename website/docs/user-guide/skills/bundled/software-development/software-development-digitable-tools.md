---
title: "Digitable Tools — Use when routing work to tools.digitable.life utilities"
sidebar_label: "Digitable Tools"
description: "Use when routing work to tools.digitable.life utilities"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Digitable Tools

Use when routing work to tools.digitable.life utilities.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/software-development/digitable-tools` |
| Version | `1.0.0` |
| Author | Digitable |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `digitable`, `tools`, `converters`, `generators`, `developer-utilities` |
| Related skills | [`digit`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-digit), [`digitable-portal`](/docs/user-guide/skills/bundled/productivity/productivity-digitable-portal), [`digitable-courses`](/docs/user-guide/skills/bundled/productivity/productivity-digitable-courses) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Digitable Tools

## Overview

Route deterministic browser tasks to `https://tools.digitable.life/<route>`. The utilities run client-side where supported, but the agent must still avoid uploading or pasting secrets unless the user understands the page and browser boundary.

## Route catalog

### Crypto and identifiers

`token-generator`, `hash-text`, `bcrypt`, `uuid-generator`, `ulid-generator`, `encryption`, `bip39-generator`, `hmac-generator`, `rsa-key-pair-generator`, `password-strength-analyser`, `pdf-signature-checker`.

### Conversion and structured data

`date-converter`, `base-converter`, `roman-numeral-converter`, `base64-string-converter`, `base64-file-converter`, `color-converter`, `case-converter`, `text-to-nato-alphabet`, `text-to-binary`, `text-to-unicode`, `yaml-to-json-converter`, `json-to-yaml-converter`, `toml-to-json`, `toml-to-yaml`, `yaml-to-toml`, `json-to-toml`, `json-to-xml`, `xml-to-json`, `list-converter`, `markdown-to-html`.

### Web and browser data

`url-encoder`, `html-entities`, `url-parser`, `device-information`, `basic-auth-generator`, `og-meta-generator`, `otp-generator`, `mime-types`, `jwt-parser`, `keycode-info`, `slugify-string`, `html-wysiwyg-editor`, `user-agent-parser`, `http-status-codes`, `json-diff`, `safelink-decoder`.

### Development and formatting

`git-memo`, `random-port-generator`, `crontab-generator`, `json-prettify`, `json-minify`, `json-to-csv`, `sql-prettify`, `chmod-calculator`, `docker-run-to-docker-compose-converter`, `xml-formatter`, `yaml-prettify`, `email-normalizer`, `regex-tester`, `regex-memo`, `benchmark-builder`.

### Network

`ipv4-subnet-calculator`, `ipv4-address-converter`, `ipv4-range-expander`, `mac-address-lookup`, `mac-address-generator`, `ipv6-ula-generator`.

### Images, video, text, math, and data

`qrcode-generator`, `wifi-qrcode-generator`, `svg-placeholder-generator`, `camera-recorder`, `math-evaluator`, `eta-calculator`, `percentage-calculator`, `chronometer`, `temperature-converter`, `lorem-ipsum-generator`, `text-statistics`, `emoji-picker`, `string-obfuscator`, `text-diff`, `numeronym-generator`, `ascii-text-drawer`, `phone-parser-and-formatter`, `iban-validator-and-parser`.

## Workflow

1. Select the narrowest matching route from the catalog.
2. Link directly to `https://tools.digitable.life/<route>`.
3. State the expected input and output in one sentence.
4. Prefer an agent-native local tool when the user asked Digit to perform the operation directly, when automation is required, or when data is sensitive.
5. Verify the route is live before claiming availability when network access exists.

## Common pitfalls

- JWT parsing does not verify a signature; say so explicitly.
- Hashing is not encryption, and Base64 is not encryption.
- Do not generate production secrets in a shared or recorded browser session.
- Do not route large batch jobs through an interactive browser utility.

## Verification checklist

- [ ] Route exists in the catalog and direct URL is correct.
- [ ] Input/output expectation is clear.
- [ ] Sensitive-data and automation boundaries were considered.
