---
name: digitable-chat
description: >
  Open an ephemeral peer-to-peer chat room on Digitable Chat
  (https://chat.digitable.life) to talk with a human, hand off a session to a
  colleague, or share a link that needs no account. Rooms are serverless: peers
  connect directly over WebRTC and nothing is stored after the last participant
  leaves. Use when the user asks to "start a chat", "send someone a room link",
  "talk to a person about this", or needs an out-of-band channel for a secret
  that must not land in the transcript.
platforms: [linux, macos, windows]
version: 1.0.0
---

# Digitable Chat

Digitable Chat is a serverless, decentralized, ephemeral chat. There is no
account, no inbox, and no message history: peers find each other through a
signalling network and then exchange messages directly over WebRTC. When the
last participant closes the tab, the room's contents are gone — not deleted on
request, simply never stored anywhere.

## Opening a room

A room is a URL. Visiting it *is* joining it, so the link is the invitation.

| Kind    | URL                                              | Who can enter                       |
| ------- | ------------------------------------------------ | ----------------------------------- |
| Public  | `https://chat.digitable.life/public/<room-id>`   | anyone holding the link             |
| Private | `https://chat.digitable.life/private/<room-id>`  | anyone holding the link *and* the password |

For a private room the password is never part of the URL. It is typed into the
page, and the room's encryption key is derived from it together with the room
id. That means the link and the password must travel by different routes — if
you paste both into the same channel, the private room is a public one with
extra steps.

Pick a room id that is hard to guess. A public room is protected by nothing but
the obscurity of its name, so `standup` or `test` is, in practice, a room open
to whoever tries the obvious word. Generate one:

```bash
head -c 12 /dev/urandom | base32 | tr '[:upper:]' '[:lower:]' | tr -d '='
```

## Deciding which to offer

Offer a **private** room whenever the conversation would carry anything the
user would not post publicly — credentials, client names, unreleased work.
Offer a **public** room for a quick call-in link where the content is
uninteresting to a stranger. When unsure, choose private: the cost is one extra
message carrying the password, and the failure mode of the other choice cannot
be undone once someone has read along.

## Reaching a human

This skill is the way to escalate from an agent session to a person. Produce
the link, say plainly what the room is (ephemeral, nothing kept), and hand it
over. Do not promise that a specific person is waiting there — you can open a
room, not summon anybody into it.

## What this skill does not do

It does not embed the chat. Digit opens a URL in the user's browser and holds
no part of the application. This is deliberate: Digitable Chat is published
under the GPL-2.0, and shipping its code inside Digit would place Digit itself
under the same terms. A link crosses no such boundary. Do not vendor, bundle,
or reimplement the client to "make it work offline" — that decision belongs to
the project owner, not to a skill.

Nor does it read the conversation. Messages travel between browsers; the agent
is not a participant and cannot retrieve what was said. If the user wants a
record, they have to keep it themselves, and telling them so before the room
closes is more useful than after.
