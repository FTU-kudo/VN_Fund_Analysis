# Báo Cáo Tổng Kết Kiểm Định, Sửa Lỗi & Gia Cố Vận Hành — `FTU-kudo/VN_Fund_Analysis`

- **Ngày thực hiện:** `2026-10-27` (`Asia/Ho_Chi_Minh`) `[ĐÃ CHẠY XÁC MINH]`
- **Repository:** `FTU-kudo/VN_Fund_Analysis` `[ĐÃ CHẠY XÁC MINH]`
- **Commit gốc (`main`):** `7fb0af40be11576c7b8f3080be6d6f07aa77380f` `[ĐÃ CHẠY XÁC MINH]`
- **Nhánh làm việc:** `arena/f47ebb91-vn-fund-analysis` (nhánh phiên cố định theo ràng buộc môi trường Arena Agent Mode; không đổi tên sang `agent/master-20261027` để bảo toàn liên kết phiên và PR) `[ĐÃ CHẠY XÁC MINH]`
- **Báo cáo kiểm toán giai đoạn đầu:** [`docs/AGENT_AUDIT_20261027.md`](AGENT_AUDIT_20261027.md) `[ĐÃ CHẠY XÁC MINH]`

> Quy ước bằng chứng xuyên suốt báo cáo: mọi mệnh đề thực nghiệm đều gắn một trong ba nhãn **`[ĐÃ CHẠY XÁC MINH]`**, **`[SUY LUẬN]`**, hoặc **`[CHƯA XÁC MINH]`**.

---

## 1. Tóm tắt điều hành (Executive Summary)

1. **Đối chiếu độc lập toàn bộ baseline & GitHub Actions:** Đã xác minh trên checkout thực tế và GitHub REST API rằng `data/` có `90` tệp (`89` CSV + `last_run.json`), `69` quỹ (`STOCK: 34`, `BALANCED: 11`, `BOND: 24`), `46` tệp cổ phiếu (`407` dòng), `33` tệp trái phiếu (`180` dòng), `47.121` dòng `nav.csv` (`59/69` quỹ, dừng ở `2025-10-31`), `69` dòng `nav_snapshots.csv`, `139` HTTP `200` source events (`129` `success`, `10` `empty_response`), và chuỗi sự kiện PR #1 (`35522ba747c2`) → Pages run `40459352639` → Daily run `40461812195` (`7fb0af40be11`) → Pages run `40462259636` `[ĐÃ CHẠY XÁC MINH]`.
2. **Nén an toàn sidecar provenance & giảm tải Git:** Theo phê duyệt của chủ dự án (`migrate_with_backup`), `data/research_observations.csv` đã được sao lưu ngoài Git kèm kiểm chứng SHA-256 và nén `47.121` dòng điểm lẻ `nav_history` thành `59` dòng summary theo quỹ/lần tải (`19.741.555` bytes → `1.091.787` bytes, giảm **94,47%**, bảo toàn nguyên vẹn `2.192` dòng quan sát phi-NAV và `59/59` mã băm chuỗi `nav.csv`) `[ĐÃ CHẠY XÁC MINH]`.
3. **Tăng tốc exporter & khôi phục provenance timestamp cho holdings:** Sửa lệch khóa `record_key` giữa `run_daily.py` và `dashboard_export.py`, bổ sung index bộ nhớ đệm O(1) cho `ProvenanceStore`, giúp `export_dashboard("data", ...)` giảm thời gian chạy từ **`21,6s` xuống `3,456s` (-84,0%)** và nâng tỷ lệ khớp `observed_at` trên `holdings.json` từ **`0/587` lên `587/587` (100%)** `[ĐÃ CHẠY XÁC MINH]`.
4. **Kích hoạt SLA độ mới, loại bỏ nhiễu cảnh báo giả ở trái phiếu & nâng cấp `source_runs.csv` v2:** Bật ngưỡng SLA được duyệt trong `config.toml` (`45d` cho `fund_holdings`/`fund_bond_holdings`/`allocation`, `3d` cho `nav_snapshots`, `180d` cho `fees`), phát hiện chính xác `allocation` đang `stale` (`56d > 45d`); loại bỏ `180` cảnh báo `missing_price_k` mang tính cấu trúc trên trái phiếu Fmarket; nâng cấp `data/source_runs.csv` lên v2 (thêm cột `fund_code`, giữ `NA` cho `139` dòng lịch sử — tuyệt đối không đoán từ thứ tự dòng) `[ĐÃ CHẠY XÁC MINH]`.
5. **Khắc phục lỗi Compare view & hoàn thiện UI nghiên cứu tĩnh:** Tái hiện và sửa triệt để lỗi giữ tham số `a`/`b` lệch nhóm khi đổi `category` trên trang `So sánh quỹ`, hiển thị đầy đủ `1.467` bản ghi `fund_facts` (`allocation`, `industry`, `fees`, `profile`), hiển thị tỷ lệ kèm mẫu số cho `Publication time chưa có` (`2.251 / 2.251 (100%)`), chuẩn hóa dấu `+/-` (`−` Unicode) theo `vi-VN` và hỗ trợ `prefers-reduced-motion` `[ĐÃ CHẠY XÁC MINH]`.
6. **Gia cố bảo mật CI/CD & kiểm thử hồi quy offline:** Pin toàn bộ GitHub Actions sang commit SHA 40 ký tự chạy runtime Node 24 (`checkout@v7.0.1`, `setup-python@v7.1.0`, `setup-node@v7.1.0`, `upload-pages-artifact@v5.0.0`, `configure-pages@v6.0.0`, `deploy-pages@v5.0.1`), sửa lỗi unquoted `$LIMIT`, tích hợp `check_data_budget.py` + `write_workflow_status.py` (`if: always()`), nâng bộ test offline lên **`69` Python tests (độ phủ `vnfa/` đạt `86%`)** và **`12` Node UI tests (`100% pass`)** `[ĐÃ CHẠY XÁC MINH]`.

