# Prompts

This directory contains all Claude prompts used by the application.

## Conventions

- One prompt per `.md` file
- Filename format: `<name>_v<version>.md`
- Every prompt file has a header section explaining: purpose, used-by, model, version history, calibration notes
- Variables in templates use double-brace syntax: `{{variable_name}}`
- The actual content to process is appended after `Content:` at the end

## Versioning

- Start at v1
- Bump version on substantive prompt changes
- Keep old versions for reference and rollback
- Document the rationale for each version bump in the file header

## Testing a new prompt

When iterating on a prompt:

1. Create the new version file (e.g. `claim_extraction_v2.md`)
2. Update the calling code to use the new version
3. Run on a held-out test set of 20-30 representative inputs
4. Manually evaluate outputs vs the previous version
5. If better, ship it. If worse, revise or revert.
6. Always keep the old version file — never overwrite a shipped prompt
