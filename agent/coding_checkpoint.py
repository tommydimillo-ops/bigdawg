"""Phase 10 increment 1 -- git-ref-based checkpoint/rollback, the
precondition for a real CodingAgent rather than a companion feature to it
(see `.relay/PHASE10-DESIGN.md` section 0). Pure runtime machinery: no
ToolSpec, nothing wired to anything yet. A model never chooses to call
this directly -- exposing checkpoint/restore as a tool would let a model
roll back the user's own work on its own judgment, which is exactly the
kind of enforced-boundary-by-code-not-by-model-judgment this project
never trades away.

Checkpoint mechanism, and why: a private git ref
(refs/jarvis/checkpoints/<request_id>), written via a scratch
GIT_INDEX_FILE, so creating a checkpoint writes neither the real index
nor HEAD --

    GIT_INDEX_FILE=<tmp> git add -A
    GIT_INDEX_FILE=<tmp> git write-tree      -> <tree>
    git commit-tree <tree> -p HEAD -m "..."  -> <commit>
    git update-ref refs/jarvis/checkpoints/<request_id> <commit>

A ref outside refs/heads/ never appears in `git log`, `git branch`, or a
push, and rollback is `git restore --source=<ref> -- <paths>` (a
path-scoped restore, never `git reset --hard`) -- both deliberately avoid
the two rejected alternatives (a branch commit, which pollutes history
and fights the user's own git state; a temp-directory file copy, which
races with a concurrent editor/session and has no natural diff). This
also specifically survives the scenario that sank a plain file-snapshot
design: this repo can have a second, concurrent Claude Code session with
its own uncommitted edits in the same working tree (relay mode's whole
premise) -- a scratch index and a non-heads ref mean creation writes
nothing a concurrent `git status`/`git add`/`git commit` in that same
tree would see, and takes no lock it would race with.

That last claim was NOT true as originally shipped, and the earlier
wording of this docstring ("the real index and HEAD are never touched")
was wrong with it. The plan-b6 audit found the dirty-path baseline,
a plain `git status --porcelain`, opportunistically REWRITES the real
.git/index under index.lock whenever a tracked file's stat data is stale
-- so a concurrent `git add` landing in that window failed (7 of 148 in
a stress run). Every git call here now runs with GIT_OPTIONAL_LOCKS=0
(see _run_git), which is what makes the claim true: with it, creation
was measured to leave the index bytes and HEAD unchanged and to take no
index lock. Rollback is different and stays different: `git restore`
takes the real index lock because it needs it (in testing it did not
rewrite the index's contents), so a held OR stale .git/index.lock blocks
rollback. That fails cleanly -- nothing is corrupted and the checkpoint
ref survives for manual recovery -- but rollback does depend on the
real index being usable.

What a snapshot cannot cover: gitignored files. `git add -A` honors
.gitignore, so an ignored path is in no snapshot, is invisible to
changed_paths_since, and cannot be told apart from "did not exist".
Three guards follow from that, none of them a substitute for the others:
agent/agents/coding.py refuses to write any gitignored path (so an edit
no checkpoint could recover is never made), restore_paths refuses --
never deletes -- an ignored path absent from the snapshot, and that
same write path never lets the agent edit a .gitignore (so ignore status
is stable for the length of a run, which is what makes it a sound way to
tell "the agent created this" from "this predates the checkpoint").

The one rule the whole design rests on: `dirty_at_checkpoint` is recorded
at checkpoint time, and `restore_paths` refuses to touch any path that
was already dirty then. Restoring a checkpoint over a path the user (or
another process) had already changed before the agent even started would
silently discard THEIR work, not the agent's -- this must never be
softened for convenience. Everything else about "should this actually be
rolled back" (agent-write-failure vs. needs-a-human-decision) is a policy
call for CodingAgent/the agent manager to make using `dirty_at_checkpoint`
and this module's return values, not something this module decides for
them -- this module only ever answers "what was here, and can I safely
put it back," never "should I."
"""
import fcntl
import os
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional

from agent.audit import log_action

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GIT_TIMEOUT_SECONDS = 15
_REF_PREFIX = "refs/jarvis/checkpoints/"


class CheckpointError(Exception):
    """Base for every error this module raises -- a caller that only
    wants to know "did checkpointing work" can catch this one class."""


class CheckpointCreationFailed(CheckpointError):
    pass


class CheckpointRestoreFailed(CheckpointError):
    pass


class PathOutsideRepository(CheckpointError):
    pass