---

## 2. Bảng chỉ số Trước vs Sau (Baseline vs Post-Remediation)

| Hạng mục đo lường | Baseline (`7fb0af4`) | Sau xử lý (nhánh hiện tại) | Bằng chứng |
|---|---:|---:|---|
| Python offline tests (`pytest -q`) | `53 passed` | **`69 passed`** (`+16` bài test bất biến & hồi quy) | `[ĐÃ CHẠY XÁC MINH]` |
| Độ phủ dòng lệnh gói `vnfa/` (`pytest --cov=vnfa`) | `79%` (`2.671` stmts) | **`86%`** (`2.804` stmts, vượt ngưỡng `≥ 85%`) | `[ĐÃ CHẠY XÁC MINH]` |
| Node offline UI tests (`node --test`) | `9 passed` | **`12 passed`** | `[ĐÃ CHẠY XÁC MINH]` |
| Kích thước `data/research_observations.csv` | `19.741.555` bytes (`49.313` dòng) | **`1.091.787` bytes** (`2.251` dòng; **-94,47%**) | `[ĐÃ CHẠY XÁC MINH]` |
| Tổng dung lượng thư mục `data/` (`90` tệp) | `21.298.029` bytes (`21,30 MB`) | **`2.638.560` bytes** (`2,639 MB`; **-87,61%**) | `[ĐÃ CHẠY XÁC MINH]` |
| Dự phóng dung lượng `data/` sau 90 ngày (`check_data_budget.py`) | `~1.798 MB` (nếu backfill/tải lại point-NAV) `[SUY LUẬN]` | **`16,611 MB`** (`status: "ok"`, `< 25 MB` warn) | `[ĐÃ CHẠY XÁC MINH]` |
| Thời gian chạy `export_dashboard("data", ...)` | `21,6s` | **`3,456s`** (**-84,0%**) | `[ĐÃ CHẠY XÁC MINH]` |
| Khớp `observed_at` trên `holdings.json` (`587` dòng) | `0 / 587` (`0%`) | **`587 / 587` (`100%`)** | `[ĐÃ CHẠY XÁC MINH]` |
| Cảnh báo bất thường giả trên `fund_bond_holdings` | `180` (`missing_price_k` do schema nguồn không có) | **`0`** (`censored_top_n` tách riêng vào `structural_warnings`) | `[ĐÃ CHẠY XÁC MINH]` |
| Đánh giá SLA độ mới trên `15` dataset | `0` dataset được đánh giá (`not_assessed_no_sla`) | **`5` dataset có SLA** (`4 within_sla`, `1 stale`: `allocation` `56d > 45d`) | `[ĐÃ CHẠY XÁC MINH]` |

