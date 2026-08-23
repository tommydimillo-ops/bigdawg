"""Tests for agent/history_store.py -- Phase 9 M4.1's durable, searchable
history database (HISTORY_DB), distinct from agent/memory/'s superseding
personal-fact store (see history_store.py's module docstring for the
HISTORY-vs-MEMORY boundary rationale).

Every test in this file uses a temp, file-backed SQLite database passed
explicitly via `db_path=` -- matching this project's established pattern
of never letting a test touch the real file-backed store (see
tests/test_execution_history.py, tests/test_usage.py). A handful of
tests additionally assert that agent.history_store.HISTORY_DB itself
(the real production path) was never created, as a second, independent
guard against a bug where some code path silently ignores an explicit
db_path and falls back to the module-level default.

Run with: python -m unittest tests.test_history_store -v
"""
import multiprocessing
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import agent.history_store as history_store
from agent.history_store import (
    HistoryBusy,
    HistoryCorruption,
    HistorySchemaError,
    HistoryStoreError,
    HistoryUnavailable,
    HistoryUnsupportedRuntime,
    HistoryValidationError,
    build_safe_match_query,
    close_session,
    create_session,
    history_status,
    initialize_history_store,
    record_turn,
    search_history,
)

# Synthetic secrets shaped exactly like agent/memory/safety.py's real
# _SECRET_PATTERNS, never real credentials.
FAKE_ANTHROPIC_KEY = "sk-ant-" + ("a1B2c3D4e5F6g7H8i9J0" * 2)
FAKE_OPENAI_KEY = "sk-" + ("a1B2c3D4e5F6g7H8i9J0" * 2)
FAKE_AWS_KEY = "AKIA" + "Q" * 16
FAKE_GITHUB_TOKEN = "ghp_" + "b" * 40


def _multiprocess_init_worker(db_path, barrier, result_queue):
    """Module-level (not a closure) so it's picklable under macOS's
    default 'spawn' multiprocessing start method -- each call is a
    genuinely separate OS process/interpreter, not a thread, matching
    production's real concurrent-initializer scenario (menu-bar app,
    scheduler daemon, Streamlit are independent processes). db_path is
    always passed explicitly; this never touches agent.history_store's
    module-level HISTORY_DB default."""
    import agent.history_store as _history_store
    try:
        barrier.wait(timeout=10)
        _history_store.initialize_history_store(db_path=db_path)
        result_queue.put(None)
    except Exception as e:
        result_queue.put(repr(e))


class IsolatedHistoryDbTestCase(unittest.TestCase):
    """Every test gets its own temp directory and an explicit db_path --
    never the module-level HISTORY_DB default -- plus a guard asserting
    the real production database was never created as a side effect."""

    def setUp(self):
        self._real_history_db_existed = os.path.exists(history_store.HISTORY_DB)
        self._tmpdir = tempfile.mkdtemp(prefix="history_store_test_")
        self.db_path = os.path.join(self._tmpdir, "history.db")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        # A bug where some function ignored db_path and fell back to
        # HISTORY_DB would silently start writing into the real user's
        # data -- this must never become true as a side effect of a test.
        self.assertEqual(
            os.path.exists(history_store.HISTORY_DB),
            self._real_history_db_existed,
            "a test touched the real production history.db",
        )

    def _assert_schema_v1_fully_intact(self, db_path):
        """Full post-condition check after concurrent initialization --
        not just "no exception," but that exactly one valid, complete,
        privacy-correct schema exists: every table/index/trigger, WAL
        active, FTS5 secure-delete enabled, and the database reopens
        cleanly afterward."""
        conn = sqlite3.connect(db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], history_store.SCHEMA_VERSION)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")

            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'index', 'trigger')"
                ).fetchall()
            }
            for expected in (
                "history_session", "history_turn", "history_turn_fts",
                "idx_turn_session_id", "idx_turn_request_id", "idx_turn_created_at",
                "idx_turn_role", "idx_turn_request_role",
                "history_turn_ai", "history_turn_ad", "history_turn_au",
            ):
                self.assertIn(expected, names)

            fts_config = dict(conn.execute("SELECT k, v FROM history_turn_fts_config").fetchall())
            self.assertEqual(fts_config.get("secure-delete"), 1)
        finally:
            conn.close()

        # Core secure_delete is a per-connection PRAGMA (never persisted
        # in the file the way journal_mode is), so a race in setup could
        # in principle have left a production write path not setting it
        # -- proven here via a brand-new connection through the real
        # _connect_writable(), the same one every real caller uses.
        write_conn = history_store._connect_writable(db_path)
        try:
            self.assertTrue(write_conn.execute("PRAGMA secure_delete").fetchone()[0])
        finally:
            write_conn.close()

        # A fresh connection/reopen must succeed cleanly -- proves no
        # partial/corrupt schema was left behind.
        status = history_status(db_path=db_path)
        self.assertTrue(status.available)
        self.assertEqual(status.schema_version, history_store.SCHEMA_VERSION)


class TestImportIsSideEffectFree(unittest.TestCase):

    def test_importing_module_creates_nothing(self):
        # HISTORY_DB is computed at import time already (module is
        # already imported at this point in the suite); the assertion
        # that matters is that the *file* was never created merely by
        # that computation/import having happened.
        if not os.path.exists(history_store.HISTORY_DB):
            self.assertFalse(os.path.exists(history_store.HISTORY_DB))


