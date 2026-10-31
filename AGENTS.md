# CQ-automata collaboration rules

## Project and version
- Repository: `plfm-iis/CQ-automata`, branch `main`.
- Overleaf project: `69e740f8b52c29cb561f584f`.
- Formal entry point: `main.tex`; it inputs `macros.tex` and uses `literature.bib`.
- Human/coauthor writing takes place on Overleaf. Before substantive review, obtain and verify a fresh snapshot through the inbound workflow.

## Synchronization
- Implemented direction: Overleaf to GitHub only.
- Workflow: `.github/workflows/refresh-from-overleaf.yml`.
- Trigger by workflow dispatch or updating `.github/overleaf-refresh-trigger`.
- First check existing queued/in-progress runs and recent successful results; avoid duplicate refreshes.
- The bridge uses the existing `OVERLEAF_GIT_TOKEN` Actions secret without exposing it or changing its access.
- Initial setup requires identical manuscript snapshots. Later imports require GitHub manuscript files to match the recorded baseline and that baseline to exist in Overleaf history.
- Pending GitHub manuscript edits, changed revisions, unsafe file types, missing core files, and large deletions stop the import.
- `.github/`, `.ai/`, `AGENTS.md`, `PAPER_CONTEXT.md`, and `CLAIMS.md` are GitHub metadata and are never imported from Overleaf.
- Inspect `.github/overleaf-state.json`, the completed run, and final GitHub commit to verify actual success. A trigger commit alone proves nothing.
- No outbound workflow is configured. Never infer authorization to write to Overleaf from read access or this connection request.

## Manuscript edits
- Read the current version first, prepare a complete exact unified diff, and obtain explicit approval before committing manuscript edits.
- Recheck both version bases before applying approval. If either changed, regenerate the diff and obtain approval again.
- Do not force-push, overwrite coauthor edits, change credentials or permissions, or contact collaborators without explicit authorization.
- Do not add or modify schedules unless requested.
