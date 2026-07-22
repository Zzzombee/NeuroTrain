# NeuroTrain Repository Instructions

## Skill routing

- For NeuroTrain work, including requests using the legacy name `neuroexplorer-origin-psth-pptx`, read the repository-root `SKILL.md` before acting.
- Reuse the entry points and scripts documented in `SKILL.md` instead of reimplementing the workflow.

## Source of truth

- Treat this repository root as the only NeuroTrain development source. On the current workstation, its canonical path is `D:/Code/NeuroTrain`.
- Do not develop against the legacy checkout at `D:/Code/Research/skills/neurotrain` or against a separate copy under the user profile.
- The user-level path `C:/Users/26353/.codex/skills/neurotrain` must remain a Windows junction to this repository root.
- Make code, tests, configuration-template, and documentation changes in this repository so the project source and the user-level skill entry remain synchronized.

## Repository conventions

- Keep `README.md` and `HELP.md` in Chinese. Preserve commands, paths, configuration keys, data fields, and filenames in their technical form.
- Preserve the separation between the normal aligned-rate branch and the dedicated time-cluster branch described in `SKILL.md`.
- Do not modify raw fullrate CSV files or use deletion of raw/intermediate Unit data as a filtering mechanism.
