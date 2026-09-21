"""Application layer — RESERVED, currently EMPTY and UNREFERENCED.

Nothing in this repository imports this package. It is kept as a named
placeholder for orchestration that sits *above* the kernel: run lifecycle,
baseline comparison, review workflow, evidence assembly policy.

Deliberately not populated yet, because the current deliverable is a
verifiable vertical slice whose boundaries are:

    domain/   -> pure semantics and validation (no I/O)
    kernel/   -> pure computation (no I/O, no policy)
    adapters/ -> JSON in / text out
    cli/      -> argument handling, exit codes, evidence bundle wiring
    evidence/ -> artifact writing

Adding an application layer before there is a second caller would be
speculative structure. If you are looking for the analysis entry point use
`native_safety.kernel.solve.solve_model`; for the CLI use
`native_safety.cli.main`.
"""
