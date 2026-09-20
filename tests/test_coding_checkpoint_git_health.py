"""Characterization tests for agent/coding_checkpoint.py against the four
concerns Phase 10's design docs raised about git-based checkpointing
(plan-b6 item 1; findings in JarvisVault/Knowledge/Decisions/
Phase10-Checkpoint-Git-Vs-Byte-Level.md).

These pin what the shipped module ACTUALLY does today, real `git`
against a throwaway repo per test, never mocked. Tests named
`test_KNOWN_GAP_*` pin behavior that is a real defect or a real hole in
a guarantee the module's docstring implies -- they are here so the
finding stays re-checkable on every run, not because the behavior is
desired. When one of these gaps is fixed, its test SHOULD start failing;
flip its assertion (and update the decision note) rather than deleting it.

Deliberately no fix and no change to the module in this round.

Run with: python -m unittest tests.test_coding_checkpoint_git_health -v
"""
import os
import subprocess
import unittest
from unittest.mock import patch

import agent.agents.coding as coding
import agent.coding_checkpoint as ckpt
from agent.agents.coding import _is_never_writable
from agent.request_context import RequestContext
from tests.test_coding_checkpoint import CheckpointTestCase, _git

_REAL_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _index_bytes(repo):
    with open(os.path.join(repo, ".git", "index"), "rb") as file:
        return file.read()


def _fsck_clean(repo):
    result = subprocess.run(
        ["git", "fsck", "--no-dangling"], cwd=repo, capture_output=True, text=True, timeout=15,
    )
    return result.returncode == 0 and not result.stderr.strip()


class TestIndexContention(CheckpointTestCase):

    def _hold_index_lock(self):
        lock = os.path.join(self.repo, ".git", "index.lock")
        open(lock, "w").close()
        self.addCleanup(lambda: os.path.exists(lock) and os.remove(lock))
        return lock

    def test_create_checkpoint_succeeds_while_index_lock_is_held(self):
        # A concurrent `git add` (VS Code, another terminal) holds the
        # real index lock. The scratch GIT_INDEX_FILE means checkpoint
        # creation neither needs nor touches it.
        lock = self._hold_index_lock()
        index_before = _index_bytes(self.repo)
        checkpoint = ckpt.create_checkpoint("lock-create", repo_root=self.repo)
        self.assertEqual(_git(self.repo, "rev-parse", checkpoint.ref).strip(), checkpoint.commit_sha)
        self.assertTrue(os.path.exists(lock), "must not remove or steal someone else's lock")
        self.assertEqual(_index_bytes(self.repo), index_before)
        self.assertTrue(_fsck_clean(self.repo))

    def test_restore_fails_cleanly_while_index_lock_is_held_then_works_after_release(self):
        # `git restore` operates on the real index by design (see
        # _restore_lock's docstring), so a held OR STALE lock blocks
        # rollback entirely -- fails cleanly, corrupts nothing.
        checkpoint = ckpt.create_checkpoint("lock-restore", repo_root=self.repo)
        self._write("tracked.txt", "AGENT EDIT\n")
        lock = self._hold_index_lock()

        with self.assertRaises(ckpt.CheckpointError) as caught:
            ckpt.restore_paths(checkpoint, ["tracked.txt"])
        self.assertIn("index.lock", str(caught.exception))
        # Not CheckpointRestoreFailed: agent/agents/coding.py's
        # _attempt_rollback only catches that subclass, so this escapes to
        # execute()'s outer `except Exception` instead (see the decision
        # note) -- pinned so a change to either side is noticed.
        self.assertNotIsInstance(caught.exception, ckpt.CheckpointRestoreFailed)
        self.assertEqual(self._read("tracked.txt"), "AGENT EDIT\n")
        self.assertTrue(_fsck_clean(self.repo))

        os.remove(lock)
        self.assertEqual(ckpt.restore_paths(checkpoint, ["tracked.txt"]), ["tracked.txt"])
        self.assertEqual(self._read("tracked.txt"), "original content\n")

    def test_KNOWN_GAP_create_checkpoint_rewrites_the_real_index_via_git_status(self):
        # The module docstring says the real index is "never touched".
        # `_dirty_paths` runs a plain `git status --porcelain`, which
        # opportunistically refreshes and REWRITES .git/index (taking
        # index.lock briefly) when any tracked file's stat data is stale.
        # A concurrent `git add` that lands in that window fails with
        # "Unable to create '.git/index.lock'" (reproduced: 7 of 148 in an
        # 8s stress run; see the decision note). GIT_OPTIONAL_LOCKS=0
        # avoids it -- not applied by _run_git today.
        tracked = os.path.join(self.repo, "tracked.txt")

        os.utime(tracked, (1, 1))
        env_without = {k: v for k, v in os.environ.items() if k != "GIT_OPTIONAL_LOCKS"}
        with patch.dict(os.environ, env_without, clear=True):
            before = _index_bytes(self.repo)
            ckpt.create_checkpoint("status-refresh", repo_root=self.repo)
            self.assertNotEqual(_index_bytes(self.repo), before)

        os.utime(tracked, (2, 2))
        with patch.dict(os.environ, {**env_without, "GIT_OPTIONAL_LOCKS": "0"}, clear=True):
            before = _index_bytes(self.repo)
            ckpt.create_checkpoint("status-no-refresh", repo_root=self.repo)
            self.assertEqual(_index_bytes(self.repo), before)