---

## 3. Sổ cái lỗi đã tái hiện, sửa và khóa bằng test (Bug Ledger `BUG-01`..`BUG-09`)

| Mã lỗi | Mô tả gốc & Tái hiện | Giải pháp kỹ thuật đã áp dụng | Bài test hồi quy khóa lỗi | Trạng thái |
|---|---|---|---|---|
| **BUG-01** | Đổi `category` trên trang `So sánh quỹ` không cập nhật ngay danh sách ứng viên và giữ tham số URL `a`/`b` lệch nhóm (`dashboard/app.mjs`) `[ĐÃ CHẠY XÁC MINH]` | Bổ sung `sanitizeCompareSelection` + `resolveCompareState`, gắn listener `change` trên `#compare-category` để xóa ngay `a`/`b` ngoài nhóm và nút `Xóa lựa chọn ngoài nhóm` cho URL deep-link lệch nhóm `[ĐÃ CHẠY XÁC MINH]` | `tests/test_dashboard_ui.mjs`: `compare category change clears stale out-of-category funds...` | **Đã sửa** (`b948d98`) |
| **BUG-02** | `research_observations.csv` phình `19,74 MB` (`47.121` dòng điểm `nav_history`), `ProvenanceStore` đọc lại CSV mỗi lần append (`O(N^2)`), `export_dashboard.py` mất `21,6s` và khớp `0/587` `observed_at` cho holdings `[ĐÃ CHẠY XÁC MINH]` | Chuyển `run_daily.py`, `backfill_hat_ccq.py` sang ghi `1` dòng summary `nav_history`/quỹ; tạo `scripts/compact_provenance.py` nén `data/research_observations.csv`; cache ID bộ nhớ cho `ProvenanceStore`; khớp `record_key` `effective_date\|ticker` trong `dashboard_export.py` `[ĐÃ CHẠY XÁC MINH]` | `tests/test_foundation.py`: `test_provenance_compaction_and_run_daily_nav_summary_and_observed_date`; `tests/test_dashboard_export.py` | **Đã sửa** (`0583448`, `5f51d93`) |
| **BUG-03** | `validate_bond_holdings` phát `180` cảnh báo giả `missing_price_k`; `inventory.py` dùng `pd.read_csv` thường; chưa áp SLA; thiếu `fund_code` trong `source_runs.csv` và thiếu `workflow_status.json` `[ĐÃ CHẠY XÁC MINH]` | Tách `STRUCTURAL_WARNING_CODES` và `optional_price_columns` trong `vnfa/quality.py`; chuyển `inventory.py` sang `read_csv_safe`; kích hoạt SLA trong `config.toml`; nâng cấp `source_runs.csv` v2 + `scripts/migrate_source_runs.py`; thêm `scripts/write_workflow_status.py` `[ĐÃ CHẠY XÁC MINH]` | `tests/test_foundation.py`: `test_freshness_sla_states_bond_price_noise...`; `tests/test_dashboard_export.py`: `test_export_sanitized_workflow_status_and_sla_stale_issue` | **Đã sửa** (`e8a5bc4`) |
| **BUG-04** | `run_daily.py` truyền `observed_date=""` vào `ProvenanceStore.append_observations`, khiến coverage `observed_date` trên `research_observations.csv` bằng `0%` `[ĐÃ CHẠY XÁC MINH]` | Truyền `observed_date=today` kèm `observed_date_basis="response_observed_at"` khi có `observed_at` (hoặc `"collector_run_date"` khi fallback) trong `scripts/run_daily.py` `[ĐÃ CHẠY XÁC MINH]` | `tests/test_foundation.py`: `test_provenance_compaction_and_run_daily_nav_summary_and_observed_date` | **Đã sửa** (`5f51d93`) |
| **BUG-05** | `vnfa/fmarket.py:_ms_to_date(0)` dùng `if not ms:` nên đánh rơi epoch `0` (`1970-01-01`), và chuỗi rác trả `None` không phân biệt `[ĐÃ CHẠY XÁC MINH]` | Sửa điều kiện `ms is None or isinstance(ms, bool)` và kiểm tra `math.isfinite(val)` trước khi quy đổi timezone `Asia/Ho_Chi_Minh` `[ĐÃ CHẠY XÁC MINH]` | `tests/test_core.py`: `test_ms_to_date_handles_zero_epoch_and_rejects_bool_or_garbage` | **Đã sửa** (`0583448`) |
| **BUG-06** | `vnfa/store.py` dùng `pd.read_csv` mặc định tự biến ticker `"NA"` / `"NAN"` / `"NULL"` thành `NaN`; `allocation.csv` có nhiễu float IEEE-754 gây lệch hash `[ĐÃ CHẠY XÁC MINH]` | Thêm `keep_default_na=False` + `SAFE_NA_VALUES` trong `read_csv_safe` (hỗ trợ cả `Path` và `BytesIO`); chuẩn hóa số thực `12g` khi hash và chấp nhận cả stored hash lẫn canonical hash trong `TableStore._last_hashes` `[ĐÃ CHẠY XÁC MINH]` | `tests/test_core.py`: `test_read_csv_safe_preserves_na_and_null_tickers_while_parsing_missing_numerics`, `test_table_store_deduplicates_legacy_float_noise_blocks` | **Đã sửa** (`0583448`, `5f51d93`) |
| **BUG-07** | `renderFundDetail` bỏ qua `industry`, `fees`, `profile` dù `fund_facts.json` đã export `1.467` dòng; `Publication time chưa có` chỉ hiện số thô không có mẫu số; dấu âm dùng `-` ASCII `[ĐÃ CHẠY XÁC MINH]` | Render đầy đủ `industry`, `fees` (`begin`/`end`/`program`), `profile` (`management_fee_pct`, `first_issue_date`, `risk_level`); thêm `formatMissingRate` (`2.251 / 2.251 (100%)`) và `signedPercentFraction`/`signedPercentPoints` (`+`/`−`) `[ĐÃ CHẠY XÁC MINH]` | `tests/test_dashboard_ui.mjs`: `missing-rate, signed vi-VN deltas, and fund_facts grouping...` | **Đã sửa** (`b948d98`) |
| **BUG-08** | `.github/workflows/daily.yml` dùng `$LIMIT` không quote (`args="$args --limit $LIMIT"`), `permissions: contents: write` cấp workflow-level, và GitHub Actions dùng tag nổi Node 20 (`@v4`/`@v5`) `[ĐÃ CHẠY XÁC MINH]` | Chuyển `permissions: contents: read` ở workflow-level, hạ `contents: write` xuống job-level, kiểm tra regex `^[1-9][0-9]*$` và truyền mảng `"${args[@]}"`, pin toàn bộ Actions sang full commit SHA chạy Node 24 + thêm `.github/dependabot.yml` `[ĐÃ CHẠY XÁC MINH]` | Kiểm tra cú pháp YAML qua `pyyaml` & đối chiếu SHA trên `github.com/actions/*` `[ĐÃ CHẠY XÁC MINH]` | **Đã sửa** (Commit 7) |
| **BUG-09** | `scripts/backfill_hat_ccq.py` ghi `47.121` dòng điểm lẻ `nav_history`, nhân đôi dòng `legacy_nav_csv` / `quality_reports` khi chạy lại Pass 2, thiếu `--dry-run`/`--max-commits`; `analyze_reaction.py` dùng `pd.read_csv` trực tiếp `[ĐÃ CHẠY XÁC MINH]` | Thêm `--dry-run`, `--max-commits`, `manifest.json`, dedup `(fund, series_sha256)` và `run_id` tất định cho `backfill_hat_ccq.py`; kiểm tra 2-pass staging trên `5` commit thật của `hat-ccq` (`88` tệp trùng SHA-256 tuyệt đối ở Pass 2); sửa `analyze_reaction.py` và `export_holdings_long.py` dùng `read_csv_safe` + `validate_nav_snapshot` `[ĐÃ CHẠY XÁC MINH]` | `tests/test_foundation.py`: `test_backfill_hat_ccq_dry_run_idempotency_and_summary_provenance`, `test_analyze_reaction_and_export_holdings_long_preserve_na_ticker_and_validate_nav` | **Đã sửa** (`840f147`) |

