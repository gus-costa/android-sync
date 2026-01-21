# Implementation Plan: Schedule Log Retention

## Overview

This plan implements automatic cleanup for schedule logs (`schedule-*.log`) to match the retention policy applied to main logs. This closes the gap between specification and implementation as identified in the gap analysis.

**Status:** IMPLEMENTED
**Priority:** MEDIUM-HIGH (required for long-running unattended operation)

---

## 1. Update Log Cleanup Function

**Specification Reference:** `specs/logging-system.md` §6.2 Cleanup Algorithm

**Current Code Location:** `src/android_sync/logging.py:59-80` (function `cleanup_old_logs`)

**Required Changes:**

- [x] Extend `cleanup_old_logs()` to glob both `android-sync-*.log` AND `schedule-*.log` files
- [x] Apply same cutoff logic (mtime-based) to both file types
- [x] Track removed count for both types in return value or logging

**Specification Quote:**
> "Process: 1. Calculate cutoff time: now - retention_days; 2. Glob for log files: android-sync-*.log (main invocation logs), schedule-*.log (background job logs); 3. For each file: Check file mtime, If mtime < cutoff: Delete file"

**Testing:**

- [x] Unit test: Create old `schedule-*.log` files, verify cleanup
- [x] Unit test: Create recent `schedule-*.log` files, verify retention
- [ ] Integration test: Run scheduled jobs, verify mtime updates prevent cleanup
- [ ] Integration test: Verify abandoned schedule logs (old mtime) get cleaned up

---

## 2. Update Documentation Comments

**Specification Reference:** `specs/logging-system.md` §6.2, §7.4

**Files to Update:**

### 2.1 `src/android_sync/logging.py`

- [x] Update `cleanup_old_logs()` docstring to mention both file types
- [x] Add reference to specification section: `specs/logging-system.md §6.2`
- [x] Update module-level docstring if it mentions log retention

### 2.2 `src/android_sync/scheduler.py`

**Current Code Location:** `src/android_sync/scheduler.py:320-343` (function `spawn_background_job`)

- [x] Update comment at line 332 where schedule log file is created
- [x] Add comment explaining retention behavior (mtime-based cleanup)
- [x] Reference specification: `specs/logging-system.md §7.4`

**Specification Quote:**
> "Schedule logs follow same retention policy as main logs (§6.1). Files older than log_retention_days are deleted automatically. Cleanup runs when any logging is initialized (including background jobs). mtime updated on each append, ensuring active logs aren't deleted."

---

## 3. Verify Retention Behavior

**Specification Reference:** `specs/logging-system.md` §7.4 Retention and Cleanup

**Verification Points:**

- [ ] Confirm that background jobs call `setup_logging()` which triggers cleanup
  - Check: `src/android_sync/cli.py` in `cmd_run()` function
  - Spec reference: `specs/logging-system.md` §6.2 "When Cleanup Runs"

- [ ] Verify file mtime updates on append operations
  - OS-level behavior: opening file in append mode updates mtime
  - No code changes needed, just verify behavior in tests

- [ ] Ensure cleanup doesn't delete active schedule logs
  - Active schedules append regularly → mtime stays fresh
  - Spec reference: `specs/logging-system.md` §7.4 "Why Append Mode Works with Retention"

---

## 4. Update Configuration Documentation

**Specification Reference:** `specs/configuration-schema.md` §4.2

**Already Complete:** Spec already updated to clarify that `log_retention_days` applies to all logs.

**Verification:**

- [ ] Confirm `specs/configuration-schema.md` §4.2 `log_retention_days` field definition mentions both log types ✓
- [ ] No code changes needed - config field already exists and is used by cleanup function

---

## 5. Update Tests

**Specification Reference:** `specs/logging-system.md` §12 Testing

### 5.1 Unit Tests: `tests/test_logging.py`

**New Test Cases Needed:**

- [x] `test_cleanup_old_logs_includes_schedule_logs`
  - Create mix of old and new `android-sync-*.log` files
  - Create mix of old and new `schedule-*.log` files
  - Run cleanup with retention_days=7
  - Assert only old files (both types) are deleted

- [x] `test_cleanup_schedule_logs_respects_retention`
  - Create `schedule-daily.log` with old mtime
  - Create `schedule-frequent.log` with recent mtime
  - Run cleanup with retention_days=7
  - Assert old file deleted, recent file retained

- [x] `test_cleanup_disabled_keeps_schedule_logs`
  - Create old `schedule-*.log` files
  - Run cleanup with retention_days=0
  - Assert no files deleted

### 5.2 Integration Tests: `tests/test_scheduler.py`

**New Test Cases Needed:**

- [ ] `test_schedule_log_mtime_updates_on_run`
  - Create schedule log file with old mtime
  - Run scheduled job (which appends to log)
  - Assert mtime is now recent
  - Run cleanup with short retention
  - Assert file NOT deleted (mtime was updated)

- [ ] `test_abandoned_schedule_logs_cleaned_up`
  - Create `schedule-abandoned.log` with old mtime
  - Run cleanup with retention_days=7
  - Assert file is deleted
  - Verifies inactive schedules are cleaned up

---

