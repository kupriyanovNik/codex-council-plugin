# Council Roles

## Architect

Purpose: propose a coherent solution or interpretation.

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

Purpose: ground the discussion in files, commands, docs, tests, or runtime
evidence.

Expected output:

- evidence artifacts
- claims with file paths, commands, or exact observations
- explicit limits when evidence is missing

## Reviewer

Purpose: assess correctness, maintainability, and test coverage.

Expected output:

- review findings
- severity-ranked concerns
- missing-test notes

## Security

Purpose: identify security, privacy, prompt-injection, data exposure, or
permission risks.

Expected output:

- threat notes
- concrete attack or misuse paths when present
- suppressions with evidence when risks are not applicable

## Writer

Purpose: implement changes when and only when the user explicitly allowed edits.

Expected output:

- a claimed task lease before editing
- a small focused patch
- verification commands and results
- completion artifact with changed paths

Writer is disabled by default in all non-implementation modes.

