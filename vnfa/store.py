# -*- coding: utf-8 -*-
"""Lưu trữ append-only bằng CSV (sep=';'), chỉ ghi khi danh mục của quỹ thực sự đổi.

data/holdings/{FUND}.csv : mỗi lần phát hiện danh mục mới ghi thêm một khối dòng (kỳ công bố)
data/changes.csv         : nhật ký mua/bán đã phát hiện
data/funds.csv           : danh bạ quỹ (mã, công ty quản lý, loại)
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

SEP = ";"
HOLD_COLS = ["observed_date", "fund", "ticker", "weight", "rank", "volume", "price_k",
             "asset_value", "industry", "update_date", "content_hash"]
_STR = {"observed_date": str, "fund": str, "ticker": str, "content_hash": str, "update_date": str}


def content_hash(df: pd.DataFrame) -> str:
    """Băm tập (mã, tỷ trọng, khối lượng) để biết danh mục có thật sự đổi hay không."""
    d = df.sort_values("ticker")
    key = "|".join(
        f"{r.ticker}:{r.weight:.5f}:{'' if pd.isna(r.volume) else int(r.volume)}" for r in d.itertuples()
    )
    return hashlib.sha1(key.encode()).hexdigest()[:12]


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
        return pd.read_csv(p, sep=SEP, dtype=_STR)

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
        """Trả về (status, prev). status ∈ {'baseline','changed','unchanged'}."""
        if holdings is None or holdings.empty:
            return "empty", None
        h = content_hash(holdings)
        prev = self.last_disclosure(fund)
        if prev is not None and prev.content_hash.iloc[0] == h:
            return "unchanged", prev
        block = holdings.copy()
        block.insert(0, "observed_date", observed_date)
        block.insert(1, "fund", fund)
        block["content_hash"] = h
        self._last[fund] = block.reindex(columns=HOLD_COLS).reset_index(drop=True)
        if self.write:
            p = self._hold_path(fund)
            block.reindex(columns=HOLD_COLS).to_csv(p, sep=SEP, mode="a", header=not p.exists(), index=False)
        return ("baseline" if prev is None else "changed"), prev

    # ── nhật ký thay đổi ──
    def read_changes(self) -> pd.DataFrame:
        p = self.root / "changes.csv"
        return pd.read_csv(p, sep=SEP, dtype={"observed_date": str}) if p.exists() else pd.DataFrame()

    def append_changes(self, changes: pd.DataFrame) -> None:
        if changes is None or changes.empty or not self.write:
            return
        old = self.read_changes()
        allc = pd.concat([old, changes], ignore_index=True) if not old.empty else changes.copy()
        allc = allc.drop_duplicates(["observed_date", "fund", "ticker"], keep="last")
        allc = allc.sort_values(["observed_date", "fund", "ticker"])
        allc.to_csv(self.root / "changes.csv", sep=SEP, index=False)

    # ── danh bạ quỹ ──
    def upsert_funds(self, metas: list[dict], seen_date: str) -> None:
        if not self.write or not metas:
            return
        p = self.root / "funds.csv"
        new = pd.DataFrame(metas)
        new["last_seen"] = seen_date
        if p.exists():
            old = pd.read_csv(p, sep=SEP, dtype=str)
            new = pd.concat([old, new.astype(str)], ignore_index=True)
        new = new.drop_duplicates("code", keep="last").sort_values("code")
        new.to_csv(p, sep=SEP, index=False)

    def fund_names(self) -> dict:
        p = self.root / "funds.csv"
        if not p.exists():
            return {}
        f = pd.read_csv(p, sep=SEP, dtype=str)
        return dict(zip(f.code, f.mgmt_co))


# ───────────────────────── Bảng một-file cho dữ liệu bổ sung ─────────────────────────
class TableStore:
    """Mỗi bảng là một CSV chung cho mọi quỹ: data/{table}.csv (sep=';').

    append_if_changed(): chỉ ghi khi nội dung của quỹ đó khác khối gần nhất (so bằng hash).
    Cột đầu: observed_date, fund; cột cuối: content_hash.
    """

    def __init__(self, root: str | Path = "data", write: bool = True):
        self.root, self.write = Path(root), write
        self._hashes: dict[str, dict[str, str]] = {}
        if write:
            self.root.mkdir(parents=True, exist_ok=True)

    def path(self, table: str) -> Path:
        return self.root / f"{table}.csv"

    def read(self, table: str) -> pd.DataFrame:
        p = self.path(table)
        return pd.read_csv(p, sep=SEP, dtype=str) if p.exists() else pd.DataFrame()

    @staticmethod
    def _hash(df: pd.DataFrame) -> str:
        cols = sorted(df.columns)
        txt = df[cols].astype(str).sort_values(cols).to_csv(index=False)
        return hashlib.sha1(txt.encode()).hexdigest()[:12]

    def _last_hashes(self, table: str) -> dict[str, str]:
        if table not in self._hashes:
            t = self.read(table)
            self._hashes[table] = {} if t.empty else dict(t.groupby("fund").content_hash.last())
        return self._hashes[table]

    def append_if_changed(self, table: str, fund: str, df: pd.DataFrame, observed_date: str) -> str:
        """Trả về 'baseline' | 'changed' | 'unchanged' | 'empty'."""
        if df is None or df.empty:
            return "empty"
        h = self._hash(df)
        hashes = self._last_hashes(table)
        if hashes.get(fund) == h:
            return "unchanged"
        status = "changed" if fund in hashes else "baseline"
        hashes[fund] = h
        if self.write:
            block = df.copy()
            block.insert(0, "observed_date", observed_date)
            block.insert(1, "fund", fund)
            block["content_hash"] = h
            p = self.path(table)
            block.to_csv(p, sep=SEP, mode="a", header=not p.exists(), index=False)
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
        return pd.read_csv(p, sep=SEP, dtype={"fund": str, "date": str}) if p.exists() else pd.DataFrame(columns=["fund", "date", "nav"])

    def nav_last_date(self, fund: str, nav: pd.DataFrame | None = None) -> str | None:
        nav = self.read_nav() if nav is None else nav
        x = nav[nav.fund == fund]
        return None if x.empty else x.date.max()

    def upsert_nav(self, parts: dict[str, pd.DataFrame]) -> int:
        """Gộp NAV mới vào data/nav.csv (khóa: fund, date; giá trị mới ghi đè). Trả về số dòng mới/đổi."""
        if not parts:
            return 0
        old = self.read_nav()
        new = pd.concat([d.assign(fund=f) for f, d in parts.items() if not d.empty], ignore_index=True)
        if new.empty:
            return 0
        both = pd.concat([old.assign(_o=1), new.assign(_o=0)], ignore_index=True)
        both["nav"] = both.nav.astype(float)
        merged = both.sort_values(["fund", "date", "_o"]).drop_duplicates(["fund", "date"], keep="first")
        added = len(merged) - len(old)
        changed = 0
        if not old.empty:
            m = old.merge(new, on=["fund", "date"], suffixes=("_o", "_n"))
            changed = int(((m.nav_o.astype(float) - m.nav_n.astype(float)).abs() > 1e-9).sum())
        if self.write:
            merged[["fund", "date", "nav"]].sort_values(["fund", "date"]).to_csv(self.root / "nav.csv", sep=SEP, index=False)
        return added + changed

    # ── NAV chụp hằng ngày từ chi tiết quỹ (API lịch sử NAV của Fmarket bị cũ/thưa, xem README) ──
    def append_nav_snapshot(self, fund: str, nav: float, date: str) -> bool:
        """Ghi (fund;date;nav) vào data/nav_snapshots.csv khi NAV khác lần ghi trước. Trả về True nếu có ghi."""
        if "nav_snapshots" not in self._hashes:
            t = self.read("nav_snapshots")
            self._hashes["nav_snapshots"] = {} if t.empty else dict(zip(t.fund, t.nav.astype(float)))
        last = self._hashes["nav_snapshots"]
        if fund in last and abs(last[fund] - nav) < 1e-9:
            return False
        last[fund] = nav
        if self.write:
            p = self.path("nav_snapshots")
            pd.DataFrame([{"fund": fund, "date": date, "nav": nav}]).to_csv(
                p, sep=SEP, mode="a", header=not p.exists(), index=False)
        return True
