"""
archive_raw.py — 오래된 원시 센서 기록을 날짜별 압축 파일로 옮긴다(2026-10-11)
=============================================================================
DB(server/majubom.db)에는 최근 KEEP_DAYS일치 원시 기록만 두고, 그보다 오래된
tof_readings·mmw_readings 행을 하루 단위 gzip JSONL 파일로 옮긴 뒤 DB에서 지운다.

  server/raw_archive/2026-10-09/tof_readings.jsonl.gz
  server/raw_archive/2026-10-09/mmw_readings.jsonl.gz

- 한 줄 = DB 한 행(컬럼 이름 그대로). 파일을 먼저 끝까지 쓰고 디스크에 반영한 뒤에만 DB 행을 지운다.
- 같은 날짜 파일이 이미 있으면 뒤에 이어 붙인다(gzip 여러 묶음이 이어진 형태라 일반 gzip 도구로 그대로 읽힌다).
- 다른 테이블(sensor_clean_data, activity_log)의 raw_id/source_id는 옮긴 행의 id를 그대로 가리킨다.
  파일 안의 "id" 값으로 찾아갈 수 있다.

읽는 법(압축을 풀 필요 없음):
  python3 tools/archive_raw.py --read server/raw_archive/2026-10-09/tof_readings.jsonl.gz --limit 5
  zcat server/raw_archive/2026-10-09/tof_readings.jsonl.gz | head
  pandas.read_json("...jsonl.gz", lines=True)          # 압축 자동 인식

실행(매일 cron):
  python3 tools/archive_raw.py --keep-days 3
"""
import argparse
import gzip
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "server", "majubom.db")
OUT = os.path.join(ROOT, "server", "raw_archive")
TABLES = ("tof_readings", "mmw_readings")
BATCH = 20000


def archive(db_path, out_dir, keep_days, vacuum_free_mb):
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()   # 이 날짜 이전(미만)만 옮김
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    total = 0
    for table in TABLES:
        days = [r[0] for r in conn.execute(
            f"SELECT DISTINCT substr(timestamp, 1, 10) FROM {table} WHERE substr(timestamp, 1, 10) < ? ORDER BY 1",
            (cutoff,))]
        for day in days:
            folder = os.path.join(out_dir, day)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{table}.jsonl.gz")
            moved, last_id = 0, 0
            while True:
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE substr(timestamp, 1, 10) = ? AND id > ? ORDER BY id LIMIT ?",
                    (day, last_id, BATCH)).fetchall()
                if not rows:
                    break
                with gzip.open(path, "at", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                ids = [r["id"] for r in rows]
                with conn:
                    conn.executemany(f"DELETE FROM {table} WHERE id = ?", [(i,) for i in ids])
                last_id = ids[-1]
                moved += len(ids)
            total += moved
            print(f"[archive] {table} {day}: {moved}행 → {path}")
    page = conn.execute("PRAGMA page_size").fetchone()[0]
    free_mb = conn.execute("PRAGMA freelist_count").fetchone()[0] * page / 1e6
    if vacuum_free_mb is not None and free_mb >= vacuum_free_mb:
        print(f"[archive] 빈 공간 {free_mb:.0f}MB → VACUUM")
        conn.execute("VACUUM")
    conn.close()
    print(f"[archive] 완료: {total}행 이동, 기준 {cutoff} 이전, DB 빈 공간 {free_mb:.0f}MB")


def read(path, limit):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            sys.stdout.write(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--keep-days", type=int, default=3, help="DB에 남길 최근 일수(오늘 포함 전 N일)")
    ap.add_argument("--vacuum-free-mb", type=float, default=200,
                    help="옮긴 뒤 DB 빈 공간이 이 크기(MB) 이상이면 VACUUM으로 파일 크기를 줄인다")
    ap.add_argument("--read", metavar="FILE", help="압축 파일 내용을 한 줄씩 출력")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    if args.read:
        read(args.read, args.limit)
    else:
        archive(args.db, args.out, args.keep_days, args.vacuum_free_mb)


if __name__ == "__main__":
    main()