---

## 4. Kết quả chi tiết theo từng Giai đoạn (Phases 0–8)

### Phase 0 — Kiểm toán độc lập & Cổng phê duyệt
- Hoàn thành báo cáo [`docs/AGENT_AUDIT_20261027.md`](AGENT_AUDIT_20261027.md) và thu thập 4 quyết định quản trị từ chủ dự án: (1) giữ `hat-ccq` ở chế độ staging test, không nạp vào `data/`; (2) nén `data/research_observations.csv` kèm backup SHA-256; (3) bật SLA độ mới `45/45/45/3/180` ngày; (4) giữ Phase 6 ở trạng thái `BLOCKED` cho tới khi có `smart_money_quant.py` và dữ liệu Yuanta `[ĐÃ CHẠY XÁC MINH]`.

### Phase 1 — Kiểm chứng staging `scripts/backfill_hat_ccq.py` & Giấy phép nguồn
- Kiểm tra API giấy phép `https://api.github.com/repos/huyhn-ba-po/hat-ccq/license` trả về `404 Not Found` dù `README.md` của repo gốc ghi `[MIT](LICENSE)`, đồng thời quyền tái phân phối dữ liệu Fmarket chưa được xác minh `[ĐÃ CHẠY XÁC MINH]`.
- Chạy thử nghiệm 2-pass thực tế trên shallow clone `/tmp/vnfa_hat_ccq_stage` (`--max-commits 5`, từ `34f494e3...` ngày `2026-10-22` đến `2eefdc9f...` ngày `2026-10-26`, `commit_list_sha256 = a739aca396872fd050b42fb5a1a033ed56dbd31580f4db778bd3e2978aeb8e78`):
  - `--dry-run`: `0` tệp sinh ra `[ĐÃ CHẠY XÁC MINH]`.
  - Pass 1: `69` quỹ, `47.121` dòng `nav.csv`, `59` dòng summary `nav_history`, `1.933` dòng provenance, `276` dòng quality reports `[ĐÃ CHẠY XÁC MINH]`.
  - Pass 2: `0` dòng append mới trên mọi bảng; toàn bộ `88` tệp trong thư mục staging khớp SHA-256 tuyệt đối giữa Pass 1 và Pass 2; thư mục `data/` của repo không bị chạm tới và `/tmp/vnfa_hat_ccq_stage` đã được dọn sạch `[ĐÃ CHẠY XÁC MINH]`.

