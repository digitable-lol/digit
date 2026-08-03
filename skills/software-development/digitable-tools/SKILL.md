---
name: digitable-tools
description: Use when routing work to tools.digitable.life utilities.
version: 1.0.0
author: Digitable
license: MIT
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [digitable, tools, converters, generators, developer-utilities]
    related_skills: [digit, digitable-portal, digitable-courses]
---

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

## Local execution — prefer this when Digit is doing the work

The same catalog is available **offline, in-process**, through the `digit-tools`
MCP server. It is the headless port of the same utility collection the website
is built from, so route names and semantics match.

    digit mcp install digit-tools

Three tools, used as a routing sequence rather than 95 flat tools:

| Step | Tool | Arguments |
|---|---|---|
| 1 | `tools_categories` | none — returns 14 categories |
| 2 | `tools_list` | `category` — full input/output schemas and examples |
| 3 | `tools_execute` | `tool_id`, `args`, optional `timeout_ms` |

Argument names come from step 2 and are validated against each utility's own
`input_schema`; guessing them returns a structured `invalid_args` error rather
than a wrong answer. Tool ids use underscores (`hash_text`), while website
routes use hyphens (`hash-text`).

Choose the local server when the user asked Digit to perform the operation,
when the result feeds another step, or when the data is sensitive — nothing
leaves the machine. Link to the website when the user wants to do it themselves
in a browser.

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
