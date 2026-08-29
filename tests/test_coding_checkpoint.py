"""Tests for agent/coding_checkpoint.py -- Phase 10 increment 1's
git-ref-based checkpoint/rollback machinery.

Every test runs real `git` subprocess calls against a real, throwaway
git repository created fresh in a temp directory per test -- never the
real CampusPilot repo, never mocked. Mocking `subprocess.run` here would
mean testing nothing real: git's own behavior (does `restore` on a path
absent from the source tree error or delete; does a scratch
GIT_INDEX_FILE really leave the real index/HEAD untouched) is exactly
what increment 1 needs verified, the same reasoning
tests/test_history_store.py already applies to real SQLite/WAL behavior
rather than mocking it away.

Run with: python -m unittest tests.test_coding_checkpoint -v
"""
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import unittest

import agent.coding_checkpoint as ckpt
from agent.audit import LOG_FILE, recent_actions


def _git(repo, *args):
    result = subprocess.run(
        ["git"] + list(args), cwd=repo, capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout


def _concurrent_create_worker(repo, request_id, barrier, result_queue):
    # Module-level, not a closure/method -- picklable under macOS's
    # default 'spawn' multiprocessing start method (same requirement
    # tests/test_history_store.py's own multiprocess worker documents).
    try:
        barrier.wait(timeout=10)
        checkpoint = ckpt.create_checkpoint(request_id, repo_root=repo)
        result_queue.put(("ok", request_id, checkpoint.commit_sha))
    except Exception as error:
        result_queue.put(("error", request_id, f"{type(error).__name__}: {error}"))


def _concurrent_restore_worker(repo, idx, barrier, result_queue):
    try:
        request_id = f"interleave-{idx}"
        path = f"file_{idx}.txt"
        barrier.wait(timeout=15)
        checkpoint = ckpt.create_checkpoint(request_id, repo_root=repo)
        with open(os.path.join(repo, path), "w") as file:
            file.write(f"modified by {idx}\n")
        restored = ckpt.restore_paths(checkpoint, [path])
        with open(os.path.join(repo, path)) as file:
            content = file.read()
        ok = content == f"original {idx}\n"
        result_queue.put(("ok" if ok else "MISMATCH", idx, content, restored))
    except Exception as error:
        result_queue.put(("error", idx, f"{type(error).__name__}: {error}", None))


class CheckpointTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="jarvis-checkpoint-test-")
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@test.com")
        _git(self.repo, "config", "user.name", "test")
        with open(os.path.join(self.repo, "tracked.txt"), "w") as f:
            f.write("original content\n")
        with open(os.path.join(self.repo, "to_delete.txt"), "w") as f:
            f.write("will be deleted\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "initial")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _write(self, relpath, content):
        with open(os.path.join(self.repo, relpath), "w") as f:
            f.write(content)

    def _read(self, relpath):
        with open(os.path.join(self.repo, relpath)) as f:
            return f.read()


class TestCreateCheckpoint(CheckpointTestCase):
    def test_returns_resolvable_ref_and_commit(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self.assertEqual(checkpoint.ref, "refs/jarvis/checkpoints/req-1")
        resolved = _git(self.repo, "rev-parse", checkpoint.ref).strip()
        self.assertEqual(resolved, checkpoint.commit_sha)

    def test_checkpoint_commit_parents_real_head(self):
        head = _git(self.repo, "rev-parse", "HEAD").strip()
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        parents = _git(self.repo, "log", "--pretty=%P", "-1", checkpoint.commit_sha).strip()
        self.assertEqual(parents, head)

    def test_does_not_touch_log_or_branches(self):
        before_log = _git(self.repo, "log", "--oneline")
        before_branch = _git(self.repo, "branch")
        ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self.assertEqual(_git(self.repo, "log", "--oneline"), before_log)
        self.assertEqual(_git(self.repo, "branch"), before_branch)

    def test_does_not_touch_real_index_or_head(self):
        # A file staged for a real, unrelated commit-in-progress must
        # survive checkpointing untouched -- this is the whole point of
        # using a scratch GIT_INDEX_FILE instead of the real index.
        self._write("staged_for_real.txt", "staged by the user, not the agent\n")
        _git(self.repo, "add", "staged_for_real.txt")
        real_head_before = _git(self.repo, "rev-parse", "HEAD").strip()
        ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD").strip(), real_head_before)
        status = _git(self.repo, "status", "--porcelain")
        self.assertIn("staged_for_real.txt", status)
        self.assertTrue(status.startswith("A "))

    def test_clean_tree_records_not_dirty(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self.assertFalse(checkpoint.dirty_at_checkpoint)
        self.assertEqual(checkpoint.dirty_paths_at_checkpoint, [])

    def test_dirty_tree_records_modified_and_untracked_paths(self):
        self._write("tracked.txt", "modified before the agent even started\n")
        self._write("pre_existing_untracked.txt", "was already here\n")
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self.assertTrue(checkpoint.dirty_at_checkpoint)
        self.assertIn("tracked.txt", checkpoint.dirty_paths_at_checkpoint)
        self.assertIn("pre_existing_untracked.txt", checkpoint.dirty_paths_at_checkpoint)

    def test_snapshot_includes_untracked_files(self):
        self._write("was_untracked.txt", "captured by add -A\n")
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"{checkpoint.ref}:was_untracked.txt"],
            cwd=self.repo, capture_output=True,
        )
        self.assertEqual(exists.returncode, 0)

    def test_two_requests_get_independent_checkpoints(self):
        first = ckpt.create_checkpoint("req-a", repo_root=self.repo)
        self._write("tracked.txt", "changed between checkpoints\n")
        second = ckpt.create_checkpoint("req-b", repo_root=self.repo)
        self.assertNotEqual(first.ref, second.ref)
        self.assertNotEqual(first.commit_sha, second.commit_sha)

    def test_raises_outside_a_git_repository(self):
        not_a_repo = tempfile.mkdtemp(prefix="jarvis-not-a-repo-")
        try:
            with self.assertRaises(ckpt.CheckpointError):
                ckpt.create_checkpoint("req-1", repo_root=not_a_repo)
        finally:
            shutil.rmtree(not_a_repo, ignore_errors=True)

    def test_logs_creation_via_audit(self):
        ckpt.create_checkpoint("req-audit-create", repo_root=self.repo)
        actions = [a for a in recent_actions(limit=50) if a["tool"] == "coding_checkpoint_created"]
        self.assertTrue(actions, f"expected an audit entry in {LOG_FILE}")
        self.assertIn("req-audit-create", actions[-1]["input"])

    def test_dirty_paths_reduces_a_rename_to_just_the_new_path(self):
        # _dirty_paths' own docstring documents this reduction ("old ->
        # new" collapsed to just "new") but until now had no dedicated
        # test proving it -- this is the actual regression test a real
        # CodingAgent dogfood run (Phase 10 increment 1's first real,
        # live task) was asked to write. Its own generated version used
        # bare assert + a plain function, this project's exclusive
        # unittest.TestCase convention doesn't collect that at all --
        # `python -m unittest discover` silently never ran it, so
        # "success": true from that run did not actually mean this
        # behavior was verified. Added here for real, in the convention
        # this project's own suite actually collects.
        _git(self.repo, "mv", "tracked.txt", "renamed.txt")
        self.assertEqual(ckpt._dirty_paths(self.repo), ["renamed.txt"])


class TestRestorePaths(CheckpointTestCase):
    def test_reverts_a_modified_file(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self._write("tracked.txt", "MODIFIED BY AGENT\n")
        changed = ckpt.restore_paths(checkpoint, ["tracked.txt"])
        self.assertEqual(changed, ["tracked.txt"])
        self.assertEqual(self._read("tracked.txt"), "original content\n")

    def test_recreates_a_deleted_file(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        os.remove(os.path.join(self.repo, "to_delete.txt"))
        changed = ckpt.restore_paths(checkpoint, ["to_delete.txt"])
        self.assertEqual(changed, ["to_delete.txt"])
        self.assertEqual(self._read("to_delete.txt"), "will be deleted\n")

    def test_deletes_a_file_the_agent_created_that_did_not_exist_at_checkpoint(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self._write("new_by_agent.txt", "brand new\n")
        changed = ckpt.restore_paths(checkpoint, ["new_by_agent.txt"])
        self.assertEqual(changed, ["new_by_agent.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "new_by_agent.txt")))

    def test_unchanged_path_is_not_reported_as_changed(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        changed = ckpt.restore_paths(checkpoint, ["tracked.txt"])
        self.assertEqual(changed, [])

    def test_refuses_a_path_dirty_at_checkpoint_time(self):
        self._write("tracked.txt", "dirty before the agent started\n")
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self._write("tracked.txt", "agent changed it further\n")
        with self.assertRaises(ckpt.CheckpointRestoreFailed):
            ckpt.restore_paths(checkpoint, ["tracked.txt"])
        # Refusing must not have touched the file either way.
        self.assertEqual(self._read("tracked.txt"), "agent changed it further\n")

    def test_refuses_a_path_outside_the_repo_root(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        with self.assertRaises(ckpt.PathOutsideRepository):
            ckpt.restore_paths(checkpoint, ["../../etc/passwd"])

    def test_refuses_a_path_inside_dot_git(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        with self.assertRaises(ckpt.PathOutsideRepository):
            ckpt.restore_paths(checkpoint, [".git/config"])

    def test_restores_multiple_paths_independently(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self._write("tracked.txt", "changed\n")
        self._write("new_by_agent.txt", "new\n")
        changed = ckpt.restore_paths(checkpoint, ["tracked.txt", "new_by_agent.txt"])
        self.assertEqual(set(changed), {"tracked.txt", "new_by_agent.txt"})
        self.assertEqual(self._read("tracked.txt"), "original content\n")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "new_by_agent.txt")))

    def test_logs_restore_via_audit(self):
        checkpoint = ckpt.create_checkpoint("req-audit-restore", repo_root=self.repo)
        self._write("tracked.txt", "changed\n")
        ckpt.restore_paths(checkpoint, ["tracked.txt"])
        actions = [a for a in recent_actions(limit=50) if a["tool"] == "coding_checkpoint_restored"]
        self.assertTrue(actions, f"expected an audit entry in {LOG_FILE}")


class TestExistedAtCheckpoint(CheckpointTestCase):
    def test_true_for_a_path_present_at_checkpoint_time(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self.assertTrue(ckpt.existed_at_checkpoint(checkpoint, "tracked.txt"))

    def test_false_for_a_path_created_after_checkpoint_time(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self._write("new_by_agent.txt", "new\n")
        self.assertFalse(ckpt.existed_at_checkpoint(checkpoint, "new_by_agent.txt"))

    def test_refuses_a_path_outside_the_repo(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        with self.assertRaises(ckpt.PathOutsideRepository):
            ckpt.existed_at_checkpoint(checkpoint, "../outside.txt")


class TestChangedPathsSince(CheckpointTestCase):
    def test_no_changes_reports_nothing(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self.assertEqual(ckpt.changed_paths_since(checkpoint), [])

    def test_detects_a_modified_tracked_file(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self._write("tracked.txt", "modified\n")
        self.assertEqual(ckpt.changed_paths_since(checkpoint), ["tracked.txt"])

    def test_detects_a_brand_new_untracked_file(self):
        # The gap a plain `git diff <ref>` has and a tree-to-tree diff
        # does not: diff alone never reports untracked paths.
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self._write("new_by_agent.txt", "new\n")
        self.assertEqual(ckpt.changed_paths_since(checkpoint), ["new_by_agent.txt"])

    def test_detects_a_deleted_file(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        os.remove(os.path.join(self.repo, "to_delete.txt"))
        self.assertEqual(ckpt.changed_paths_since(checkpoint), ["to_delete.txt"])

    def test_matches_what_restore_paths_actually_changes(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.repo)
        self._write("tracked.txt", "modified\n")
        self._write("new_by_agent.txt", "new\n")
        candidates = ckpt.changed_paths_since(checkpoint)
        changed = ckpt.restore_paths(checkpoint, candidates)
        self.assertEqual(set(changed), set(candidates))


class TestPruneCheckpoints(CheckpointTestCase):
    def _refs(self):
        output = _git(self.repo, "for-each-ref", "--format=%(refname)", ckpt._REF_PREFIX)
        return [line for line in output.splitlines() if line]

    def test_keeps_most_recent_n(self):
        for i in range(5):
            ckpt.create_checkpoint(f"req-{i}", repo_root=self.repo)
        pruned = ckpt.prune_checkpoints(keep_last=2, repo_root=self.repo)
        self.assertEqual(len(pruned), 3)
        self.assertEqual(len(self._refs()), 2)

    def test_keeps_the_actual_most_recent_ones_not_just_the_right_count(self):
        # Real bug, caught by dogfooding rather than this test (which
        # only checked counts): checkpoints created within the same
        # second tie under git's 1-second-resolution creatordate, and
        # the tie-break was not creation order. Explicit request-id
        # ordering matches call order here since each request_id is
        # created strictly after the previous one returns.
        for i in range(5):
            ckpt.create_checkpoint(f"req-{i}", repo_root=self.repo)
        ckpt.prune_checkpoints(keep_last=2, repo_root=self.repo)
        remaining = self._refs()
        self.assertEqual(set(remaining), {"refs/jarvis/checkpoints/req-3", "refs/jarvis/checkpoints/req-4"})

    def test_under_the_limit_prunes_nothing(self):
        ckpt.create_checkpoint("req-only", repo_root=self.repo)
        pruned = ckpt.prune_checkpoints(keep_last=20, repo_root=self.repo)
        self.assertEqual(pruned, [])
        self.assertEqual(len(self._refs()), 1)

    def test_pruned_ref_is_actually_gone(self):
        ckpt.create_checkpoint("req-old", repo_root=self.repo)
        ckpt.create_checkpoint("req-new", repo_root=self.repo)
        ckpt.prune_checkpoints(keep_last=1, repo_root=self.repo)
        resolve = subprocess.run(
            ["git", "rev-parse", "refs/jarvis/checkpoints/req-old"],
            cwd=self.repo, capture_output=True,
        )
        self.assertNotEqual(resolve.returncode, 0)


class TestConcurrency(CheckpointTestCase):
    """Real, multi-process reproductions -- barrier-synchronized, never
    sleep-based, matching tests/test_history_store.py's own concurrency
    testing convention (a race is a genuine OS/filesystem-level
    condition, not a Python GIL artifact, so real separate processes are
    what production actually does, not just threads within one).

    This is not speculative coverage: create_checkpoint's own
    concurrency safety was verified by direct reproduction to need no
    lock at all (its scratch GIT_INDEX_FILE never touches the real
    index), and restore_paths's lock exists specifically because the
    identical experiment against it failed with a real
    `.git/index.lock` collision every round, before the lock was added."""

    def test_concurrent_create_checkpoint_needs_no_lock(self):
        barrier = multiprocessing.Barrier(8)
        result_queue = multiprocessing.Queue()
        procs = [
            multiprocessing.Process(
                target=_concurrent_create_worker, args=(self.repo, f"req-{i}", barrier, result_queue),
            )
            for i in range(8)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=15)

        results = [result_queue.get() for _ in range(8)]
        errors = [r for r in results if r[0] == "error"]
        self.assertEqual(errors, [], f"expected zero errors, got: {errors}")

        for _, request_id, commit_sha in results:
            resolved = _git(self.repo, "rev-parse", f"refs/jarvis/checkpoints/{request_id}").strip()
            self.assertEqual(resolved, commit_sha)

        fsck = subprocess.run(["git", "fsck", "--full"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(fsck.returncode, 0)
        self.assertEqual(fsck.stdout.strip(), "")
        self.assertEqual(fsck.stderr.strip(), "")

    def test_concurrent_restore_paths_is_safe_with_the_lock(self):
        n = 8
        for i in range(n):
            self._write(f"file_{i}.txt", f"original {i}\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "add per-worker files")

        barrier = multiprocessing.Barrier(n)
        result_queue = multiprocessing.Queue()
        procs = [
            multiprocessing.Process(target=_concurrent_restore_worker, args=(self.repo, i, barrier, result_queue))
            for i in range(n)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=25)

        results = [result_queue.get() for _ in range(n)]
        errors = [r for r in results if r[0] == "error"]
        mismatches = [r for r in results if r[0] == "MISMATCH"]
        self.assertEqual(errors, [], f"expected zero errors (this exact scenario failed on .git/index.lock before the lock was added): {errors}")
        self.assertEqual(mismatches, [], f"expected every file restored to its own original content: {mismatches}")


class TestLinkedWorktree(CheckpointTestCase):
    """A linked git worktree's `.git` is a plain FILE (pointing at the
    real gitdir elsewhere), not a directory -- unlike an ordinary
    checkout. Found for real by dogfooding CodingAgent from inside a
    worktree (the natural way to give it a clean tree to work in without
    disturbing the actual repo's own uncommitted state): create_checkpoint
    failed outright with a real `Not a directory` error before
    _git_path() existed. This class exercises the full checkpoint/
    restore/prune cycle against a genuine linked worktree of the
    fixture repo, not just _git_path() in isolation."""

    def setUp(self):
        super().setUp()
        self.worktree = tempfile.mkdtemp(prefix="jarvis-checkpoint-worktree-")
        os.rmdir(self.worktree)  # git worktree add requires the target not exist yet
        _git(self.repo, "worktree", "add", "--detach", self.worktree, "HEAD")
        self.assertTrue(os.path.isfile(os.path.join(self.worktree, ".git")), "expected .git to be a file, not a directory, in a linked worktree")

    def tearDown(self):
        _git(self.repo, "worktree", "remove", "--force", self.worktree)
        super().tearDown()

    def test_checkpoint_create_restore_and_prune_all_work(self):
        checkpoint = ckpt.create_checkpoint("req-1", repo_root=self.worktree)
        with open(os.path.join(self.worktree, "tracked.txt"), "w") as file:
            file.write("modified in the worktree\n")
        changed = ckpt.restore_paths(checkpoint, ["tracked.txt"])
        self.assertEqual(changed, ["tracked.txt"])
        with open(os.path.join(self.worktree, "tracked.txt")) as file:
            self.assertEqual(file.read(), "original content\n")
        pruned = ckpt.prune_checkpoints(keep_last=0, repo_root=self.worktree)
        self.assertEqual(pruned, [checkpoint.ref])

    def test_checkpoint_scratch_files_land_in_the_real_gitdir_not_the_worktree(self):
        # The whole point of _git_path(): a worktree's own directory has
        # no .git/ subdirectory to write scratch files into at all.
        real_gitdir = _git(self.worktree, "rev-parse", "--git-dir").strip()
        self.assertNotEqual(real_gitdir, ".git")
        self.assertFalse(os.path.isdir(os.path.join(self.worktree, ".git")))
        ckpt.create_checkpoint("req-1", repo_root=self.worktree)  # must not raise


if __name__ == "__main__":
    unittest.main()