### Phase 2 — Nén `data/research_observations.csv` & Ngân sách dung lượng Git
- Sao lưu ngoài Git với kiểm chứng SHA-256 trước khi ghi (`before_sha256 = 55e83a3f0c8c500e13d403ce3fc737d0d19ef6bc8cade41ea1d49f2b574bb301`, `19.741.555` bytes, `49.313` dòng) `[ĐÃ CHẠY XÁC MINH]`.
- Sau khi chạy `scripts/compact_provenance.py`: còn `1.091.787` bytes (`2.251` dòng, `after_sha256 = 317e17fcb243cc42ed9447caf3c248c13889dd73d48c75684206bdded0a31e1c`), tiết kiệm `18.649.768` bytes (**94,47%**) `[ĐÃ CHẠY XÁC MINH]`.
- Đối chiếu toàn vẹn: `2.192` dòng thuộc 8 dataset phi-NAV khớp tuyệt đối (`pandas.testing.assert_frame_equal`); `59/59` dòng summary `nav_history` khớp mã băm SHA-256 chuỗi với `data/nav.csv`; chạy lại Pass 2 trả `already_compact = true`, `bytes_saved = 0` `[ĐÃ CHẠY XÁC MINH]`.
- Tích hợp `scripts/check_data_budget.py` vào CI (`status = "ok"`, `2,639 MB` hiện tại, dự phóng 90 ngày `16,611 MB` < ngưỡng cảnh báo `25 MB` / ngưỡng lỗi `80 MB`) `[ĐÃ CHẠY XÁC MINH]`.