## 6. Manual Testing Scenarios

**Specification Reference:** `specs/logging-system.md` §11 Operational Procedures

### 6.1 Active Schedule Test

1. Configure a schedule with short interval (e.g., every 6 hours)
2. Set `log_retention_days = 2` in config
3. Let schedule run multiple times over 3 days
4. Verify schedule log file exists and contains recent entries
5. Verify old main logs are cleaned up but schedule log remains

### 6.2 Inactive Schedule Test

1. Create a `schedule-test.log` file manually
2. Set mtime to 10 days ago: `touch -d "10 days ago" ~/logs/schedule-test.log`
3. Set `log_retention_days = 7` in config
4. Run any command that initializes logging (e.g., `android-sync status`)
5. Verify `schedule-test.log` is deleted

### 6.3 Mixed Logs Test

1. Create multiple old and new log files (both types)
2. Set `log_retention_days = 7`
3. Run cleanup
4. Verify only old files (both types) are deleted

---

## 7. Deployment Considerations

**Specification Reference:** `specs/logging-system.md` §11 Operational Procedures

### 7.1 Backward Compatibility

- [ ] **No breaking changes:** Existing configs continue to work unchanged
- [ ] **No user action required:** Cleanup applies automatically on next logging init
- [ ] **Existing schedule logs:** Will be cleaned up if mtime is old enough
  - If users have large old schedule logs, these will be deleted on next run
  - Consider: Add release note warning about this behavior

### 7.2 Migration Notes

**For existing installations:**

- Schedule logs created before this change may be large
- On first run after update, old schedule logs will be deleted if mtime exceeds retention
- No data loss concern - logs are historical only
- Users who want to preserve old logs should:
  - Manually archive before upgrading: `cp ~/logs/schedule-*.log ~/backup/`
  - Or temporarily set `log_retention_days = 0` during upgrade

### 7.3 Release Notes Entry

```markdown
## Changed

- Schedule logs (`schedule-*.log`) now follow the same retention policy as main logs
  - Controlled by `log_retention_days` config field (default: 7 days)
  - Active schedules continuously update file mtime and avoid deletion
  - Inactive/abandoned schedule logs are cleaned up automatically
  - **Note:** Existing large schedule logs may be deleted on first run if older than retention period
  - To preserve: archive old logs before upgrading or temporarily set `log_retention_days = 0`
```

---

## 8. Code Review Checklist

Before merging:

- [x] Implementation matches specification exactly
  - [x] Both `android-sync-*.log` and `schedule-*.log` are globbed
  - [x] Same cutoff logic applied to both
  - [x] Spec references added to code comments

- [x] Tests pass
  - [x] All existing tests still pass
  - [x] New unit tests for schedule log cleanup pass
  - [ ] Integration tests verify mtime behavior

- [x] Documentation updated
  - [x] Docstrings reference spec sections
  - [x] Code comments explain retention behavior
  - [x] No contradictory comments remain

- [ ] Manual testing completed
  - [ ] Active schedule logs NOT deleted
  - [ ] Inactive schedule logs ARE deleted
  - [ ] Mixed log types cleaned up correctly

---

## 9. Specification Cross-References

This implementation satisfies the following specification sections:

- **Primary:**
  - `specs/logging-system.md` §6.2 Cleanup Algorithm - Defines the glob patterns and implementation
  - `specs/logging-system.md` §7.4 Retention and Cleanup - Explains how schedule logs are retained

- **Supporting:**
  - `specs/logging-system.md` §7.2 Append Mode - Describes schedule log file creation
  - `specs/configuration-schema.md` §4.2 log_retention_days - Config field applies to all logs
  - `specs/scheduling.md` §5.3.2 Background Job Spawning - Where schedule logs are created

- **Testing:**
  - `specs/logging-system.md` §12 Testing - Test strategy and coverage

---

## 10. Success Criteria

Implementation is complete when:

1. ✅ `cleanup_old_logs()` globs both `android-sync-*.log` AND `schedule-*.log` - DONE
2. ✅ All tests pass (existing + new) - DONE (104 tests passing)
3. ⚠️  Manual testing confirms:
   - Active schedule logs are NOT deleted (mtime stays fresh) - PENDING
   - Inactive schedule logs ARE deleted (old mtime triggers cleanup) - PENDING
   - Both log types follow same retention policy - VERIFIED IN UNIT TESTS
4. ✅ Code comments reference specification sections - DONE
5. ✅ Specs and implementation are aligned (no gaps) - DONE

---

## 11. Files to Modify

Summary of all files requiring changes:

| File | Change Type | Lines | Description |
|------|-------------|-------|-------------|
| `src/android_sync/logging.py` | Code | ~75-80 | Add second glob for `schedule-*.log` |
| `src/android_sync/logging.py` | Docs | ~59-68 | Update docstring |
| `src/android_sync/scheduler.py` | Docs | ~320-330 | Update docstring and comments |
| `tests/test_logging.py` | Tests | New | Add 3-4 test cases |
| `tests/test_scheduler.py` | Tests | New | Add 2 integration tests |

**No changes needed:**
- Configuration schema (already correct)
- CLI commands (cleanup already called)
- Specifications (already updated)