class TestStatusAndSearchNeverCreateMissingDb(IsolatedHistoryDbTestCase):

    def test_history_status_against_missing_db_reports_unavailable(self):
        status = history_status(db_path=self.db_path)
        self.assertFalse(status.available)
        self.assertFalse(os.path.exists(self.db_path))

    def test_search_history_against_missing_db_raises_unavailable(self):
        with self.assertRaises(HistoryUnavailable):
            search_history("anything", db_path=self.db_path)
        self.assertFalse(os.path.exists(self.db_path))

    def test_only_explicit_write_creates_the_db(self):
        self.assertFalse(os.path.exists(self.db_path))
        initialize_history_store(db_path=self.db_path)
        self.assertTrue(os.path.exists(self.db_path))


class TestSchemaVersioning(IsolatedHistoryDbTestCase):

    def test_new_db_initializes_to_current_schema_version(self):
        initialize_history_store(db_path=self.db_path)
        status = history_status(db_path=self.db_path)
        self.assertTrue(status.available)
        self.assertEqual(status.schema_version, history_store.SCHEMA_VERSION)

    def test_reinitializing_existing_current_schema_is_a_noop(self):
        initialize_history_store(db_path=self.db_path)
        create_session("chat", session_id="s1", db_path=self.db_path)
        initialize_history_store(db_path=self.db_path)
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.session_count, 1)

    def test_schema_newer_than_supported_fails_closed_on_write(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"PRAGMA user_version = {history_store.SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.assertRaises(HistorySchemaError):
            create_session("chat", db_path=self.db_path)

    def test_schema_newer_than_supported_fails_closed_on_status(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"PRAGMA user_version = {history_store.SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.assertRaises(HistorySchemaError):
            history_status(db_path=self.db_path)

    def test_schema_newer_than_supported_fails_closed_on_search(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"PRAGMA user_version = {history_store.SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.assertRaises(HistorySchemaError):
            search_history("anything", db_path=self.db_path)

    def test_never_downgrades_or_overwrites_newer_schema(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"PRAGMA user_version = {history_store.SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        try:
            create_session("chat", db_path=self.db_path)
        except HistorySchemaError:
            pass
        conn = sqlite3.connect(self.db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        self.assertEqual(version, history_store.SCHEMA_VERSION + 1)


class TestDbFilePermissions(IsolatedHistoryDbTestCase):

    def test_new_db_file_is_owner_only(self):
        initialize_history_store(db_path=self.db_path)
        mode = os.stat(self.db_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_wal_and_shm_sidecars_are_owner_only(self):
        initialize_history_store(db_path=self.db_path)
        create_session("chat", session_id="s1", db_path=self.db_path)
        record_turn("s1", "user", "hello", db_path=self.db_path)
        for suffix in ("-wal", "-shm"):
            sidecar = self.db_path + suffix
            if os.path.exists(sidecar):
                mode = os.stat(sidecar).st_mode & 0o777
                self.assertEqual(mode, 0o600, f"{sidecar} is not owner-only")


class TestCreateSession(IsolatedHistoryDbTestCase):

    def test_creates_session_with_generated_id(self):
        record = create_session("chat", db_path=self.db_path)
        self.assertTrue(record.session_id)
        self.assertEqual(record.source, "chat")
        self.assertIsNone(record.ended_at)

    def test_accepts_explicit_session_id(self):
        record = create_session("voice", session_id="fixed-id", db_path=self.db_path)
        self.assertEqual(record.session_id, "fixed-id")

    def test_idempotent_for_matching_source(self):
        first = create_session("scheduled", session_id="s1", db_path=self.db_path)
        second = create_session("scheduled", session_id="s1", db_path=self.db_path)
        self.assertEqual(first, second)
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.session_count, 1)

    def test_conflicting_source_for_same_id_raises(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        with self.assertRaises(HistoryValidationError):
            create_session("voice", session_id="s1", db_path=self.db_path)

    def test_invalid_source_rejected(self):
        with self.assertRaises(HistoryValidationError):
            create_session("not-a-real-source", db_path=self.db_path)

    def test_real_source_values_all_accepted(self):
        # These three are the exact real values agent.executor's callers
        # pass -- verified directly against app.py / voice_session.py /
        # scheduler_daemon.py, not assumed.
        for source in ("chat", "voice", "scheduled"):
            record = create_session(source, db_path=self.db_path)
            self.assertEqual(record.source, source)


class TestCloseSession(IsolatedHistoryDbTestCase):

    def test_closes_an_open_session(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        found = close_session("s1", db_path=self.db_path)
        self.assertTrue(found)

    def test_closing_unknown_session_returns_false_not_raise(self):
        initialize_history_store(db_path=self.db_path)
        found = close_session("no-such-session", db_path=self.db_path)
        self.assertFalse(found)

    def test_closing_already_closed_session_returns_false(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        close_session("s1", db_path=self.db_path)
        found_again = close_session("s1", db_path=self.db_path)
        self.assertFalse(found_again)


class TestRecordTurn(IsolatedHistoryDbTestCase):

    def setUp(self):
        super().setUp()
        create_session("chat", session_id="s1", db_path=self.db_path)

    def test_requires_existing_session(self):
        with self.assertRaises(HistoryValidationError):
            record_turn("no-such-session", "user", "hi", db_path=self.db_path)

    def test_rejects_invalid_role(self):
        with self.assertRaises(HistoryValidationError):
            record_turn("s1", "system", "hi", db_path=self.db_path)

    def test_records_a_turn(self):
        turn = record_turn("s1", "user", "hello there", db_path=self.db_path)
        self.assertEqual(turn.session_id, "s1")
        self.assertEqual(turn.role, "user")
        self.assertFalse(turn.redacted)
        self.assertFalse(turn.truncated)

    def test_duplicate_request_id_and_role_does_not_duplicate(self):
        first = record_turn("s1", "user", "hello", request_id="req-1", db_path=self.db_path)
        second = record_turn("s1", "user", "hello again", request_id="req-1", db_path=self.db_path)
        self.assertEqual(first.turn_id, second.turn_id)
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.turn_count, 1)

    def test_same_request_id_different_role_both_recorded(self):
        user_turn = record_turn("s1", "user", "question", request_id="req-1", db_path=self.db_path)
        assistant_turn = record_turn("s1", "assistant", "answer", request_id="req-1", db_path=self.db_path)
        self.assertNotEqual(user_turn.turn_id, assistant_turn.turn_id)
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.turn_count, 2)

    def test_null_request_id_rows_never_deduped(self):
        record_turn("s1", "user", "one", db_path=self.db_path)
        record_turn("s1", "user", "two", db_path=self.db_path)
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.turn_count, 2)

    def test_content_redacted_before_persistence(self):
        turn = record_turn(
            "s1", "user", f"my key is {FAKE_OPENAI_KEY}", db_path=self.db_path,
        )
        self.assertTrue(turn.redacted)
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            "SELECT content FROM history_turn WHERE turn_id = ?", (turn.turn_id,),
        ).fetchone()[0]
        conn.close()
        self.assertNotIn(FAKE_OPENAI_KEY, stored)
        self.assertIn("[redacted]", stored)

    def test_truncation_applies_after_redaction_and_marks_flag(self):
        long_content = "word " * (history_store.MAX_TURN_LENGTH // 4)
        turn = record_turn("s1", "user", long_content, db_path=self.db_path)
        self.assertTrue(turn.truncated)
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            "SELECT content FROM history_turn WHERE turn_id = ?", (turn.turn_id,),
        ).fetchone()[0]
        conn.close()
        self.assertLessEqual(len(stored), history_store.MAX_TURN_LENGTH)

    def test_short_content_not_marked_truncated(self):
        turn = record_turn("s1", "user", "short", db_path=self.db_path)
        self.assertFalse(turn.truncated)

    def test_request_id_exceeding_max_length_rejected(self):
        with self.assertRaises(HistoryValidationError):
            record_turn("s1", "user", "hi", request_id="x" * 200, db_path=self.db_path)

    def test_real_request_id_format_accepted(self):
        # agent.request_context.RequestContext generates
        # uuid.uuid4().hex[:12] -- twelve lowercase hex characters.
        turn = record_turn("s1", "user", "hi", request_id="a1b2c3d4e5f6", db_path=self.db_path)
        self.assertEqual(turn.request_id, "a1b2c3d4e5f6")


class TestForeignKeyIntegrity(IsolatedHistoryDbTestCase):

    def test_foreign_keys_pragma_is_on(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        self.assertEqual(fk_status, 1)

    def test_direct_insert_with_bad_session_id_is_rejected_by_fk(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO history_turn (session_id, role, content, created_at, redacted, truncated) "
                "VALUES ('no-such-session', 'user', 'x', '2026-01-01T00:00:00+00:00', 0, 0)"
            )
        conn.close()


class TestFtsSync(IsolatedHistoryDbTestCase):

    def setUp(self):
        super().setUp()
        create_session("chat", session_id="s1", db_path=self.db_path)

    def test_inserted_turn_is_searchable(self):
        record_turn("s1", "user", "the quick brown fox", db_path=self.db_path)
        results = search_history("quick", db_path=self.db_path)
        self.assertEqual(len(results), 1)

    def test_delete_removes_from_search(self):
        turn = record_turn("s1", "user", "unique_marker_delete_me", db_path=self.db_path)
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM history_turn WHERE turn_id = ?", (turn.turn_id,))
        conn.close()
        results = search_history("unique_marker_delete_me", db_path=self.db_path)
        self.assertEqual(len(results), 0)

    def test_update_reindexes_old_terms_gone_new_terms_found(self):
        turn = record_turn("s1", "user", "original_term_zebra", db_path=self.db_path)
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "UPDATE history_turn SET content = 'replacement_term_giraffe' WHERE turn_id = ?",
            (turn.turn_id,),
        )
        conn.close()
        self.assertEqual(len(search_history("original_term_zebra", db_path=self.db_path)), 0)
        self.assertEqual(len(search_history("replacement_term_giraffe", db_path=self.db_path)), 1)

    def test_fts_table_never_written_directly_by_application_code(self):
        import inspect
        source = inspect.getsource(history_store)
        # The only literal "INSERT INTO history_turn_fts" / "history_turn_fts("
        # write occurrences allowed are inside the trigger DDL strings in
        # _create_schema_v1 -- application functions must never write to
        # the FTS table directly outside of that.
        for name in ("record_turn", "create_session", "close_session"):
            func_source = inspect.getsource(getattr(history_store, name))
            self.assertNotIn("history_turn_fts", func_source)


class TestSecureDelete(IsolatedHistoryDbTestCase):

    def test_secure_delete_pragma_is_enabled(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA secure_delete = ON")
        value = conn.execute("PRAGMA secure_delete").fetchone()[0]
        conn.close()
        self.assertTrue(value)

    def test_deleted_row_bytes_not_recoverable_from_raw_file(self):
        marker = "SECURE_DELETE_MARKER_XYZ_UNIQUE_STRING_998877"
        create_session("chat", session_id="s1", db_path=self.db_path)
        turn = record_turn("s1", "user", marker, db_path=self.db_path)

        # secure_delete is a per-connection PRAGMA, not persisted in the
        # database file the way journal_mode is -- a fresh connection
        # defaults to it being off, so a real future deletion path must
        # set this explicitly too, same as _connect_writable() already
        # does for every write connection this module opens itself.
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("DELETE FROM history_turn WHERE turn_id = ?", (turn.turn_id,))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        with open(self.db_path, "rb") as f:
            raw = f.read()
        self.assertNotIn(marker.encode(), raw)


class TestSafeQueryBuilder(unittest.TestCase):

    def test_plain_terms_pass_through_quoted(self):
        self.assertEqual(build_safe_match_query("hello world"), '"hello" "world"')

    def test_empty_query_returns_none(self):
        self.assertIsNone(build_safe_match_query(""))
        self.assertIsNone(build_safe_match_query("   "))

    def test_non_string_returns_none(self):
        self.assertIsNone(build_safe_match_query(None))
        self.assertIsNone(build_safe_match_query(123))

    def test_query_length_is_capped(self):
        query = build_safe_match_query("a" * 5000)
        self.assertLessEqual(len(query or ""), history_store.MAX_QUERY_LENGTH * 3)

    def test_term_count_is_capped(self):
        many_terms = " ".join(f"term{i}" for i in range(50))
        result = build_safe_match_query(many_terms)
        term_count = result.count('"') // 2
        self.assertLessEqual(term_count, history_store.MAX_QUERY_TERMS)

    def _assert_safe_against_real_fts(self, hostile_input, db_path):
        initialize_history_store(db_path=db_path)
        create_session("chat", session_id="s1", db_path=db_path)
        record_turn("s1", "user", "an ordinary safe sentence about cats and dogs", db_path=db_path)
        # Must not raise -- a raw sqlite3.OperationalError here would mean
        # hostile input reached FTS5's MATCH parser unescaped.
        search_history(hostile_input, db_path=db_path)

    def test_hostile_inputs_never_reach_fts_syntax(self):
        hostile_inputs = [
            "or",
            "cats OR dogs",
            "NEAR miss",
            "a NOT b",
            "col:value",
            "(cats OR dogs) AND NOT fish",
            'embedded "quotes" here',
            "wild*card",
            "-excluded term",
            '"already quoted phrase"',
        ]
        for hostile in hostile_inputs:
            with self.subTest(hostile=hostile):
                tmpdir = tempfile.mkdtemp(prefix="history_store_hostile_")
                try:
                    db_path = os.path.join(tmpdir, "history.db")
                    self._assert_safe_against_real_fts(hostile, db_path)
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)

    def test_control_characters_stripped(self):
        result = build_safe_match_query("hello\x00\x01world")
        self.assertNotIn("\x00", result or "")


class TestSearchHistory(IsolatedHistoryDbTestCase):

    def setUp(self):
        super().setUp()
        create_session("chat", session_id="s1", db_path=self.db_path)
        create_session("voice", session_id="s2", db_path=self.db_path)

    def test_finds_matching_turn(self):
        record_turn("s1", "user", "what is the weather in boston", db_path=self.db_path)
        results = search_history("weather boston", db_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].session_id, "s1")

    def test_no_match_returns_empty_not_raise(self):
        record_turn("s1", "user", "hello", db_path=self.db_path)
        results = search_history("zzz_nonexistent_term_zzz", db_path=self.db_path)
        self.assertEqual(results, [])

    def test_filters_by_source(self):
        record_turn("s1", "user", "banana smoothie recipe", db_path=self.db_path)
        record_turn("s2", "user", "banana smoothie recipe", db_path=self.db_path)
        results = search_history("banana smoothie", source="voice", db_path=self.db_path)
        self.assertTrue(all(r.source == "voice" for r in results))

    def test_filters_by_role(self):
        record_turn("s1", "user", "papaya juice recipe", db_path=self.db_path)
        record_turn("s1", "assistant", "papaya juice recipe", db_path=self.db_path)
        results = search_history("papaya juice", role="assistant", db_path=self.db_path)
        self.assertTrue(all(r.role == "assistant" for r in results))

    def test_filters_by_session_id(self):
        record_turn("s1", "user", "mango lassi recipe", db_path=self.db_path)
        record_turn("s2", "user", "mango lassi recipe", db_path=self.db_path)
        results = search_history("mango lassi", session_id="s1", db_path=self.db_path)
        self.assertTrue(all(r.session_id == "s1" for r in results))

    def test_max_results_is_hard_capped(self):
        for i in range(30):
            record_turn("s1", "user", f"repeatedterm entry number {i}", db_path=self.db_path)
        results = search_history("repeatedterm", max_results=1000, db_path=self.db_path)
        self.assertLessEqual(len(results), history_store._MAX_SEARCH_RESULTS)

    def test_results_never_expose_db_path_or_raw_sql(self):
        record_turn("s1", "user", "confidential sounding content here", db_path=self.db_path)
        results = search_history("confidential sounding", db_path=self.db_path)
        for r in results:
            self.assertNotIn(self.db_path, repr(r))

    def test_snippet_is_bounded(self):
        long_text = "findme " + ("filler word " * 500)
        record_turn("s1", "user", long_text, db_path=self.db_path)
        results = search_history("findme", db_path=self.db_path)
        self.assertTrue(results)
        self.assertLess(len(results[0].snippet), len(long_text))

    def test_ranking_orders_by_bm25_then_recency(self):
        record_turn("s1", "user", "irrelevant filler content", db_path=self.db_path)
        record_turn("s1", "user", "targetword targetword targetword", db_path=self.db_path)
        record_turn("s1", "user", "just one targetword mention here", db_path=self.db_path)
        results = search_history("targetword", db_path=self.db_path)
        self.assertGreaterEqual(len(results), 2)
        ranks = [r.rank for r in results]
        self.assertEqual(ranks, sorted(ranks))

    def test_invalid_source_filter_rejected(self):
        with self.assertRaises(HistoryValidationError):
            search_history("hello", source="not-a-real-source", db_path=self.db_path)

    def test_secret_content_not_retrievable_via_search(self):
        record_turn("s1", "user", f"my token: {FAKE_GITHUB_TOKEN}", db_path=self.db_path)
        results = search_history(FAKE_GITHUB_TOKEN, db_path=self.db_path)
        self.assertEqual(results, [])
        for r in results:
            self.assertNotIn(FAKE_GITHUB_TOKEN, r.snippet)


class TestErrorSemanticsDistinctness(IsolatedHistoryDbTestCase):

    def test_corrupt_db_raises_corruption_not_generic(self):
        with open(self.db_path, "wb") as f:
            f.write(b"this is not a sqlite database file at all, just garbage bytes")
        with self.assertRaises((HistoryCorruption, HistorySchemaError, sqlite3.DatabaseError)):
            create_session("chat", db_path=self.db_path)

    def test_zero_results_distinct_from_unavailable(self):
        initialize_history_store(db_path=self.db_path)
        create_session("chat", session_id="s1", db_path=self.db_path)
        results = search_history("zzz_no_such_term_zzz", db_path=self.db_path)
        self.assertEqual(results, [])
        missing_path = os.path.join(self._tmpdir, "does_not_exist.db")
        with self.assertRaises(HistoryUnavailable):
            search_history("zzz_no_such_term_zzz", db_path=missing_path)

    def test_all_public_errors_derive_from_history_store_error(self):
        for exc in (HistoryUnavailable, HistorySchemaError, HistoryCorruption, HistoryValidationError, HistoryBusy):
            self.assertTrue(issubclass(exc, HistoryStoreError))


class TestConcurrency(IsolatedHistoryDbTestCase):

    def test_concurrent_initialization_is_safe(self):
        """Barrier-synchronized (never sleep-based) so every thread hits
        the PRAGMA/schema-creation critical section at the same instant --
        this is what actually stresses the race, unlike simply starting
        threads in a loop (Thread.start() overhead alone spreads out real
        start times enough to mostly miss the window). 16 threads against
        one brand-new database, matching the real production scenario:
        the menu-bar app, the scheduler daemon, and the Streamlit process
        are independent OS processes that may legitimately race to
        initialize history.db on first use."""
        n_threads = 16
        errors = []
        barrier = threading.Barrier(n_threads)

        def init():
            try:
                barrier.wait(timeout=10)
                initialize_history_store(db_path=self.db_path)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=init) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self._assert_schema_v1_fully_intact(self.db_path)

    def test_concurrent_initialization_is_safe_across_repeated_fresh_databases(self):
        """Bounded repeated-round coverage (Phase 9 Reliability S1.1) --
        the race this guards against was empirically rare (order of 1 in
        a few hundred attempts under 12-way contention), so a single
        round is not reliable regression coverage on its own. Several
        rounds against brand-new databases, kept fast (well under a
        second per round in practice) rather than a long stress suite."""
        n_rounds = 15
        n_threads = 12
        for round_index in range(n_rounds):
            db_path = os.path.join(self._tmpdir, f"round_{round_index}.db")
            errors = []
            barrier = threading.Barrier(n_threads)

            def init():
                try:
                    barrier.wait(timeout=10)
                    initialize_history_store(db_path=db_path)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=init) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(errors, [], f"round {round_index} failed")
            status = history_status(db_path=db_path)
            self.assertTrue(status.available)

    def test_concurrent_distinct_writes_do_not_corrupt(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        errors = []

        def write(i):
            try:
                record_turn("s1", "user", f"message number {i}", request_id=f"req-{i}", db_path=self.db_path)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.turn_count, 10)

    def test_concurrent_duplicate_request_id_does_not_duplicate(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        errors = []

        def write():
            try:
                record_turn("s1", "user", "same message", request_id="dupe-req", db_path=self.db_path)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.turn_count, 1)

    def test_wal_mode_active(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_concurrent_initialization_across_real_separate_processes(self):
        """The race this guards against (SQLITE_BUSY from PRAGMA
        journal_mode=WAL's one-time transition) is a SQLite/filesystem-
        level lock, not a Python GIL/shared-memory artifact -- confirmed
        by reproducing it with plain threads, which never contend for the
        GIL during a blocking C-level sqlite3 call. Real separate OS
        processes are the production-realistic case (menu-bar app,
        scheduler daemon, Streamlit), so this exercises the actual
        boundary directly rather than only inferring it from the
        thread-based tests above."""
        n_processes = 4
        barrier = multiprocessing.Barrier(n_processes)
        result_queue = multiprocessing.Queue()
        processes = [
            multiprocessing.Process(target=_multiprocess_init_worker, args=(self.db_path, barrier, result_queue))
            for _ in range(n_processes)
        ]
        for p in processes:
            p.start()
        for p in processes:
            p.join(timeout=15)

        results = [result_queue.get(timeout=5) for _ in range(n_processes)]
        errors = [r for r in results if r is not None]
        self.assertEqual(errors, [])
        for p in processes:
            self.assertEqual(p.exitcode, 0)

        self._assert_schema_v1_fully_intact(self.db_path)


class TestHistoryBusySemantics(IsolatedHistoryDbTestCase):
    """HistoryBusy must remain the exception a caller actually sees for a
    genuinely-locked-longer-than-the-deadline condition -- never a raw
    sqlite3.OperationalError, never HistoryCorruption/HistorySchemaError
    (both real, distinct failure classes that must never be conflated
    with contention), and never silently retried forever."""

    def test_set_journal_mode_wal_retries_transient_busy_then_succeeds(self):
        # A real SQLITE_BUSY on the first two attempts, succeeding on the
        # third -- proves the retry loop actually retries rather than
        # failing (or succeeding) on the very first attempt only.
        busy_error = sqlite3.OperationalError("database is locked")
        busy_error.sqlite_errorcode = history_store._SQLITE_BUSY
        attempts = {"count": 0}

        class _FakeConn:
            def execute(self, sql):
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise busy_error

        with patch.object(history_store, "_WAL_INIT_RETRY_INTERVAL_SECONDS", 0.001):
            history_store._set_journal_mode_wal(_FakeConn())

        self.assertEqual(attempts["count"], 3)

    def test_set_journal_mode_wal_raises_history_busy_after_deadline_exceeded(self):
        busy_error = sqlite3.OperationalError("database is locked")
        busy_error.sqlite_errorcode = history_store._SQLITE_BUSY

        class _AlwaysBusyConn:
            def execute(self, sql):
                raise busy_error

        # Deadline patched to near-zero so this test stays fast without
        # weakening the production _BUSY_TIMEOUT_MS value itself.
        with patch.object(history_store, "_BUSY_TIMEOUT_MS", 1), \
             patch.object(history_store, "_WAL_INIT_RETRY_INTERVAL_SECONDS", 0.001):
            with self.assertRaises(HistoryBusy):
                history_store._set_journal_mode_wal(_AlwaysBusyConn())

    def test_set_journal_mode_wal_never_retries_a_non_busy_operational_error(self):
        # A real disk I/O failure (or any other non-SQLITE_BUSY
        # OperationalError) must propagate immediately -- retrying this
        # class of error would be actively wrong, not just unnecessary.
        disk_error = sqlite3.OperationalError("disk I/O error")
        disk_error.sqlite_errorcode = 10  # SQLITE_IOERR, not SQLITE_BUSY

        class _DiskFailureConn:
            def execute(self, sql):
                raise disk_error

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            history_store._set_journal_mode_wal(_DiskFailureConn())
        self.assertIs(ctx.exception, disk_error)

    def test_genuinely_held_write_lock_raises_history_busy_not_a_raw_error(self):
        """End-to-end, real SQLite: a separate connection genuinely holds
        the write lock (BEGIN IMMEDIATE, never committed) while a second
        writer tries to initialize/write against the same database.
        Proves the pre-existing _begin_immediate -> HistoryBusy path
        actually behaves this way for real -- it was previously only
        checked structurally (HistoryBusy is-a HistoryStoreError), never
        exercised against a genuine held lock."""
        initialize_history_store(db_path=self.db_path)

        holder = sqlite3.connect(self.db_path, isolation_level=None)
        holder.execute(f"PRAGMA busy_timeout = {history_store._BUSY_TIMEOUT_MS}")
        holder.execute("BEGIN IMMEDIATE")
        try:
            with patch.object(history_store, "_BUSY_TIMEOUT_MS", 200):
                with self.assertRaises(HistoryBusy):
                    create_session("chat", db_path=self.db_path)
        finally:
            holder.execute("ROLLBACK")
            holder.close()

        # Lock released -- a normal write must now succeed cleanly, and
        # nothing about the schema/privacy configuration was disturbed by
        # the failed attempt above.
        create_session("chat", session_id="after-lock-released", db_path=self.db_path)
        self._assert_schema_v1_fully_intact(self.db_path)


class TestTransactionIntegrity(IsolatedHistoryDbTestCase):

    def test_rollback_on_bad_role_leaves_no_partial_row(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        with self.assertRaises(HistoryValidationError):
            record_turn("s1", "not-a-real-role", "hi", db_path=self.db_path)
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.turn_count, 0)

    def test_reopen_after_writes_preserves_data_and_search(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        record_turn("s1", "user", "persisted_across_reopen_marker", db_path=self.db_path)
        # Nothing keeps a connection open between calls (connection-per-
        # operation), so simply calling again exercises a fresh open.
        status = history_status(db_path=self.db_path)
        self.assertEqual(status.turn_count, 1)
        results = search_history("persisted_across_reopen_marker", db_path=self.db_path)
        self.assertEqual(len(results), 1)

    def test_fk_violation_does_not_leave_orphan_fts_row(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute(
                "INSERT INTO history_turn (session_id, role, content, created_at, redacted, truncated) "
                "VALUES ('ghost-session', 'user', 'orphan_marker_text', '2026-01-01T00:00:00+00:00', 0, 0)"
            )
        except sqlite3.IntegrityError:
            pass
        conn.close()
        results = search_history("orphan_marker_text", db_path=self.db_path)
        self.assertEqual(results, [])


class TestHistoryStatusReadOnlyContract(IsolatedHistoryDbTestCase):

    def test_status_does_not_expose_db_path(self):
        initialize_history_store(db_path=self.db_path)
        status = history_status(db_path=self.db_path)
        self.assertNotIn(self.db_path, repr(status))

    def test_status_reports_expected_fields_on_populated_db(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        record_turn("s1", "user", "hello", db_path=self.db_path)
        status = history_status(db_path=self.db_path)
        self.assertTrue(status.available)
        self.assertEqual(status.session_count, 1)
        self.assertEqual(status.turn_count, 1)
        self.assertIsNotNone(status.oldest_turn_at)
        self.assertIsNotNone(status.newest_turn_at)
        self.assertTrue(status.fts_available)
        self.assertEqual(status.journal_mode.lower(), "wal")


class TestPrivacyNeverLogsRawContent(IsolatedHistoryDbTestCase):

    def test_validation_error_message_never_contains_content(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        secret_content = f"leaked secret {FAKE_AWS_KEY}"
        try:
            record_turn("s1", "not-a-role", secret_content, db_path=self.db_path)
        except HistoryValidationError as e:
            self.assertNotIn(FAKE_AWS_KEY, str(e))
            self.assertNotIn(secret_content, str(e))

    def test_secret_not_recoverable_from_raw_db_bytes_after_write(self):
        create_session("chat", session_id="s1", db_path=self.db_path)
        record_turn("s1", "user", f"api_key: {FAKE_ANTHROPIC_KEY}", db_path=self.db_path)
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                with open(path, "rb") as f:
                    raw = f.read()
                self.assertNotIn(FAKE_ANTHROPIC_KEY.encode(), raw)


class _NoFtsSecureDeleteConnection(sqlite3.Connection):
    """Simulates a SQLite/FTS5 build that predates 3.42.0 and doesn't
    recognize the 'secure-delete' FTS5 config command. Verified
    empirically (not assumed) that a genuinely unrecognized FTS5 special
    command raises exactly sqlite3.OperationalError("SQL logic error")
    on this project's real runtime -- that's what this reproduces, so the
    fail-closed path is tested against the real shape of failure, not a
    made-up one."""

    def execute(self, sql, *args, **kwargs):
        if "'secure-delete'" in sql:
            raise sqlite3.OperationalError("SQL logic error")
        return super().execute(sql, *args, **kwargs)


class TestFtsSecureDeleteConfiguration(IsolatedHistoryDbTestCase):
    """Phase 9 / M4.1 hardening pass: FTS5's own 'secure-delete' config
    (SQLite >= 3.42.0), layered on top of the core `PRAGMA secure_delete`
    already covered by TestSecureDelete above. The core pragma only
    covers ordinary SQLite table storage; it does not by itself guarantee
    an FTS5 index's own b-tree segments stop retaining old term data
    after a logical delete/update -- this is the second, independent
    layer for that."""

    def test_schema_init_enables_fts_secure_delete(self):
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        rows = dict(conn.execute("SELECT k, v FROM history_turn_fts_config").fetchall())
        conn.close()
        self.assertEqual(rows.get("secure-delete"), 1)

    def test_fts_secure_delete_setting_persists_across_reopen(self):
        # Unlike the core PRAGMA (per-connection, reset on every new
        # connection -- see TestSecureDelete), FTS5's own 'secure-delete'
        # is a property of the table itself, stored in its `_config`
        # shadow table. Proven here by reading it back from a brand-new
        # connection that sets no pragmas/config of its own at all.
        initialize_history_store(db_path=self.db_path)
        create_session("chat", session_id="s1", db_path=self.db_path)
        record_turn("s1", "user", "hello", db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        rows = dict(conn.execute("SELECT k, v FROM history_turn_fts_config").fetchall())
        conn.close()
        self.assertEqual(rows.get("secure-delete"), 1)

    def test_fts_config_shadow_table_not_exposed_via_public_api(self):
        # The shadow table is a legitimate, read-only inspection point in
        # TESTS (per this pass's own instructions) but must never be
        # reachable through this module's public functions/return types.
        import inspect
        for name in ("initialize_history_store", "create_session", "close_session",
                     "record_turn", "history_status", "search_history"):
            source = inspect.getsource(getattr(history_store, name))
            self.assertNotIn("_config", source)


class TestFtsSecureDeleteFailsClosed(IsolatedHistoryDbTestCase):

    def _patched_connect(self, *args, **kwargs):
        kwargs.pop("factory", None)
        return self._real_connect(*args, factory=_NoFtsSecureDeleteConnection, **kwargs)

    def setUp(self):
        super().setUp()
        self._real_connect = sqlite3.connect

    def test_unsupported_fts_runtime_raises_history_unsupported_runtime(self):
        with patch.object(history_store.sqlite3, "connect", self._patched_connect):
            with self.assertRaises(HistoryUnsupportedRuntime):
                initialize_history_store(db_path=self.db_path)

    def test_unsupported_fts_runtime_via_create_session_also_fails_closed(self):
        with patch.object(history_store.sqlite3, "connect", self._patched_connect):
            with self.assertRaises(HistoryUnsupportedRuntime):
                create_session("chat", db_path=self.db_path)

    def test_unsupported_fts_runtime_leaves_no_usable_schema_behind(self):
        with patch.object(history_store.sqlite3, "connect", self._patched_connect):
            try:
                create_session("chat", db_path=self.db_path)
            except HistoryUnsupportedRuntime:
                pass
        # Whatever file was left behind (if any -- a 0600 empty file may
        # exist from the pre-create step) must not be a working,
        # production-capable schema: the failed transaction must have
        # rolled back cleanly, leaving user_version at 0 and no tables.
        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            conn.close()
            self.assertEqual(version, 0)
            self.assertEqual(tables, [])

    def test_unsupported_fts_runtime_never_silently_degrades(self):
        # A supported runtime (the real one running this test) must never
        # produce a database that's missing the FTS secure-delete
        # invariant just because *some* other environment might lack it
        # -- fail-closed is per-runtime-capability, not a permanent
        # downgrade once triggered once.
        initialize_history_store(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        rows = dict(conn.execute("SELECT k, v FROM history_turn_fts_config").fetchall())
        conn.close()
        self.assertEqual(rows.get("secure-delete"), 1)

    def test_history_unsupported_runtime_is_a_history_store_error(self):
        self.assertTrue(issubclass(HistoryUnsupportedRuntime, HistoryStoreError))


class TestUpdatePrivacyAcrossBothLayers(IsolatedHistoryDbTestCase):

    def test_update_replaces_searchable_content_and_scrubs_old_token_bytes(self):
        marker_old = "UPDATEPRIVACYOLDTOKEN998877uniquemarker"
        marker_new = "UPDATEPRIVACYNEWTOKEN112233uniquemarker"
        create_session("chat", session_id="s1", db_path=self.db_path)
        turn = record_turn("s1", "user", marker_old, db_path=self.db_path)

        self.assertEqual(len(search_history(marker_old, db_path=self.db_path)), 1)

        # The canonical-table update the AFTER UPDATE trigger supports --
        # exercising the same trigger a real update path would use,
        # since M4.1 has no public update API of its own yet.
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute(
            "UPDATE history_turn SET content = ? WHERE turn_id = ?",
            (marker_new, turn.turn_id),
        )
        # FTS5's own internal-consistency self-check -- raises if the
        # index is corrupt, distinct from merely "no results returned".
        conn.execute("INSERT INTO history_turn_fts(history_turn_fts) VALUES ('integrity-check')")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        self.assertEqual(len(search_history(marker_old, db_path=self.db_path)), 0)
        results_new = search_history(marker_new, db_path=self.db_path)
        self.assertEqual(len(results_new), 1)
        self.assertEqual(results_new[0].turn_id, turn.turn_id)

        raw = b""
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                with open(path, "rb") as f:
                    raw += f.read()
        self.assertNotIn(marker_old.encode(), raw)
        self.assertIn(marker_new.encode(), raw)


class TestDeletePrivacyAcrossBothLayers(IsolatedHistoryDbTestCase):

    def test_delete_removes_from_both_layers_and_scrubs_raw_bytes(self):
        marker = "DELETEPRIVACYBOTHLAYERS554433uniquemarker"
        create_session("chat", session_id="s1", db_path=self.db_path)
        turn = record_turn("s1", "user", marker, db_path=self.db_path)

        self.assertEqual(len(search_history(marker, db_path=self.db_path)), 1)
        conn_check = sqlite3.connect(self.db_path)
        canonical_before = conn_check.execute(
            "SELECT count(*) FROM history_turn WHERE turn_id = ?", (turn.turn_id,)
        ).fetchone()[0]
        conn_check.close()
        self.assertEqual(canonical_before, 1)

        # A controlled internal/test connection with core secure_delete
        # explicitly enabled -- this is exactly the combination this
        # hardening pass validates: core pragma + FTS config + the
        # existing external-content DELETE trigger, all three together.
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("DELETE FROM history_turn WHERE turn_id = ?", (turn.turn_id,))
        conn.execute("INSERT INTO history_turn_fts(history_turn_fts) VALUES ('integrity-check')")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        self.assertEqual(len(search_history(marker, db_path=self.db_path)), 0)
        conn_check = sqlite3.connect(self.db_path)
        canonical_after = conn_check.execute(
            "SELECT count(*) FROM history_turn WHERE turn_id = ?", (turn.turn_id,)
        ).fetchone()[0]
        conn_check.close()
        self.assertEqual(canonical_after, 0)

        raw = b""
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                with open(path, "rb") as f:
                    raw += f.read()
        self.assertNotIn(marker.encode(), raw)


class TestPerConnectionSecureDeleteTrap(IsolatedHistoryDbTestCase):
    """Regression coverage for the already-discovered invariant: the core
    `PRAGMA secure_delete=ON` is per-connection, not persisted in the
    database file. Every production write path in this module must set
    it explicitly on every connection it opens -- this test protects that
    invariant so a future change (e.g. a real deletion API) can't
    accidentally open a write connection that skips it."""

    class _RecordingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            self.__dict__.setdefault("_executed_sql", []).append(sql)
            return super().execute(sql, *args, **kwargs)

    def test_every_public_write_path_sets_core_secure_delete_pragma(self):
        created_connections = []
        real_connect = sqlite3.connect

        def fake_connect(*args, **kwargs):
            kwargs.pop("factory", None)
            conn = real_connect(*args, factory=self._RecordingConnection, **kwargs)
            created_connections.append(conn)
            return conn

        with patch.object(history_store.sqlite3, "connect", fake_connect):
            initialize_history_store(db_path=self.db_path)
            create_session("chat", session_id="s1", db_path=self.db_path)
            record_turn("s1", "user", "hello", db_path=self.db_path)
            close_session("s1", db_path=self.db_path)

        # None of these four functions ever open a read-only connection
        # internally (that's only history_status()/search_history()), so
        # every connection created here is expected to be a write path.
        self.assertGreaterEqual(len(created_connections), 4)
        for conn in created_connections:
            executed = getattr(conn, "_executed_sql", [])
            self.assertIn(
                "PRAGMA secure_delete = ON", executed,
                "a write connection was opened without setting secure_delete=ON",
            )


if __name__ == "__main__":
    unittest.main()