### Phase 3 — SLA độ mới, Khử nhiễu trái phiếu, `source_runs.csv` v2 & `workflow_status.json`
- Bật `[freshness]` trong `config.toml` (`fund_holdings = 45`, `fund_bond_holdings = 45`, `allocation = 45`, `nav_snapshots = 3`, `fees = 180`), phân biệt rõ 7 trạng thái: `within_sla`, `stale`, `unavailable_missing`, `empty_dataset`, `schema_mismatch`, `unknown_no_dates`, `not_assessed_no_sla` `[ĐÃ CHẠY XÁC MINH]`.
- Nâng cấp `data/source_runs.csv` (`139` dòng) từ v1 (`16` cột, SHA-256 `dcea37f7f0acd3dfc431b58fc12ab590ce916d705d0bd5e0ef6d3a612202dde4`) sang v2 (`17` cột có `fund_code`, SHA-256 `5dac05d4070b9d1ba2f673d8616f729e56698bfe409cc871346fd48b21afd363`), giữ trống `fund_code` cho `139` dòng lịch sử và tự động gắn `fund_code` cho mọi lần gọi `FmarketClient.product_detail` / `nav_history` về sau `[ĐÃ CHẠY XÁC MINH]`.
- Thêm `scripts/write_workflow_status.py` chỉ xuất các trường enum/số/timestamp an toàn (`schema_version`, `workflow`, `conclusion`, `recorded_at`, `trigger`, `collector_exit_code`, `budget_status`) `[ĐÃ CHẠY XÁC MINH]`.

### Phase 4 — Chuỗi NAV & Ngữ nghĩa thời gian
- Bảo toàn tách biệt tuyệt đối giữa `data/nav.csv` (`series_type = "nav_history"`, ngày nguồn Fmarket dừng ở `2025-10-31`) và `data/nav_snapshots.csv` (`series_type = "nav_snapshot"`, ngày hệ thống quan sát) trên cả CSV, JSON dashboard và biểu đồ SVG `[ĐÃ CHẠY XÁC MINH]`.
- Gia cố `scripts/analyze_reaction.py` kiểm tra `validate_nav_snapshot` trước khi ghép có điều kiện cho báo cáo mô tả và dùng `sum(min_count=1)` để không biến giá trị thiếu thành `0` `[ĐÃ CHẠY XÁC MINH]`.

### Phase 5 — Kiểm định bất biến bộ máy `vnfa/diff.py`
- Khắc phục `sum(min_count=1)` trong `market_flow` (tránh biến tổng `est_value_vnd` toàn `NaN` thành `0.0` tỷ đồng) và khóa chặt 12 bất biến toán học trong `tests/test_core.py`: ngưỡng `≥ 4` vs `≤ 3` mã chung (`UNCONFIRMED?`), thiếu khối lượng (`WEIGHT_CHANGE?`), nhân đôi quy mô quỹ `2x` (`PRORATA`, dòng tiền chủ động = `0`), biến động giá thuần túy không đổi khối lượng (`0` sự kiện giao dịch), quyền đồng loạt `≥ 3` quỹ (`CORP_ACTION?`), kiểm duyệt `NEW_IN_TOP10` / `EXIT_TOP10`, khối lượng âm/bằng `0`, gộp mã trùng trong cùng snapshot, bất biến hoán vị thứ tự dòng, và bảo toàn dấu dòng tiền ròng `[ĐÃ CHẠY XÁC MINH]`.

### Phase 6 — Cổng tích hợp Quant (`BLOCKED` minh bạch)
- Không tồn tại `smart_money_quant.py` hoặc tệp dữ liệu thị trường Yuanta (`stocks_master.parquet`, `ohlcv_daily.parquet`, `foreign_room_daily.parquet`, `valuation_daily.parquet`) trong repository `[ĐÃ CHẠY XÁC MINH]`.
- Theo quyết định của chủ dự án (`defer_until_supplied`), dự án không tạo Smart Money Score giả, không tạo bảng xếp hạng hay khuyến nghị đầu tư; `scripts/export_holdings_long.py` đã được sửa lỗi ticker `"NA"` và gắn cờ `period_end_inferred = True`, sẵn sàng kết nối khi có dữ liệu PIT hợp lệ `[ĐÃ CHẠY XÁC MINH]`.

### Phase 7 — Sửa lỗi & Nâng cấp Dashboard nghiên cứu tĩnh
- Đã sửa lỗi `Compare` (`BUG-01`), hiển thị đầy đủ `industry`/`fees`/`profile` từ `fund_facts.json` (`1.467` dòng), hiển thị badge SLA độ mới và cảnh báo `stale`, hiển thị tỷ lệ thiếu kèm mẫu số (`2.251 / 2.251 (100%)`), chuẩn hóa dấu `+/-` (`−` Unicode) theo `vi-VN` và tôn trọng `prefers-reduced-motion` `[ĐÃ CHẠY XÁC MINH]`.

