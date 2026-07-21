# Windrose Documentation

## Layout

| Dir | Contents |
|-----|----------|
| [`brd/`](brd/) | Business requirements per service (source of truth for scope) |
| [`platform/`](platform/) | Architecture, conventions, capabilities, agent/user guides |
| [`design/`](design/) | Earlier per-feature design notes (problem → phases → status) |
| [`initiatives/`](initiatives/) | **Full-lifecycle docs — every substantive change lives here** |

## Documentation convention (standing)

Every substantive change is documented as one file under [`initiatives/`](initiatives/),
following this three-phase pattern (see [`_TEMPLATE.md`](_TEMPLATE.md)):

1. **Analysis**
   - **Platform / product** — why it matters to the product and the customer; the problem, who it affects, the outcome.
   - **Technical** — the current state in code with `file:line` evidence; the root cause; what's already there vs missing. No guessing — cite the code.
2. **Architecture & Design** — the approach, the options weighed, the decision and why, the contracts/invariants, and what stays out of scope.
3. **Implementation & Test** — what was built (files + commits), how it was verified (tests + live evidence), and what's explicitly deferred.

Keep it honest: record what was verified vs assumed, and flag known gaps rather than hide them.

## Index — initiatives

| Doc | Status |
|-----|--------|
| [Tenant customization lifecycle](initiatives/tenant-customization-lifecycle.md) | Implemented (BFF+UI), not browser-verified |
| [Stability: durability & self-heal](initiatives/stability-durability.md) | Implemented; live green pending infra recreate |
| [Scalability bottleneck audit](initiatives/scalability-audit.md) | Analysis + fix roadmap; implementation pending |
