# Contributing

Thank you for helping with Naos. This page covers how to set up the tree,
what a change has to pass and how commits are written. Everyone taking part
follows the [Code of Conduct](CODE_OF_CONDUCT.md).

- [Before you start](#before-you-start)
- [Set up the tree](#set-up-the-tree)
- [Make a change](#make-a-change)
- [Commits](#commits)
- [Pull requests](#pull-requests)
- [Dependencies](#dependencies)

## Before you start

- A bug or a feature starts as an issue: pick the matching form. Small fixes
  of typos and links can go straight to a pull request.
- A vulnerability is never an issue: follow [SECURITY.md](SECURITY.md).
- A change to the architecture is proposed in `docs/` first and agreed
  before it is built. [AGENTS.md](AGENTS.md) holds the invariants a change
  must keep: default deny, deny overrides allow, fail closed.

## Set up the tree

Install the requirements listed in the [README](README.md#requirements), then:

```bash
make install
make shell
```

`make install` builds `.venv`, installs the pinned tools and packer, and the
git hooks, commit-msg included. `make shell`, or a bare `make`, opens a shell
inside that venv.

## Make a change

1. Name the component that owns the change: `packages/api`, `packages/web`,
   `packages/runner`, `packer/`, `docker/`, `release/` or `docs/`.
2. Define how it fails and write the tests, positive and negative. A
   security-sensitive change always has a negative test.
3. Keep the change small and scoped to that component.
4. Update the docs the change makes wrong.

Run the hooks and the tests before you push:

```bash
make lint
make test
```

`make test-api`, `make test-web` and `make test-image` run one component;
`make smoke` boots a Run end to end. Runner, Run, gate and console tokens
are credentials: never log them, and never put them in tests, fixtures or
commits.

## Commits

Messages follow the `wyld_cz` [commitizen](https://commitizen-tools.github.io/commitizen/)
style, and the commit-msg hook rejects anything else:

```text
[fix][runner]: refuse a mount outside the allowed roots
```

- The type is `fix`, `feat`, `build`, `docs` or `refactor`.
- The scope is the component: `api`, `web`, `runner`, `image`, `packer`,
  `docker`, `release`, `docs`.
- The subject is short and imperative.
- A breaking change carries `[!]` in front and a `BREAKING CHANGE:` block.

`cz commit` asks for each part and writes the message for you. Each
component keeps its own version and `.cz.yaml`; a release is tagged
`naos-api-X.Y.Z`, `naos-web-X.Y.Z`, `runner-X.Y.Z`, `image-X.Y.Z`, and the
stack-wide meta release plain `X.Y.Z`.

## Pull requests

- One topic per pull request, branched from `main`.
- The title follows the commit format, `[type][scope]: subject`. CI checks
  it with `cz check`.
- Fill in the template: what changed, why, and the linked issue.
- `make lint` and `make test` pass, and CI is green.
- A maintainer reviews and merges it.

## Dependencies

[Dependabot](https://docs.github.com/code-security/dependabot) opens weekly
pull requests for the Python requirements, pre-commit hooks, crates, base
images, compose services, the vendored browser packages, the agent CLIs in
the image and the GitHub Actions. What it cannot read, the `pins` workflow
checks: the Alpine ISO, goose, packer and its QEMU plugin, virtiofsd. Both
wait 14 days after an upstream release before they propose it.
