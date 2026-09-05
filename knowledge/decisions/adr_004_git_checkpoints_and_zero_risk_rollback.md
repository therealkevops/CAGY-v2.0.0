# ADR 004: Native Git Checkpoints and Zero-Risk Auto-Stash Rollback

- **Date**: 2026-09-04
- **Status**: Accepted
- **Tags**: #decisions #adr #checkpoints #rollback #git #safety

## Context & Problem Statement
When autonomous agents execute complex file modifications, code refactoring, and multi-file code generation, human operators require continuous oversight and reliable rollback mechanisms. Previous workflows faced key challenges:
1. Reverting an undesirable turn required manual shell execution of Git commands.
2. Direct `git reset --hard` carried catastrophic data loss risks for human operators who had uncommitted modifications or untracked files in their workspace.
3. Operators could not inspect unified commit patch diffs directly in the Web UI before deciding whether to roll back.

## Decision Drivers
- Provide human operators with instant visual awareness of workspace commits and modifications.
- Ensure rollback operations are 100% zero-risk: no uncommitted work or untracked files may ever be destroyed.
- Enable interactive commit patch diff inspection in the Web UI prior to reverting.
- Gracefully handle edge cases such as non-Git directories, clean trees, and merge conflicts.

## Considered Options
1. **Custom File-Snapshotting Engine**: Recursively duplicate changed files into a hidden `.cagy_snapshots/` folder before every agent execution. (Rejected: Heavy disk I/O overhead, potential storage bloat, out-of-sync with operator Git history).
2. **Destructive Git Reset**: Issue raw `git reset --hard <commit>` directly. (Rejected: Destroys unstaged human edits and untracked files with no recovery mechanism).
3. **Native Git Checkpoint Engine with Zero-Risk Auto-Stash Safety Net**: Query Git log/status for commit checkpoints, provide a unified patch diff viewer, and execute rollbacks with an automatic safety stash (`git stash push -u -m "pre-rollback-auto-stash-..."`) before checking out target state.

## Decision Outcome
**Option 3 was chosen**:
- Built backend endpoints in `webui/api/spaces.py`:
  - `GET /api/spaces/checkpoints`: Returns recent Git commits, active branch, author, and dirty working tree status.
  - `GET /api/spaces/diff`: Generates unified diffs between commits or against the working tree.
  - `POST /api/spaces/rollback`: Creates a timestamped untracked safety stash before safely checking out the target commit or resetting modifications.
- Integrated an interactive Checkpoints list and Commit Diff modal into the Web UI Spaces tab.
- Added comprehensive hermetic test coverage in `tests/test_rollback.py`.

## Consequences & Linked Systems
- Works seamlessly with [[architecture/artifact_canvas_and_diff_viewer]] for visual code inspection.
- Protects developer working state aligned with [[user/conventions]].
- Integrated into the containerized execution model described in [[architecture/cagy_unified]].
