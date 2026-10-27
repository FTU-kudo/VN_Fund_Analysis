# -*- coding: utf-8 -*-
"""Lưu trữ append-only bằng CSV (sep=';'), chỉ ghi khi danh mục của quỹ thực sự đổi.

data/holdings/{FUND}.csv : mỗi lần phát hiện danh mục mới ghi thêm một khối dòng (kỳ công bố)
data/changes.csv         : nhật ký thay đổi vị thế quan sát/suy luận và cờ bất định
data/funds.csv           : danh bạ quỹ (mã, công ty quản lý, loại)
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas._libs.parsers import STR_NA_VALUES

SEP = ";"
HOLD_COLS = ["observed_date", "fund", "ticker", "weight", "rank", "volume", "price_k",
             "asset_value", "industry", "update_date", "content_hash"]
_STR = {"observed_date": str, "fund": str, "ticker": str, "content_hash": str, "update_date": str}
ID_COLUMNS = frozenset({"code", "fund", "ticker"})


def read_csv_safe(path: str | Path, *, sep: str = SEP, dtype: Any = None) -> pd.DataFrame:
    """Read semicolon CSV while preserving literal 'NA'/'NAN'/'NULL' in identifier columns.

    Empty strings remain missing everywhere, and all non-identifier columns retain
    pandas' standard NA token semantics so missing values are never turned into text/zero.
    """
    p = Path(path)
    header = pd.read_csv(p, sep=sep, nrows=0)
    columns = list(header.columns)
    na_map = {col: {""} if col in ID_COLUMNS else STR_NA_VALUES for col in columns}
    effective_dtype = dtype
    if isinstance(dtype, dict):
        effective_dtype = {**{col: str for col in columns if col in ID_COLUMNS}, **dtype}
    elif dtype is None:
        id_present = {col: str for col in columns if col in ID_COLUMNS}
        effective_dtype = id_present or None
    return pd.read_csv(p, sep=sep, dtype=effective_dtype, keep_default_na=False, na_values=na_map)


def atomic_write_csv(df: pd.DataFrame, path: str | Path, *, sep: str = SEP) -> None:
    """Atomically write a complete CSV via temporary file + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        df.to_csv(tmp, sep=sep, index=False)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_append_csv(df: pd.DataFrame, path: str | Path, *, sep: str = SEP) -> None:
    """Atomically append rows to a CSV while preserving existing file bytes verbatim."""
    if df is None or df.empty:
        return
    target = Path(path)
    if not target.exists():
        atomic_write_csv(df, target, sep=sep)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(target, tmp)
        # Ensure trailing newline before appending if existing file lacked one.
        with open(tmp, "rb+") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() > 0:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    fh.write(b"\n")
        df.to_csv(tmp, sep=sep, mode="a", header=False, index=False)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def content_hash(df: pd.DataFrame) -> str:
    """Băm tập (mã, tỷ trọng, khối lượng) để biết danh mục có thật sự đổi hay không."""
    d = df.assign(_ticker_str=df.ticker.astype(str)).sort_values("_ticker_str", kind="stable")
    parts = []
    for r in d.itertuples():
        vol_txt = ""
        if pd.notna(r.volume) and np.isfinite(float(r.volume)):
            vol_txt = str(int(round(float(r.volume))))
        parts.append(f"{r.ticker}:{float(r.weight):.5f}:{vol_txt}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def _canonical_cell(value: Any) -> str:
    if value is None or value is pd.NA:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text == "":
        return ""
    try:
        num = float(text)
        if np.isfinite(num):
            rounded = round(num, 8)
            if rounded == 0.0:
                return "0"
            formatted = f"{rounded:.8f}".rstrip("0").rstrip(".")
            return formatted or "0"
    except (TypeError, ValueError):
        pass
    return text


class Store:
    def __init__(self, root: str | Path = "data", write: bool = True, subdir: str = "holdings"):
        self.root = Path(root)
        self.write = write
        self.hold_dir = self.root / subdir
        self._last: dict[str, pd.DataFrame | None] = {}
        if write:
            self.hold_dir.mkdir(parents=True, exist_ok=True)

    # ── danh mục ──
    def _hold_path(self, fund: str) -> Path:
        return self.hold_dir / f"{fund}.csv"

    def read_holdings(self, fund: str) -> pd.DataFrame:
        p = self._hold_path(fund)
        if not p.exists():
            return pd.DataFrame(columns=HOLD_COLS)
        return read_csv_safe(p, sep=SEP, dtype=_STR)

    def last_disclosure(self, fund: str) -> pd.DataFrame | None:
        if fund not in self._last:
            df = self.read_holdings(fund)
            if df.empty:
                self._last[fund] = None
            else:
                last = df.iloc[-1]
                m = (df.observed_date == last.observed_date) & (df.content_hash == last.content_hash)
                self._last[fund] = df[m].reset_index(drop=True)
        return self._last[fund]

    def append_if_changed(self, fund: str, holdings: pd.DataFrame, observed_date: str):
        """Trả về (status, prev). status ∈ {'baseline','changed','unchanged','empty'}."""
        if holdings is None or holdings.empty:
            return "empty", None
        try:
            observed_date = pd.Timestamp(observed_date).date().isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"observed_date không hợp lệ: {observed_date!r}") from exc
        h = content_hash(holdings)
        prev = self.last_disclosure(fund)
        if prev is not None:
            previous_date = pd.to_datetime(prev.observed_date, errors="raise").max().date().isoformat()
            if observed_date < previous_date:
                raise ValueError(f"Snapshot ngược thời gian cho {fund}: {observed_date} < {previous_date}")
            if prev.content_hash.iloc[0] == h:
                return "unchanged", prev
        block = holdings.copy()
        block.insert(0, "observed_date", observed_date)
        block.insert(1, "fund", fund)
        block["content_hash"] = h
        aligned = block.reindex(columns=HOLD_COLS).reset_index(drop=True)
        if self.write:
            atomic_append_csv(aligned, self._hold_path(fund), sep=SEP)
        self._last[fund] = aligned
        return ("baseline" if prev is None else "changed"), prev

    # ── nhật ký thay đổi ──
    def read_changes(self) -> pd.DataFrame:
        p = self.root / "changes.csv"
        return read_csv_safe(p, sep=SEP, dtype={"observed_date": str}) if p.exists() else pd.DataFrame()

    def append_changes(self, changes: pd.DataFrame) -> None:
        if changes is None or changes.empty or not self.write:
            return
        old = self.read_changes()
        allc = pd.concat([old, changes], ignore_index=True) if not old.empty else changes.copy()
        if "asset_class" in allc.columns:
            allc["_ac_key"] = allc["asset_class"].fillna("STOCK").astype(str)
            allc = allc.drop_duplicates(["observed_date", "fund", "_ac_key", "ticker"], keep="last")
            allc = allc.sort_values(["observed_date", "fund", "_ac_key", "ticker"], kind="stable")
            allc = allc.drop(columns=["_ac_key"])
        else:
            allc = allc.drop_duplicates(["observed_date", "fund", "ticker"], keep="last")
            allc = allc.sort_values(["observed_date", "fund", "ticker"], kind="stable")
        atomic_write_csv(allc, self.root / "changes.csv", sep=SEP)

    # ── danh bạ quỹ ──
    def upsert_funds(self, metas: list[dict], seen_date: str) -> None:
        if not self.write or not metas:
            return
        p = self.root / "funds.csv"
        new = pd.DataFrame(metas)
        new["last_seen"] = seen_date
        if p.exists():
            old = read_csv_safe(p, sep=SEP, dtype=str)
            new = pd.concat([old, new], ignore_index=True)
        new = new.drop_duplicates("code", keep="last").sort_values("code", kind="stable")
        atomic_write_csv(new, p, sep=SEP)

    def fund_names(self) -> dict:
        p = self.root / "funds.csv"
        if not p.exists():
            return {}
        f = read_csv_safe(p, sep=SEP, dtype=str)
        return dict(zip(f.code, f.mgmt_co))