class TestGitignoredFiles(CheckpointTestCase):

    def setUp(self):
        super().setUp()
        self._write(".gitignore", "ignored/\n*.secret\n")
        _git(self.repo, "add", ".gitignore")
        _git(self.repo, "commit", "-qm", "gitignore")
        os.makedirs(os.path.join(self.repo, "ignored"))
        self._write("ignored/pre.txt", "PRE-EXISTING ignored file\n")
        self._write("key.secret", "PRE-EXISTING ignored secret\n")

    def test_snapshot_excludes_gitignored_files_and_reports_them_as_never_existing(self):
        checkpoint = ckpt.create_checkpoint("ignored-snapshot", repo_root=self.repo)
        in_snapshot = _git(self.repo, "ls-tree", "-r", "--name-only", checkpoint.ref).splitlines()
        self.assertNotIn("ignored/pre.txt", in_snapshot)
        self.assertNotIn("key.secret", in_snapshot)
        self.assertFalse(ckpt.existed_at_checkpoint(checkpoint, "ignored/pre.txt"))
        self.assertFalse(checkpoint.dirty_at_checkpoint)

    def test_changed_paths_since_cannot_see_ignored_files_so_codingagent_refuses_to_write_them(self):
        # Was test_KNOWN_GAP_an_agent_edit_to_an_ignored_file_is_invisible_
        # to_changed_paths_since. The module-level limit is unchanged and
        # inherent (the snapshot is a `git add -A` tree, which honors
        # .gitignore) -- what closed the gap is that CodingAgent's write
        # path (plan-b7 item 1) now refuses any gitignored target, so an
        # invisible edit can no longer be produced through it. The first
        # half proves the limit still holds; the second proves the write
        # that would have exploited it is refused and leaves the file alone.
        checkpoint = ckpt.create_checkpoint("ignored-invisible", repo_root=self.repo)
        self._write("ignored/pre.txt", "edited outside CodingAgent's write path\n")
        self.assertEqual(ckpt.changed_paths_since(checkpoint), [])

        self._write("ignored/pre.txt", "PRE-EXISTING ignored file\n")
        context = RequestContext.create("t", source="agent_worker")
        files_written = []
        result = coding._write_file(self.repo, "ignored/pre.txt", "AGENT OVERWROTE\n", files_written, context)
        self.assertIn("Error: refusing to write", result)
        self.assertIn("gitignored", result)
        self.assertEqual(files_written, [])
        self.assertEqual(self._read("ignored/pre.txt"), "PRE-EXISTING ignored file\n")

    def test_KNOWN_GAP_restore_paths_deletes_a_preexisting_ignored_file_it_was_never_asked_to_change(self):
        # Data loss, silently reported as a successful rollback: an
        # ignored file is absent from the snapshot tree, so the module
        # concludes "did not exist at checkpoint" and os.remove()s it --
        # even though it existed then and the agent never touched it.
        checkpoint = ckpt.create_checkpoint("ignored-delete", repo_root=self.repo)
        changed = ckpt.restore_paths(checkpoint, ["key.secret"])
        self.assertEqual(changed, ["key.secret"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "key.secret")))

    def test_KNOWN_GAP_restore_paths_cannot_bring_back_an_overwritten_ignored_file(self):
        checkpoint = ckpt.create_checkpoint("ignored-overwrite", repo_root=self.repo)
        self._write("ignored/pre.txt", "AGENT OVERWROTE\n")
        ckpt.restore_paths(checkpoint, ["ignored/pre.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "ignored", "pre.txt")))

    def test_ignored_file_the_agent_created_is_removed_by_restore_only_by_accident_of_the_same_bug(self):
        checkpoint = ckpt.create_checkpoint("ignored-created", repo_root=self.repo)
        self._write("ignored/new.txt", "agent created\n")
        self.assertEqual(ckpt.restore_paths(checkpoint, ["ignored/new.txt"]), ["ignored/new.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "ignored", "new.txt")))


class TestGitignoredSecretsAreNotWritableByCodingAgent(unittest.TestCase):
    """Reachability of the gitignored-file gap above: the write denylist
    in agent/agents/coding.py is what stands between CodingAgent's
    write_file and these paths. It used to not cover them (the
    plan-b6 audit's severe finding); plan-b7 item 1 added them. Runs
    `git check-ignore` against the real repo's own .gitignore (pattern
    matching only -- no file need exist, nothing is read or written)."""

    def test_gitignored_sensitive_paths_are_on_the_write_denylist(self):
        # Was test_KNOWN_GAP_gitignored_sensitive_paths_are_not_on_the_write_denylist.
        for path in (".env", ".env.local", "JarvisVault/Knowledge/x.md", "logs/menubar.err.log", "graphify-out/graph.json"):
            with self.subTest(path=path):
                ignored = subprocess.run(
                    ["git", "check-ignore", "-q", path], cwd=_REAL_REPO, timeout=15,
                ).returncode == 0
                self.assertTrue(ignored, f"{path} is expected to be gitignored in this repo")
                self.assertTrue(_is_never_writable(path))


class TestGitUnhealthy(CheckpointTestCase):

    def test_detached_head_checkpoint_and_rollback_both_work(self):
        head = _git(self.repo, "rev-parse", "HEAD").strip()
        _git(self.repo, "checkout", "-q", "--detach", head)
        checkpoint = ckpt.create_checkpoint("detached", repo_root=self.repo)
        self._write("tracked.txt", "AGENT\n")
        self.assertEqual(ckpt.restore_paths(checkpoint, ["tracked.txt"]), ["tracked.txt"])
        self.assertEqual(self._read("tracked.txt"), "original content\n")

    def _start_conflicted_merge(self):
        _git(self.repo, "checkout", "-qb", "side")
        self._write("to_delete.txt", "side version\n")
        _git(self.repo, "commit", "-qam", "side")
        _git(self.repo, "checkout", "-q", "-")
        self._write("to_delete.txt", "main version\n")
        _git(self.repo, "commit", "-qam", "main")
        subprocess.run(["git", "merge", "side"], cwd=self.repo, capture_output=True, text=True, timeout=15)
        self.assertIn("UU to_delete.txt", _git(self.repo, "status", "--porcelain"))

    def test_conflicted_merge_checkpoint_records_the_conflict_and_still_restores_a_clean_path(self):
        self._start_conflicted_merge()
        checkpoint = ckpt.create_checkpoint("merge", repo_root=self.repo)
        self.assertTrue(checkpoint.dirty_at_checkpoint)
        self.assertEqual(checkpoint.dirty_paths_at_checkpoint, ["to_delete.txt"])

        self._write("tracked.txt", "AGENT\n")
        self.assertEqual(ckpt.restore_paths(checkpoint, ["tracked.txt"]), ["tracked.txt"])
        self.assertEqual(self._read("tracked.txt"), "original content\n")

        with self.assertRaises(ckpt.CheckpointRestoreFailed):
            ckpt.restore_paths(checkpoint, ["to_delete.txt"])
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")))

    def test_corrupt_index_before_checkpoint_fails_closed(self):
        with open(os.path.join(self.repo, ".git", "index"), "wb") as file:
            file.write(b"NOT A REAL INDEX" * 8)
        with self.assertRaises(ckpt.CheckpointError):
            ckpt.create_checkpoint("corrupt-before", repo_root=self.repo)

    def test_corrupt_index_after_checkpoint_blocks_rollback_but_the_ref_still_holds_the_original(self):
        # "Rollback still works when git itself is unhealthy" -- it does
        # not: restore_paths depends on the real index. What survives is
        # the checkpoint ref itself, so manual recovery is still possible.
        checkpoint = ckpt.create_checkpoint("corrupt-after", repo_root=self.repo)
        self._write("tracked.txt", "AGENT\n")
        with open(os.path.join(self.repo, ".git", "index"), "wb") as file:
            file.write(b"NOT A REAL INDEX" * 8)

        with self.assertRaises(ckpt.CheckpointError):
            ckpt.restore_paths(checkpoint, ["tracked.txt"])
        self.assertEqual(self._read("tracked.txt"), "AGENT\n")
        self.assertEqual(_git(self.repo, "show", f"{checkpoint.ref}:tracked.txt"), "original content\n")

    def test_repository_with_no_commits_fails_closed(self):
        empty = os.path.join(self.repo, "empty-repo")
        os.makedirs(empty)
        _git(empty, "init", "-q")
        with self.assertRaises(ckpt.CheckpointError):
            ckpt.create_checkpoint("unborn", repo_root=empty)


class TestConcurrentHumanEdit(CheckpointTestCase):

    def test_KNOWN_GAP_restore_silently_discards_a_human_edit_made_after_the_agents_write(self):
        # The only protection is dirty_paths_at_checkpoint (edits that
        # predate the checkpoint). Nothing records what the agent wrote,
        # so a later edit to the same file by anything else is
        # indistinguishable from the agent's and is overwritten without
        # any error. agent/agents/coding.py's files_written intersection
        # protects OTHER files, not this one.
        checkpoint = ckpt.create_checkpoint("human-edit", repo_root=self.repo)
        self._write("tracked.txt", "AGENT EDIT\n")
        with open(os.path.join(self.repo, "tracked.txt"), "a") as file:
            file.write("HUMAN EDIT made after the agent's write\n")

        ckpt.restore_paths(checkpoint, ["tracked.txt"])
        self.assertEqual(self._read("tracked.txt"), "original content\n")

    def test_KNOWN_GAP_restore_paths_itself_does_not_guard_a_path_the_agent_never_touched(self):
        # Module level only: the caller (coding.py) never passes such a
        # path, but nothing in restore_paths enforces that.
        checkpoint = ckpt.create_checkpoint("untouched", repo_root=self.repo)
        self._write("to_delete.txt", "HUMAN EDIT\n")
        self.assertEqual(ckpt.restore_paths(checkpoint, ["to_delete.txt"]), ["to_delete.txt"])
        self.assertEqual(self._read("to_delete.txt"), "will be deleted\n")


if __name__ == "__main__":
    unittest.main()