@dataclass(frozen=True)
class Checkpoint:
    request_id: str
    ref: str
    commit_sha: str
    repo_root: str
    dirty_at_checkpoint: bool
    dirty_paths_at_checkpoint: List[str]
    created_at: float


def _run_git(args: List[str], cwd: str, env: Optional[dict] = None, check: bool = True):
    # GIT_OPTIONAL_LOCKS=0 on every call, not just `status`: git otherwise
    # opportunistically refreshes and REWRITES the real .git/index (under
    # index.lock) during a plain read-only `git status --porcelain`, which
    # _dirty_paths runs on every create_checkpoint. A human's concurrent
    # `git add`/VS Code stage that lands in that window fails with "Unable
    # to create '.git/index.lock'" -- reproduced by the plan-b6 audit: 7 of
    # 148 concurrent `git add` calls failed without this, 0 of 132 with it.
    # It only suppresses *optional* locks; a command that genuinely needs
    # the index (`git restore`) still takes it.
    full_env = dict(os.environ if env is None else env)
    full_env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            ["git"] + args, cwd=cwd, env=full_env, capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise CheckpointError(f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s") from error
    if check and result.returncode != 0:
        raise CheckpointError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _git_path(repo_root: str, name: str) -> str:
    """Resolves a filename to its real location inside the git directory,
    via `git rev-parse --git-path` rather than assuming `<repo_root>/.git`
    is a directory -- true for an ordinary checkout, but `.git` is a
    plain *file* (pointing at the real gitdir elsewhere) inside a linked
    worktree (`git worktree add`). Found for real, not by inspection:
    dogfooding this module's own `create_checkpoint` from inside a
    worktree failed with a real `fatal: Unable to create '.../.git/
    jarvis-checkpoint-index-...: Not a directory` before this existed.
    `--git-path` returns an absolute path for a worktree and a relative
    one for an ordinary checkout; `os.path.join` handles both correctly
    (an absolute second argument wins outright, a relative one joins onto
    repo_root), so no case split is needed here."""
    result = _run_git(["rev-parse", "--git-path", name], cwd=repo_root)
    return os.path.join(repo_root, result.stdout.strip())


def _resolve_repo_root(repo_root: Optional[str]) -> str:
    # Defaults to None, read here rather than bound as a function
    # parameter default -- the same fix Phase 9 Reliability S1 made
    # project-wide (agent/history_store.py, agent/personal_context.py)
    # after finding a parameter default is captured at *function
    # definition* time, not call time, so a test's module-constant
    # redirect would silently never take effect for a caller that never
    # passes repo_root explicitly.
    return repo_root if repo_root is not None else _PROJECT_ROOT


def confine_to_repo(repo_root: str, path: str) -> str:
    """Resolves `path` against repo_root and returns it repo-relative,
    raising PathOutsideRepository if it would land outside repo_root, or
    inside .git -- same resolve-then-prefix-check pattern
    tools/sandbox_python.py already uses for its own write confinement.
    Public (not just used internally by restore_paths) so
    agent/agents/coding.py's own write_file tool enforces the exact same
    confinement rather than a second, independently-written copy that
    could drift from this one."""
    real_root = os.path.realpath(repo_root)
    candidate = path if os.path.isabs(path) else os.path.join(repo_root, path)
    real_candidate = os.path.realpath(candidate)
    if real_candidate != real_root and not real_candidate.startswith(real_root + os.sep):
        raise PathOutsideRepository(f"'{path}' resolves outside the repository root")
    rel = os.path.relpath(real_candidate, real_root)
    if rel == ".git" or rel.startswith(f".git{os.sep}"):
        # Not "repo source" by any definition increment 1 uses -- this
        # module's own plumbing commands touch .git internally, but a
        # caller-supplied path (ultimately traceable to whatever
        # CodingAgent decided to edit) must never be able to reach a
        # hook, config, or ref through this same interface.
        raise PathOutsideRepository(f"'{path}' resolves inside .git, which is never a valid target")
    return rel


def is_gitignored(repo_root: str, rel_path: str) -> bool:
    """Whether `rel_path` (repo-relative, already through confine_to_repo)
    is matched by an ignore rule. This is the one question the checkpoint
    snapshot itself cannot answer: `git add -A` into the scratch index
    skips ignored files, so an ignored path is absent from every snapshot
    -- but so is a brand-new, legitimate file, so "absent from the
    snapshot" cannot stand in for "ignored". Found by the plan-b6 audit:
    a gitignored file is invisible to create_checkpoint and
    changed_paths_since, and restore_paths used to delete one outright.

    `--no-index` on purpose: a file that is force-tracked but also matches
    an ignore rule is still excluded from the scratch-index snapshot
    (that index starts empty, so every path is judged as untracked), and
    "no checkpoint can cover it" is exactly the property being asked.
    Pattern-only, so it works for a path that does not exist yet.

    Fails closed: if git cannot answer (not a repository, timeout, any
    exit code other than 0/1) this raises CheckpointError rather than
    guessing "not ignored" -- a caller deciding whether a write is safe
    must never read a git failure as permission."""
    result = _run_git(["check-ignore", "-q", "--no-index", "--", rel_path], cwd=repo_root, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise CheckpointError(f"git check-ignore failed for '{rel_path}': {result.stderr.strip()}")


def _dirty_paths(repo_root: str) -> List[str]:
    """Every path `git status --porcelain` reports as modified, staged,
    deleted, or untracked. Known limitation, acceptable for increment 1's
    scope (a coding task touching a small number of named files, not bulk
    renames): a rename line's "old -> new" is reduced to just "new" --
    good enough for the dirty-path safety check below, not a general
    porcelain parser."""
    result = _run_git(["status", "--porcelain"], cwd=repo_root)
    paths = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        paths.append(line[3:].split(" -> ")[-1])
    return paths


def create_checkpoint(request_id: str, repo_root: Optional[str] = None) -> Checkpoint:
    """Snapshots the current working tree into
    refs/jarvis/checkpoints/<request_id>. Must be called before a coding
    task's first write -- dirty_at_checkpoint/dirty_paths_at_checkpoint
    are only meaningful as a "what was already here before the agent
    touched anything" baseline."""
    root = _resolve_repo_root(repo_root)
    _run_git(["rev-parse", "--is-inside-work-tree"], cwd=root)

    dirty_paths = _dirty_paths(root)
    head = _run_git(["rev-parse", "HEAD"], cwd=root).stdout.strip()

    tmp_index = _git_path(root, f"jarvis-checkpoint-index-{request_id}-{os.getpid()}")
    env = dict(os.environ, GIT_INDEX_FILE=tmp_index)
    try:
        _run_git(["add", "-A"], cwd=root, env=env)
        tree = _run_git(["write-tree"], cwd=root, env=env).stdout.strip()
    finally:
        if os.path.exists(tmp_index):
            os.remove(tmp_index)

    # The trailing seq=<nanoseconds> exists solely for prune_checkpoints'
    # ordering -- git commit timestamps (%(creatordate)) only have
    # 1-second resolution, so two checkpoints created within the same
    # second (easily happens: a fast automated run, or a batch
    # dispatching more than one "coding" subtask close together) tie, and
    # the tie-break is not creation order. Found for real, not assumed:
    # a 3-checkpoint test created within one second pruned the WRONG one
    # under plain --sort=-creatordate before this existed.
    commit_result = _run_git(
        [
            "commit-tree", tree, "-p", head,
            "-m", f"jarvis checkpoint {request_id} seq={time.time_ns()}",
        ],
        cwd=root,
    )
    commit = commit_result.stdout.strip()

    ref = f"{_REF_PREFIX}{request_id}"
    _run_git(["update-ref", ref, commit], cwd=root)

    checkpoint = Checkpoint(
        request_id=request_id, ref=ref, commit_sha=commit, repo_root=root,
        dirty_at_checkpoint=bool(dirty_paths), dirty_paths_at_checkpoint=dirty_paths,
        created_at=time.time(),
    )
    log_action(
        "coding_checkpoint_created", {"request_id": request_id},
        f"ref={ref} commit={commit} dirty_at_checkpoint={checkpoint.dirty_at_checkpoint}",
    )
    return checkpoint


def _checkpointed_content(checkpoint: Checkpoint, path: str) -> Optional[str]:
    """Returns the path's content at checkpoint time, or None if it did
    not exist then. One call answers both "did it exist" and "what was
    in it," so restore_paths doesn't need a separate existence check."""
    result = _run_git(
        ["cat-file", "-p", f"{checkpoint.ref}:{path}"], cwd=checkpoint.repo_root, check=False,
    )
    return result.stdout if result.returncode == 0 else None


def existed_at_checkpoint(checkpoint: Checkpoint, path: str) -> bool:
    """Public wrapper over _checkpointed_content for callers that only
    need the existence question, not the content -- e.g. CodingAgent's
    own final verification distinguishing a brand-new file (worth an
    extra "did this actually get collected as a test" check) from one
    that already existed (already covered by ordinary discovery)."""
    rel = confine_to_repo(checkpoint.repo_root, path)
    return _checkpointed_content(checkpoint, rel) is not None


def _in_snapshot(checkpoint: Checkpoint, path: str) -> bool:
    """Whether `path` is in the checkpoint's tree, asked of `git ls-tree`
    rather than inferred from _checkpointed_content failing. That
    function returns None for ANY `git cat-file` failure, so a transient
    or corrupt-object error was indistinguishable from "did not exist
    then" -- and restore_paths acts on "did not exist" by deleting the
    file. Here a genuine git failure raises CheckpointError instead of
    reading as absence."""
    result = _run_git(["ls-tree", "--name-only", checkpoint.ref, "--", path], cwd=checkpoint.repo_root)
    return bool(result.stdout.strip())


@contextmanager
def _restore_lock(repo_root: str):
    """Blocking -- deliberately NOT the skip-if-busy pattern
    agent/scheduler_lock.py and agent/browser_lock.py use, since a queued
    rollback must still happen, not be silently dropped because another
    one was already in flight.

    Confirmed necessary by direct reproduction, not assumed: barrier-
    synchronized real OS processes calling create_checkpoint()
    concurrently against the same repo (40+ calls across multiple rounds)
    produced zero errors and a clean `git fsck` every time -- its scratch
    GIT_INDEX_FILE never touches the real index, so nothing there needed
    a lock. The identical experiment against restore_paths(), one process
    per distinct path, failed with a real `fatal: Unable to create
    '.git/index.lock': File exists` in every round tested -- `git
    restore` operates on the real index by design and has no scratch-
    index escape hatch. This lock exists because of that specific,
    reproduced failure, not as generic caution."""
    lock_path = _git_path(repo_root, "jarvis-checkpoint-restore.lock")
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def restore_paths(checkpoint: Checkpoint, paths: List[str]) -> List[str]:
    """Restores exactly the named paths to their checkpoint-time content,
    or deletes them if they did not exist at checkpoint time (a plain
    `git restore` cannot express that -- verified empirically: it errors
    on a path absent from the source tree rather than deleting it, so
    that case is handled explicitly here). Returns the list of paths
    actually changed -- a path already identical to its checkpoint
    content (or one that never existed either way) is not "changed" and
    is left untouched, not just silently re-written to the same bytes.

    Refuses (CheckpointRestoreFailed, naming the path), and changes
    NOTHING, when it cannot prove a change is safe:

    - a path that was already dirty at checkpoint time -- restoring over
      it would discard the user's work, not the agent's; see this
      module's docstring for why this is non-negotiable;
    - a path in the snapshot whose content cannot be read back;
    - a path that exists now but is absent from the snapshot AND is
      gitignored. Found by the plan-b6 audit: an ignored file is never
      snapshotted (the scratch-index `git add -A` honors .gitignore), so
      "absent from the snapshot" is ambiguous for it -- it may have
      existed before the checkpoint and simply been unrecordable, and
      this function used to delete such a file and report success. For a
      path that is NOT ignored the ambiguity does not exist: anything
      present at checkpoint time and not ignored is in the snapshot, so
      absence means the agent created it, and removing it is the correct
      rollback. Ignore status is only a sound discriminator while the
      ignore rules are unchanged since the checkpoint; CodingAgent's
      write path guarantees that by never letting the agent write a
      .gitignore (agent/agents/coding.py's denylist). A caller that
      cannot promise the same must not rely on this for ignored paths.

    Two-phase: every path is validated before any is changed, so a
    refusal never leaves the tree half-rolled-back.

    Serialized across concurrent callers via _restore_lock -- see that
    function's docstring for the real, reproduced race this closes."""
    changed = []
    with _restore_lock(checkpoint.repo_root):
        plan = []
        for path in paths:
            rel = confine_to_repo(checkpoint.repo_root, path)
            if rel in checkpoint.dirty_paths_at_checkpoint:
                raise CheckpointRestoreFailed(
                    f"refusing to restore '{rel}': it was already dirty at checkpoint time, "
                    "before the agent made any change"
                )
            abs_path = os.path.join(checkpoint.repo_root, rel)
            in_snapshot = _in_snapshot(checkpoint, rel)
            checkpointed = _checkpointed_content(checkpoint, rel) if in_snapshot else None
            if in_snapshot and checkpointed is None:
                raise CheckpointRestoreFailed(
                    f"refusing to restore '{rel}': it is in the checkpoint but its content "
                    "could not be read back"
                )
            current = None
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r", errors="surrogateescape") as file:
                        current = file.read()
                except OSError:
                    current = None  # unreadable -- fall through and restore/delete rather than skip
            if not in_snapshot and current is not None and is_gitignored(checkpoint.repo_root, rel):
                raise CheckpointRestoreFailed(
                    f"refusing to delete '{rel}': it is absent from the checkpoint snapshot and "
                    "gitignored, so it may have existed before the checkpoint (ignored files are "
                    "never snapshotted) -- rollback cannot tell it apart from a file the agent created"
                )
            plan.append((rel, abs_path, checkpointed, current))

        for rel, abs_path, checkpointed, current in plan:
            if checkpointed is not None:
                if current == checkpointed:
                    continue
                _run_git(["restore", "--source", checkpoint.ref, "--", rel], cwd=checkpoint.repo_root)
            elif current is not None:
                os.remove(abs_path)
            else:
                continue
            changed.append(rel)

    log_action(
        "coding_checkpoint_restored", {"request_id": checkpoint.request_id, "paths": paths},
        f"restored {len(changed)} of {len(paths)} requested path(s)",
    )
    return changed


def changed_paths_since(checkpoint: Checkpoint) -> List[str]:
    """The authoritative answer to "what actually differs between the
    checkpoint and the working tree right now" -- a tree-to-tree diff,
    not a self-reported files_written list (design doc section 7 flags
    trusting the latter as circular: an agent's own claim about what it
    touched isn't independent evidence of what it touched). Built via the
    same scratch-index technique create_checkpoint uses to capture a
    "current state" tree first -- a plain `git diff <ref>` alone would
    miss brand-new untracked files, since diff never reports untracked
    paths on its own (verified empirically); comparing two full trees
    does not have that gap."""
    root = checkpoint.repo_root
    tmp_index = _git_path(root, f"jarvis-checkpoint-diff-{checkpoint.request_id}-{os.getpid()}")
    env = dict(os.environ, GIT_INDEX_FILE=tmp_index)
    try:
        _run_git(["add", "-A"], cwd=root, env=env)
        current_tree = _run_git(["write-tree"], cwd=root, env=env).stdout.strip()
    finally:
        if os.path.exists(tmp_index):
            os.remove(tmp_index)

    result = _run_git(["diff", "--name-only", checkpoint.commit_sha, current_tree], cwd=root)
    return [line for line in result.stdout.splitlines() if line]


_SEQ_PATTERN = re.compile(r"seq=(\d+)")


def prune_checkpoints(keep_last: int, repo_root: Optional[str] = None) -> List[str]:
    """Deletes all but the `keep_last` most recently created checkpoint
    refs. Deleting a ref, never a branch or a commit -- nothing else in a
    normal git workflow (log, branch, push, gc of reachable objects) will
    ever see or be affected by these either way. Called automatically at
    the end of every CodingAgent.execute() run once coding_agent_enabled
    is on; config.settings.coding_checkpoint_retention_count is the
    default `keep_last`.

    Ordered by the seq=<nanoseconds> create_checkpoint embeds in its own
    commit message, not `--sort=-creatordate` -- git commit timestamps
    only have 1-second resolution, so checkpoints created within the same
    second (a fast run, or a batch dispatching more than one "coding"
    subtask close together) tie under creatordate, and the tie-break is
    not creation order. Found for real: a 3-checkpoint test created
    within one second pruned the wrong one before this existed. A ref
    predating this fix (no `seq=` in its subject) sorts as oldest rather
    than raising -- graceful degradation, not a hard requirement that
    every ref in the repo was created by the current code."""
    root = _resolve_repo_root(repo_root)
    result = _run_git(
        ["for-each-ref", "--format=%(refname)%00%(contents:subject)", _REF_PREFIX], cwd=root,
    )
    entries = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        refname, _, subject = line.partition("\0")
        match = _SEQ_PATTERN.search(subject)
        entries.append((int(match.group(1)) if match else 0, refname))
    entries.sort(key=lambda entry: entry[0], reverse=True)

    pruned = []
    for _, ref in entries[keep_last:]:
        _run_git(["update-ref", "-d", ref], cwd=root)
        pruned.append(ref)
    if pruned:
        log_action("coding_checkpoint_pruned", {"keep_last": keep_last}, f"pruned {len(pruned)} old checkpoint ref(s)")
    return pruned
