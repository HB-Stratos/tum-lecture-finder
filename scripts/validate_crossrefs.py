"""Validate that compute_other_semesters produced correct cross-references."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tum_lecture_finder.storage import CourseStore

store = CourseStore()
conn = store._conn

total = store.course_count()
print(f"Total courses: {total}")

# Courses with non-empty other_semesters
row = conn.execute("SELECT COUNT(*) FROM courses WHERE other_semesters != ''").fetchone()
with_xref = row[0]
print(f"Courses with cross-references: {with_xref} ({100*with_xref/total:.1f}%)")

# Average number of cross-ref semesters
row2 = conn.execute(
    "SELECT AVG(LENGTH(other_semesters) - LENGTH(REPLACE(other_semesters, ',', '')) + 1) "
    "FROM courses WHERE other_semesters != ''"
).fetchone()
print(f"Avg other-semesters per cross-referenced course: {row2[0]:.2f}")

# Spot-check: pick 5 courses with cross-refs and verify them manually
print("\nSpot-check (5 courses):")
samples = conn.execute(
    "SELECT course_id, semester_key, identity_code_id, other_semesters, title_en "
    "FROM courses WHERE other_semesters != '' AND identity_code_id != 0 LIMIT 5"
).fetchall()

errors = 0
for s in samples:
    cid, sem, iid, other_csv, title = s
    stored_others = set(other_csv.split(","))
    # Recompute manually
    actual_rows = conn.execute(
        "SELECT DISTINCT semester_key FROM courses "
        "WHERE identity_code_id = ? AND course_id != ?",
        (iid, cid),
    ).fetchall()
    actual_others = {r[0] for r in actual_rows}
    match = stored_others == actual_others
    status = "OK" if match else "MISMATCH"
    if not match:
        errors += 1
    print(f"  [{status}] id={cid} sem={sem} iid={iid} "
          f"stored={sorted(stored_others)} actual={sorted(actual_others)} "
          f"title={title[:50]}")

# Broader validation: count mismatches across all courses
print("\nFull validation (all courses with identity_code_id != 0)...")
all_courses = conn.execute(
    "SELECT course_id, identity_code_id, other_semesters "
    "FROM courses WHERE identity_code_id != 0"
).fetchall()

mismatch_count = 0
for row in all_courses:
    cid, iid, other_csv = row
    stored = set(other_csv.split(",")) if other_csv else set()
    actual_rows = conn.execute(
        "SELECT DISTINCT semester_key FROM courses "
        "WHERE identity_code_id = ? AND course_id != ?",
        (iid, cid),
    ).fetchall()
    actual = {r[0] for r in actual_rows}
    if stored != actual:
        mismatch_count += 1

print(f"Checked {len(all_courses)} courses: {mismatch_count} mismatches")

# Index check
idx = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_courses_identity'"
).fetchone()
print(f"Identity index exists: {idx is not None}")

# Trigger check
trig = conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='courses_au'"
).fetchone()
has_update_of = "UPDATE OF" in trig[0] if trig else False
print(f"Update trigger scoped to FTS columns: {has_update_of}")

store.close()

if mismatch_count > 0:
    print(f"\nFAILED: {mismatch_count} cross-reference mismatches found!")
    sys.exit(1)
else:
    print("\nPASSED: All cross-references are correct.")
