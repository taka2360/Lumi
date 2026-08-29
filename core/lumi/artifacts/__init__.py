"""What Lumi runs on, and where it lives on disk. **Below both providers and setup.**

Design → docs/architecture/setup.md / Decision → docs/decisions/ADR-045-core-module-layering.md

`models` and `engines` answer one question: *which* build of an external model or
engine this version of Lumi runs against, and where it is unpacked to. `install`
fetches one, verifies it, and commits it into place atomically.

## Why this is not part of `setup`

**Both sides of the fetch need the answer.** `setup` needs it to know what to
download; a Provider needs it at runtime to find what was downloaded. With the
pins living under `setup`, every Provider imported `lumi.setup`, while `setup`
imported `lumi.providers` for the Ollama endpoint — a package-level cycle that
only avoided `ImportError` because the concrete modules happened not to meet.

Putting the pins below both makes the dependency one-way:

    artifacts  <-  providers  <-  setup

**`providers/` importing `lumi.setup` is a static-test failure**
(docs/contracts/authority-matrix.md #21).

## No external communication until the user chooses to

**The only place in Core that touches an artifact host over HTTP is `install.py`**,
and it runs only after the user's explicit choice has been received
(docs/decisions/ADR-019-tts-engine-distribution.md). Detection reads the disk;
it never reaches the network to find out what is available.
"""