### Phase 8 — Gia cố GitHub Actions, `.gitignore`, Dependabot & Tài liệu
- Đã pin toàn bộ Actions trong `.github/workflows/daily.yml` và `.github/workflows/pages.yml` sang full 40-char commit SHA hỗ trợ Node 24, sửa lỗi shell quoting `$LIMIT`, phân tách quyền tối thiểu (`contents: read` mặc định), chỉ upload/deploy Pages trên nhánh chính (PR chỉ build & test), bổ sung `.github/dependabot.yml`, cập nhật `.gitignore`, `docs/DATA_INVENTORY_2026-10-27.{json,md}` và `README.md` `[ĐÃ CHẠY XÁC MINH]`.

---

## 5. Ma trận kiểm tra Definition of Done (DoD)

| Tiêu chí DoD | Kết quả kiểm chứng | Nhãn |
|---|---|---|
| Không có dữ liệu giả, fixture lẫn vào `data/`, hay suy diễn `missing -> 0` | `data/` chỉ chứa dữ liệu Fmarket thực tế đã lưu + bản nén/nâng cấp bảo toàn đối chiếu SHA-256; mọi phép cộng giá trị thiếu dùng `min_count=1` | `[ĐÃ CHẠY XÁC MINH]` |
| Tách bạch `effective_date`, `observed_date`, `observed_at`, `published_at`, `ingested_at` | `published_at` thiếu được giữ trống (`0%` suy diễn); `nav.csv` và `nav_snapshots.csv` tách riêng chuỗi | `[ĐÃ CHẠY XÁC MINH]` |
| Bảo toàn nhãn suy luận & kiểm duyệt (`BUY`/`SELL` suy luận, `UNCONFIRMED?`, `WEIGHT_CHANGE?`, `PRORATA`, `CORP_ACTION?`, `Top-N`) | Đầy đủ trong `vnfa/diff.py`, `vnfa/report.py`, `vnfa/dashboard_export.py` và `dashboard/app.mjs` | `[ĐÃ CHẠY XÁC MINH]` |
| 100% bài test chạy offline, độ phủ `vnfa/` `≥ 85%` | `69 passed` Python tests (`vnfa/` coverage **`86%`**) + `12 passed` Node UI tests; không phát sinh bất kỳ kết nối mạng nào | `[ĐÃ CHẠY XÁC MINH]` |
| Không lộ token, secret, raw body, hoặc đường dẫn nhạy cảm; `git diff --check` sạch | Đã kiểm tra bằng test `send_telegram` không log token, whitelist `workflow_status.json`, và `git diff --check` = `0` lỗi | `[ĐÃ CHẠY XÁC MINH]` |
| Dọn sạch các thư mục staging/backup tạm thời ngoài Git | `/tmp/vnfa_hat_ccq_stage`, `/tmp/vnfa_provenance_backup_20261027`, `/tmp/vnfa_source_runs_backup_20261027` đã xóa sạch | `[ĐÃ CHẠY XÁC MINH]` |

---

## 6. Các hạng mục đang chờ bên ngoài & Khuyến nghị bước tiếp theo

1. **Xác nhận điều khoản nguồn (`hat-ccq` & Fmarket):** Chỉ chạy `scripts/backfill_hat_ccq.py` vào `data/` chính thức sau khi chủ repo `huyhn-ba-po/hat-ccq` bổ sung tệp `LICENSE` hợp lệ và xác nhận điều khoản tái sử dụng dữ liệu từ Fmarket `[CHƯA XÁC MINH]`.
2. **Tích lũy chuỗi snapshot liên tiếp trên `main`:** Hiện tại `data/` mới có 1 mốc quan sát đầu tiên (`2026-10-27`), do đó `data/changes.csv` chưa phát sinh dòng sự kiện; sau kỳ cập nhật danh mục tháng tiếp theo của các quỹ trên Fmarket, `daily.yml` sẽ tự động sinh `data/changes.csv` và `data/workflow_status.json`, đồng thời kích hoạt `pages.yml` cập nhật GitHub Pages `[SUY LUẬN]`.
3. **Mở khóa Phase 6 (Quant):** Khi có module `smart_money_quant.py`, bộ parquet thị trường Yuanta và lịch sử danh mục đầy đủ (`import_full_holdings.py`) có kiểm chứng PIT, có thể nối trực tiếp với đầu ra của `scripts/export_holdings_long.py` `[CHƯA XÁC MINH]`.
