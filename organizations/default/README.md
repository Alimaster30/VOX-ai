# Default Organization

This folder is the first step toward making VOX multi-organization.

- `profile.json` stores organization identity, model choices, greetings, and fallback behavior.
- `documents/` will hold uploaded organization datasets.
- `vector_index/` will hold generated embeddings.
- `intents.json` will hold generated or reviewed intents for this organization.

The current default profile preserves the existing Air University Multan Campus behavior while moving it out of hardcoded Python constants.