# ───────────────────────── Bảng một-file cho dữ liệu bổ sung ─────────────────────────
class TableStore:
    """Mỗi bảng là một CSV chung cho mọi quỹ: data/{table}.csv (sep=';').

    append_if_changed(): chỉ ghi khi nội dung của quỹ đó khác khối gần nhất (so bằng hash).
    Cột đầu: observed_date, fund; cột cuối: content_hash.
    """

    def __init__(self, root: str | Path = "data", write: bool = True):
        self.root, self.write = Path(root), write
        self._hashes: dict[str, dict[str, set[str]]] = {}
        self._last_dates: dict[str, dict[str, str]] = {}
        self._snapshot_last: dict[str, tuple[str, float]] | None = None
        if write:
            self.root.mkdir(parents=True, exist_ok=True)

    def path(self, table: str) -> Path:
        return self.root / f"{table}.csv"

    def read(self, table: str) -> pd.DataFrame:
        p = self.path(table)
        return read_csv_safe(p, sep=SEP, dtype=str) if p.exists() else pd.DataFrame()

    @staticmethod
    def _hash(df: pd.DataFrame) -> str:
        cols = [c for c in sorted(df.columns) if c not in {"observed_date", "fund", "content_hash"}]
        if not cols:
            return hashlib.sha1(b"").hexdigest()[:12]
        canonical = pd.DataFrame({col: [_canonical_cell(v) for v in df[col]] for col in cols})
        txt = canonical.sort_values(cols, kind="stable").to_csv(index=False)
        return hashlib.sha1(txt.encode()).hexdigest()[:12]

    def _last_hashes(self, table: str) -> dict[str, set[str]]:
        if table not in self._hashes:
            t = self.read(table)
            if t.empty:
                self._hashes[table], self._last_dates[table] = {}, {}
            else:
                hashes: dict[str, set[str]] = {}
                dates: dict[str, str] = {}
                for fund, group in t.groupby("fund", sort=False):
                    fund_str = str(fund)
                    last_row = group.iloc[-1]
                    stored_hash = str(last_row.content_hash)
                    dates[fund_str] = str(last_row.observed_date)
                    latest_block = group[
                        (group.observed_date == last_row.observed_date)
                        & (group.content_hash == last_row.content_hash)
                    ]
                    hashes[fund_str] = {stored_hash, self._hash(latest_block)}
                self._hashes[table] = hashes
                self._last_dates[table] = dates
        return self._hashes[table]

    def append_if_changed(self, table: str, fund: str, df: pd.DataFrame, observed_date: str) -> str:
        """Trả về 'baseline' | 'changed' | 'unchanged' | 'empty'."""
        if df is None or df.empty:
            return "empty"
        try:
            observed_date = pd.Timestamp(observed_date).date().isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"observed_date không hợp lệ: {observed_date!r}") from exc
        h = self._hash(df)
        hashes = self._last_hashes(table)
        dates = self._last_dates[table]
        fund_key = str(fund)
        if fund_key in dates and observed_date < pd.Timestamp(dates[fund_key]).date().isoformat():
            raise ValueError(f"Snapshot {table} ngược thời gian cho {fund}: {observed_date} < {dates[fund_key]}")
        if h in hashes.get(fund_key, set()):
            return "unchanged"
        status = "changed" if fund_key in hashes else "baseline"
        if self.write:
            block = df.copy()
            block.insert(0, "observed_date", observed_date)
            block.insert(1, "fund", fund)
            block["content_hash"] = h
            atomic_append_csv(block, self.path(table), sep=SEP)
        hashes[fund_key] = {h}
        dates[fund_key] = observed_date
        return status

    def latest(self, table: str) -> pd.DataFrame:
        """Khối mới nhất của từng quỹ."""
        t = self.read(table)
        if t.empty:
            return t
        last = t.groupby("fund").content_hash.transform("last")
        keep = (t.content_hash == last)
        return t[keep & (t.observed_date == t.groupby(["fund", "content_hash"]).observed_date.transform("max"))]

    # ── NAV ──
    def read_nav(self) -> pd.DataFrame:
        p = self.root / "nav.csv"
        return read_csv_safe(p, sep=SEP, dtype={"fund": str, "date": str}) if p.exists() else pd.DataFrame(columns=["fund", "date", "nav"])

    def nav_last_date(self, fund: str, nav: pd.DataFrame | None = None) -> str | None:
        nav = self.read_nav() if nav is None else nav
        x = nav[nav.fund == fund]
        return None if x.empty else x.date.max()

    def upsert_nav(self, parts: dict[str, pd.DataFrame]) -> int:
        """Gộp NAV mới vào data/nav.csv (khóa: fund, date; giá trị mới ghi đè). Trả về số dòng mới/đổi."""
        if not parts:
            return 0
        old = self.read_nav()
        frames = [d.assign(fund=f) for f, d in parts.items() if d is not None and not d.empty]
        if not frames:
            return 0
        new = pd.concat(frames, ignore_index=True)
        if new.empty:
            return 0
        if not {"fund", "date", "nav"}.issubset(new.columns):
            raise ValueError("NAV history thiếu một trong các cột fund/date/nav")
        new = new.copy()
        new["date"] = pd.to_datetime(new.date, errors="raise").dt.strftime("%Y-%m-%d")
        new["nav"] = pd.to_numeric(new.nav, errors="raise")
        if new.fund.isna().any() or (new.fund.astype(str).str.strip() == "").any() \
                or not np.isfinite(new.nav.astype(float)).all() or (new.nav.astype(float) <= 0).any():
            raise ValueError("NAV history chỉ được ghi với mã quỹ hợp lệ và NAV dương/hữu hạn")
        new = new.drop_duplicates(["fund", "date"], keep="last")
        both = pd.concat([old.assign(_o=1), new.assign(_o=0)], ignore_index=True)
        both["nav"] = both.nav.astype(float)
        merged = both.sort_values(["fund", "date", "_o"], kind="stable").drop_duplicates(["fund", "date"], keep="first")
        added = len(merged) - len(old)
        changed = 0
        if not old.empty:
            m = old.merge(new, on=["fund", "date"], suffixes=("_o", "_n"))
            changed = int(((m.nav_o.astype(float) - m.nav_n.astype(float)).abs() > 1e-9).sum())
        if self.write:
            out = merged[["fund", "date", "nav"]].sort_values(["fund", "date"], kind="stable")
            atomic_write_csv(out, self.root / "nav.csv", sep=SEP)
        return added + changed

    # ── NAV chụp hằng ngày từ chi tiết quỹ (API lịch sử NAV của Fmarket bị cũ/thưa, xem README) ──
    def append_nav_snapshot(self, fund: str, nav: float, date: str) -> bool:
        """Ghi lần QUAN SÁT NAV theo ngày, kể cả khi giá trị không đổi.

        `date` là ngày hệ thống quan sát, không khẳng định là ngày hiệu lực của NAV. Ghi
        các ngày không đổi giúp không nhầm "NAV chưa thay đổi" với "dữ liệu đã cũ". Khóa
        logic là (fund, date); nếu API trả NAV khác trong cùng ngày, lưu revision cuối và
        các consumer phải deduplicate theo khóa này, giữ dòng cuối.
        """
        try:
            obs_date = pd.Timestamp(date).date().isoformat()
            nav_value = float(nav)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"NAV snapshot không hợp lệ: fund={fund!r}, date={date!r}, nav={nav!r}") from exc
        fund_clean = str(fund).strip() if fund is not None and not pd.isna(fund) else ""
        if not fund_clean or not np.isfinite(nav_value) or nav_value <= 0:
            raise ValueError(f"NAV snapshot phải có mã quỹ và NAV dương hữu hạn: {fund!r}, {nav!r}")

        if self._snapshot_last is None:
            t = self.read("nav_snapshots")
            self._snapshot_last = {}
            if not t.empty:
                if not {"fund", "date", "nav"}.issubset(t.columns):
                    raise ValueError("nav_snapshots.csv thiếu một trong các cột fund/date/nav")
                t = t.copy()
                t["date"] = pd.to_datetime(t.date, errors="raise").dt.strftime("%Y-%m-%d")
                t["nav"] = pd.to_numeric(t.nav, errors="raise")
                t = t.sort_values(["fund", "date"], kind="stable")
                for row in t.groupby("fund", sort=False).tail(1).itertuples(index=False):
                    self._snapshot_last[str(row.fund)] = (str(row.date), float(row.nav))

        previous = self._snapshot_last.get(fund_clean)
        if previous is not None:
            last_date, last_nav = previous
            if obs_date < last_date:
                raise ValueError(f"NAV snapshot ngược thời gian cho {fund_clean}: {obs_date} < {last_date}")
            if obs_date == last_date and abs(last_nav - nav_value) < 1e-9:
                return False

        if self.write:
            atomic_append_csv(
                pd.DataFrame([{"fund": fund_clean, "date": obs_date, "nav": nav_value}]),
                self.path("nav_snapshots"), sep=SEP,
            )
        self._snapshot_last[fund_clean] = (obs_date, nav_value)
        return True
