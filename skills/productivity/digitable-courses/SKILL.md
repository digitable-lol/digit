---
name: digitable-courses
description: Use when finding or planning Digitable courses.
version: 1.0.0
author: Digitable
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [digitable, courses, learning, curriculum, russian]
    related_skills: [digit, digitable-portal, digitable-tools, fts]
---

# Digitable Courses

## Overview

`courses.digitable.life` is the canonical open learning portal. Use its current course pages to build study paths rather than copying the full curriculum into memory.

## Curriculum map

The portal spans five broad layers: computer-science foundations; programming languages and platforms; architecture and system design; data, machine learning, and neural networks; product, project, and engineering practice. Current tracks and lesson order must be verified on the live course index.

## Workflow

1. Ask or infer the learner's goal, current level, preferred language, available time, and target deadline.
2. Inspect `https://courses.digitable.life/courses/` and the relevant current track pages.
3. Build the shortest prerequisite chain that reaches the goal; distinguish required material from optional depth.
4. Link each recommended step to its canonical page.
5. Use `digitable-tools` for exercises requiring conversion, formatting, hashing, networking, regex, or related deterministic utilities.
6. Use `fts` when the learner wants to turn a domain rule into an executable utility, generated tests, or a verified agent guard.
7. End with a checkable milestone or small project, not only a reading list.

## Safety and accuracy

- Treat payment, access, schedule, and completion state as live account data.
- Do not claim certification or enrollment unless a canonical page or authenticated account confirms it.
- Quote sparingly; summarize course material and link to the original.

## Verification checklist

- [ ] Current course pages inspected.
- [ ] Prerequisites are ordered.
- [ ] Required and optional steps are separated.
- [ ] A concrete milestone closes the plan.
