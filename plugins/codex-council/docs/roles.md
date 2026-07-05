# Council Roles

## Architect

Purpose: propose a coherent answer, interpretation, plan, or solution.

Expected output:

- a concise proposal message
- a detailed proposal artifact
- claims for important assumptions

## Skeptic

Purpose: find weak assumptions, missing constraints, and failure modes.

Expected output:

- challenge messages addressed to relevant roles
- claims for risks and counterexamples
- a vote only after checking the strongest opposing view

## Verifier

Purpose: ground the discussion in the task context, provided material, source
documents, files, commands, docs, tests, or runtime evidence when available.

Expected output:

- evidence artifacts
- claims with source references, file paths, commands, or exact observations
- explicit limits when evidence is missing

## Reviewer

Purpose: assess correctness, reasoning quality, gaps, maintainability when code
is involved, and test coverage when tests are relevant.

Expected output:

- review findings
- severity-ranked concerns
- missing-evidence or missing-test notes

## Security

Purpose: identify safety, security, privacy, prompt-injection, data exposure, or
permission risks when they are relevant to the objective.

Expected output:

- threat notes
- concrete attack or misuse paths when present
- suppressions with evidence when risks are not applicable

## Writer

Purpose: change files or artifacts when and only when the user explicitly
allowed edits.

Expected output:

- a claimed task lease before editing
- a small focused change
- verification commands, checks, or review notes
- completion artifact with changed paths or artifact ids

Writer is disabled by default in all non-implementation modes.
