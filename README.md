🇻🇳 **Tiếng Việt** | 🇬🇧 [English](README.en.md)

# VN_Fund_Analysis

Theo dõi các quỹ mở có trên Fmarket (cổ phiếu, cân bằng, trái phiếu/MMF) bằng GitHub Actions; số quỹ thực tế được xác định khi chạy từ API. Trong checkout hiện tại (`2026-10-27`), `data/funds.csv` ghi nhận **69 quỹ** (`STOCK`: 34, `BALANCED`: 11, `BOND`: 24), kèm 46 tệp snapshot cổ phiếu, 33 tệp snapshot trái phiếu, lịch sử NAV cho 59/69 quỹ (`data/nav.csv`, 47.121 điểm đến `2025-10-31`) và snapshot NAV ngày quan sát cho 69/69 quỹ (`data/nav_snapshots.csv`).

Mỗi lần chạy thu top 10 cổ phiếu, top trái phiếu, cơ cấu tài sản, tỷ trọng ngành, biểu phí, hồ sơ quỹ và dữ liệu NAV có sẵn. Các bảng lịch sử holdings/factsheet chỉ ghi thêm khi nội dung đổi; riêng `nav_snapshots.csv` ghi một lần quan sát mỗi ngày, kể cả NAV không đổi. Sau đó hệ thống so sánh holdings giữa các snapshot và tạo báo cáo Markdown (tùy chọn gửi Telegram). Lịch sử NAV dài trong `research_observations.csv` được lưu dạng 1 dòng summary mỗi quỹ/lần tải (`nav_history` SHA-256 + phạm vi ngày + số điểm) thay vì nhân bản từng điểm ngày, giữ toàn bộ `data/` gọn nhẹ (~2,64 MB; kiểm soát tự động bằng `scripts/check_data_budget.py`).

Audit kiến trúc ban đầu: [`docs/AUDIT_2026-10-27.md`](docs/AUDIT_2026-10-27.md). Tranche 2 PIT/provenance: [`docs/TRANCHE_2_DATA_FOUNDATION_2026-10-27.md`](docs/TRANCHE_2_DATA_FOUNDATION_2026-10-27.md). Inventory thực tế: [`docs/DATA_INVENTORY_2026-10-27.md`](docs/DATA_INVENTORY_2026-10-27.md). Audit & báo cáo kiểm định độc lập (Phases 0–8): [`docs/AGENT_AUDIT_20261027.md`](docs/AGENT_AUDIT_20261027.md) và [`docs/AGENT_REPORT_20261027.md`](docs/AGENT_REPORT_20261027.md). Đây là pipeline nghiên cứu, chưa phải hệ thống tạo khuyến nghị hoặc backtest đã xác thực.

## Static research dashboard (GitHub Pages)

Bảng điều khiển nghiên cứu tĩnh triển khai từ `dashboard/` và hỗ trợ project-site path `/VN_Fund_Analysis/`. UI chạy bằng HTML/CSS/JavaScript thuần, không có dependency build phía browser và không gọi Fmarket/API có xác thực. Các view gồm tổng quan, quỹ, holdings/sự kiện danh mục, nghiên cứu mã, so sánh NAV cùng nhóm, data quality, pipeline health và methodology; bộ lọc, sắp xếp, phân trang, liên kết chi tiết và query URL có thể chia sẻ. Watchlist được lưu cục bộ trong browser. Có thể chọn `VI` / `EN` hoặc dùng `?lang=vi` / `?lang=en`; dữ liệu do nguồn cung cấp có thể vẫn hiển thị nguyên văn tiếng Việt nếu không có bản dịch được xác minh.

Exporter chỉ đọc `data/` hiện có và tạo `dashboard/data/` JSON có manifest schema-versioned:

```bash
pip install -r requirements.txt
python scripts/export_dashboard.py
python -m http.server 8000 --directory dashboard
```

Mở `http://localhost:8000/` để xem bản local. Khi `data/` không có hoặc dataset thiếu (như `changes.csv`, `holdings_full.csv`, `performance.csv` trong snapshot mốc đầu hiện tại), dashboard hiển thị trạng thái `missing`/`empty`/`unavailable` thay vì tạo mẫu.

Artifact export chỉ gồm latest holdings snapshot theo fund/source, NAV trong tối đa 5 năm gần nhất, tối đa 25.000 portfolio events trong cửa sổ 5 năm, tối đa 2.000 source-request records gần nhất, latest quality report theo dataset/fund (tối đa 10.000), factsheet block mới nhất (`allocation`, `industry`, `fees`, `profile`, `performance`), validation và metadata nguồn đã sanitize. CSV archive/lịch sử không bị sửa hoặc copy nguyên vẹn sang Pages; raw payload, run ID, credential và body API không được publish. `dashboard/data/` được gitignore và được build lại bởi workflow; hãy xem lại dữ liệu archive và quyền công bố trước khi bật Pages cho repository chứa dữ liệu nhạy cảm.

Để bật deployment: trong **Settings → Pages**, chọn **GitHub Actions** làm source. Sau đó workflow `.github/workflows/pages.yml` chạy test, kiểm tra ngân sách dung lượng `data/` (`check_data_budget.py`), chạy exporter và deploy khi có thay đổi trên `main`, sau khi workflow daily kết thúc trên nhánh mặc định (`workflow_run`), hoặc qua `workflow_dispatch`. PR chỉ build/test, không upload artifact Pages và không deploy. Pages phục vụ project-site ở `/VN_Fund_Analysis/`; URLs trong UI tương đối nên không phụ thuộc domain root. Build chỉ đọc file trong repository, không thu thập nguồn live và không cần user-defined secrets; bước deploy dùng quyền `GITHUB_TOKEN`/OIDC do GitHub Actions cấp. Mở **Actions → Static research dashboard** để xem build/deploy result.

Dashboard giữ rõ các giới hạn: Top-N không phải full portfolio; `UNCONFIRMED?` và `WEIGHT_CHANGE?` vẫn chưa xác nhận; top-N exit không chứng minh bán hết; `CORP_ACTION?` còn là heuristic. NAV snapshots là ngày quan sát, không tự xác lập effective date; publication time thiếu không được suy ra (UI hiển thị cả số lượng thiếu lẫn tỷ lệ trên mẫu sidecar). So sánh NAV chỉ dùng ngày nguồn trùng chính xác, cùng category, tối thiểu hai điểm; đổi nhóm tài sản trên UI tự động xóa lựa chọn cũ không tương thích để tránh giữ tham số URL lệch nhóm; metric đó không phải total return. Không có ranking, khuyến nghị, valuation/fundamentals hay giao dịch xác nhận. Freshness được đánh giá theo ngưỡng SLA cấu hình trong `config.toml` (`fund_holdings = 45`, `fund_bond_holdings = 45`, `allocation = 45`, `nav_snapshots = 3`, `fees = 180` ngày); các dataset không cấu hình giữ trạng thái `not_assessed_no_sla`. Trạng thái tổng thể của workflow chỉ hiển thị khi có `data/workflow_status.json` đã sanitize (ghi bởi `scripts/write_workflow_status.py`), ngược lại giữ `unknown_not_exported`.

## Cách phát hiện thay đổi vị thế

| Nhãn | Ý nghĩa |
|---|---|
| **Tăng / giảm vị thế (suy luận)** | Khối lượng đổi từ ngưỡng `min_volume_change_pct` (mặc định 5%) so với proxy quy mô quỹ. Proxy là trung vị thay đổi khối lượng của ít nhất 4 mã chung. Đây không phải dữ liệu lệnh hay bằng chứng về giao dịch cụ thể. |
| **Chưa xác nhận (`UNCONFIRMED?`)** | Có thay đổi khối lượng đáng kể nhưng dưới 4 mã chung đủ dữ liệu để ước lượng quy mô quỹ; không tính vào dòng tiền ròng. |
| **Tỷ trọng đổi (`WEIGHT_CHANGE?`)** | Chỉ có tỷ trọng mà thiếu khối lượng; chưa thể tách tác động của giá, dòng tiền và corporate actions nên không gán MUA/BÁN. |
| **Đổi đồng loạt (`PRORATA`)** | Thay đổi khối lượng gần với proxy quy mô quỹ; không xem là quan điểm đầu tư, không tính vào vị thế chủ động. |
| **Nghi do quyền (`CORP_ACTION?`)** | Heuristic: từ 3 quỹ trở lên cùng đổi khối lượng một mã gần nhau trong `lookback_days` ngày. Chưa được xác nhận bằng lịch quyền chính thức. |
| **Vào / Rời top 10** | Mã xuất hiện/biến mất khỏi top 10. Danh mục bị kiểm duyệt: không đồng nghĩa mua mới hoặc bán hết. |

Giá trị ước tính = phần khối lượng còn lại sau điều chỉnh proxy quy mô × giá ngầm định (giá trị tài sản / khối lượng). Đây là ước tính ở giá kỳ báo cáo, không phải giá giao dịch; giá trị thiếu được hiển thị là chưa có dữ liệu, không thay bằng 0.

## Quét toàn bộ cổ phiếu 3 sàn (HOSE, HNX, UPCOM) & 5 trang nghiên cứu mã (Yuanta YSradar)

Ngoài dữ liệu quỹ mở Fmarket, dự án tích hợp bộ quét toàn bộ cổ phiếu trên **3 sàn (`HOSE`/`HSX`, `HNX`, `UPCOM`)** theo cấu trúc 5 trang dữ liệu của Yuanta YSradar (mẫu tham chiếu `ACB`), lưu trữ Parquet lake ngoài Git (`data/yuanta/`) và xuất gói tĩnh nén gzip theo từng mã (`dashboard/data/yuanta/stocks/{SYMBOL}.json.gz`) phục vụ trực tiếp trên mục **Nghiên cứu mã** của dashboard:

1. **Trang 1 — Tổng quan (`overview`)**: `stock_rating/info`, `profile/info`, `profile/shareholder`, `price_board/stock/stock_info`, `last_bid_offer`, `list_transaction`, `list_stock_info`, `list_stock_rating_info`, `profile/peers`.
2. **Trang 2 — Dữ liệu giao dịch (`trading`)**: `price_history` (OHLCV + khối ngoại khớp lệnh từ `2010-01-01` đến nay), `price_history_foreign` (room ngoại & sở hữu NN từ `2025-01-01`), và 3 biểu đồ `foreign_screener` (`ngay_giao_dich`, `gtgd_binh_quan_chart`, `ty_le_von_hoa_chart`).
3. **Trang 3 — Tương quan trong ngành (`correlation`)**: Tính toán trung vị và bách phân vị tương quan từ dữ liệu BCTC Trang 4 theo **3 khối ngành lớn** (`Ngân hàng`, `Tài chính phi ngân hàng`, `Phi tài chính`), trong đó **tách riêng các phân ngành đặc thù như `Bất động sản` (`REAL_ESTATE`) ra khỏi nhánh Tài chính L1** để không gộp chung với Ngân hàng/Chứng khoán/Bảo hiểm. Hỗ trợ radar 5 trục bách phân vị và biểu đồ so sánh đa thang đo (mã vs trung vị ngành con vs trung vị khối).
4. **Trang 4 — Báo cáo tài chính (`financials`)**: Phân trang toàn bộ `CSTC` (Chỉ số tài chính) và `BCTT` (Báo cáo tóm tắt) theo quý từ **Q1/2010 đến nay** (`report_term_type=2`, `unit=1000000`), kèm bộ 4 biểu đồ BCTC chuyên biệt cho từng khối ngành (`Ngân hàng`, `Tài chính phi ngân hàng`, `Phi tài chính`).
5. **Trang 5 — Tin tức & sự kiện (`events`)**: Toàn bộ bài viết, tin doanh nghiệp, lịch cổ tức & ĐHĐCĐ từ `2010-01-01` đến nay (`market_data/article`), có tìm kiếm và bộ lọc nhóm sự kiện.

Chạy quét local (hỗ trợ resume tự động qua checkpoint `.json.gz`, incremental refresh, chọn sàn/mã/module):

```bash
# Quét thử nghiệm 3 mã đại diện cho 3 khối ngành / tách BĐS (Ngân hàng, Chứng khoán, Bất động sản)
python scripts/scan_yuanta_universe.py --tickers ACB,SSI,VHM --workers 3

# Quét toàn bộ 3 sàn HOSE + HNX + UPCOM (mặc định Q1/2010 -> nay, tự resume nếu ngắt giữa chừng)
python scripts/scan_yuanta_universe.py --exchanges HOSE,HNX,UPCOM --workers 6

# Cập nhật incremental nhanh (chỉ kéo phần giá/kỳ BCTC mới phát sinh từ checkpoint gần nhất)
python scripts/scan_yuanta_universe.py --incremental --workers 6

# Chỉ dựng lại Parquet lake + tương quan ngành Trang 3 + gói .json.gz từ checkpoints có sẵn (offline 100%)
python scripts/scan_yuanta_universe.py --rebuild-static-only
```

Đầu ra được tách bạch để không làm phình Git repository:
- `data/yuanta/` *(gitignored)*: `checkpoints/{SYMBOL}.json.gz`, 7 bảng Parquet hợp nhất (`stocks_master.parquet`, `ohlcv_daily.parquet`, `foreign_room_daily.parquet`, `financials_cstc.parquet`, `financials_bctt.parquet`, `peer_correlations.parquet`, `articles_events.parquet`), `source_runs_yuanta.csv` (chỉ lưu hash/status, không lưu raw body/token), và `scan_manifest.json`.
- `dashboard/data/yuanta/` *(gitignored local, đóng gói khi deploy Pages)*: `universe.json`, `sector_medians.json`, và `stocks/{SYMBOL}.json.gz` (trình duyệt giải nén trực tiếp qua `DecompressionStream('gzip')`, tự fallback sang `.json` nếu cần; `NaN`/`Inf` luôn chuyển thành `null`, không bao giờ ép về `0`).
- Tự động hóa trên GitHub Actions: `.github/workflows/yuanta_scan.yml` hỗ trợ `workflow_dispatch` (tùy chọn sàn, danh sách mã, số luồng, `incremental`) và lịch chạy định kỳ hằng tuần, lưu cache checkpoints + upload Parquet artifact mà không commit file lớn vào Git.

## Cấu trúc

```
VN_Fund_Analysis/
├── .github/workflows/daily.yml       # chạy 08:30 giờ VN hằng ngày + chạy tay (Fmarket)
├── .github/workflows/yuanta_scan.yml # quét 3 sàn HOSE/HNX/UPCOM (Trang 1–5) + Parquet artifact + Pages
├── .github/workflows/pages.yml       # test, export JSON tĩnh và deploy GitHub Pages
├── dashboard/                        # UI nghiên cứu static (app.mjs + yuanta_view.mjs); dashboard/data/ được build
├── config.toml                       # ngưỡng, loại quỹ, watchlist
├── vnfa/
│   ├── fmarket.py                    # client + parser Fmarket, source-event metadata
│   ├── yuanta.py                     # client + parser Yuanta 3 sàn (Trang 1–5), phân nhóm 3 khối ngành (tách BĐS)
│   ├── store.py                   # legacy CSV append-only (sep=';'; schema giữ nguyên)
│   ├── quality.py                 # validators, hard errors và warnings
│   ├── temporal.py                # cutoff Point-In-Time, lọc late/future/unknown
│   ├── provenance.py              # source/observation/quality sidecar append-only
│   ├── diagnostics.py             # chẩn đoán nguồn có phân loại kết quả
│   ├── inventory.py               # kiểm kê dữ liệu có thật trên đĩa
│   ├── dashboard_export.py        # projection JSON compact, whitelist/sanitize; không gọi API
│   ├── diff.py                    # so sánh thay đổi vị thế suy luận, PRORATA, cờ quyền
│   ├── pipeline.py                # ghi snapshot + so sánh (dùng chung)
│   ├── report.py / notify.py      # báo cáo Markdown, Telegram
│   └── config.py
├── scripts/
│   ├── run_daily.py               # lệnh chạy hằng ngày (mọi quỹ, mọi bảng)
│   ├── diagnose_sources.py        # probe Fmarket, phân loại auth/network/schema/rate-limit/empty
│   ├── data_inventory.py          # inventory JSON/Markdown + SLA freshness dựa trên CSV hiện có
│   ├── export_dashboard.py        # tạo dashboard/data/ từ archive hiện có (không sửa CSV, giữ nguyên yuanta/)
│   ├── scan_yuanta_universe.py    # quét toàn bộ cổ phiếu HOSE/HNX/UPCOM (Trang 1–5), xuất Parquet & .json.gz tĩnh
│   ├── compact_provenance.py      # nén nav_history point-rows thành per-fund summary (có --dry-run + backup)
│   ├── migrate_source_runs.py     # nâng cấp source_runs.csv v1 -> v2 (thêm fund_code, giữ unknown lịch sử)
│   ├── check_data_budget.py       # kiểm tra ngưỡng dung lượng data/ & dự phóng 90 ngày
│   ├── write_workflow_status.py   # ghi data/workflow_status.json đã sanitize (không chứa secret/log)
│   ├── analyze_reaction.py        # phản ứng của quỹ trước một mốc thị trường
│   ├── import_full_holdings.py    # nhập danh mục ĐẦY ĐỦ từ báo cáo chính thức (vd. DCDS)
│   ├── backfill_hat_ccq.py        # nạp lịch sử mirror vào staging/data (có --dry-run, --max-commits)
│   └── export_holdings_long.py    # xuất bảng cho smart_money_quant.py (chỉ khi module ngoài được cung cấp)
├── tests/
│   ├── test_core.py               # unit & invariant tests cho store, fmarket, diff
│   ├── test_foundation.py         # tests cho quality, temporal PIT, provenance, inventory, scripts
│   ├── test_dashboard_export.py   # tests cho static dashboard exporter, workflow_status & SLA
│   ├── test_yuanta.py             # offline tests cho Yuanta Pages 1–5, tách ngành BĐS, Parquet & .json.gz
│   └── test_dashboard_ui.mjs      # offline Node test suite cho UI state/filter/compare/Yuanta 5-tab view
├── data/                          # tự sinh:
│   ├── holdings/{QUY}.csv         #   top 10 cổ phiếu mỗi kỳ công bố
│   ├── bonds/{QUY}.csv            #   top trái phiếu mỗi kỳ công bố
│   ├── nav_snapshots.csv          #   NAV/CCQ theo ngày quan sát (fund;date;nav), kể cả khi NAV không đổi
│   ├── nav.csv                    #   lịch sử NAV dài (tải khi quỹ chưa có chuỗi; kiểm tra độ phủ trước khi dùng)
│   ├── allocation.csv             #   cổ phiếu/trái phiếu/tiền theo kỳ báo cáo
│   ├── industry.csv, fees.csv, profile.csv
│   ├── changes.csv                #   nhật ký quan sát/thay đổi vị thế suy luận, có cờ chưa xác nhận
│   ├── source_runs.csv            #   mỗi request: thời gian, outcome, HTTP, response hash (không lưu body)
│   ├── research_observations.csv  #   effective/observed/published/ingested; revision append-only
│   ├── quality_reports.csv        #   findings, severity, coverage metrics theo dataset/quỹ
│   └── funds.csv, last_run.json
└── reports/                       # tự sinh: latest.md, YYYY-MM-DD.md, source/inventory reports
```

## Triển khai (khoảng 10 phút)

1. Tạo repo **VN_Fund_Analysis** trên GitHub, copy các file vào đúng thư mục rồi push.
2. **Settings → Actions → General → Workflow permissions** chọn *Read and write permissions* (để workflow commit được dữ liệu).
3. Trước khi ingest thật, kiểm tra terms/rate limits của Fmarket. Nếu được phép, chạy `python scripts/diagnose_sources.py --skip-nav` từ môi trường có mạng; đây là diagnostic read-only nhưng vẫn tạo request tới nguồn. Sau đó chạy **Actions → Daily fund holdings check → Run workflow**, nhập `limit = 3` và bật `dry_run`.
   **Kết nối/schema live chưa được xác minh trong checkout này**; unit tests dùng fixture, không thay thế kiểm tra nguồn. Nếu trả 403/429, network error, schema error hoặc empty response, đọc `source_diagnostic.json`/`source_runs.csv` và mục Xử lý lỗi trước khi chạy toàn bộ.
4. Chạy lại không `dry_run` và không `limit` để ghi mốc đầu tiên cho mọi quỹ.
5. (Tùy chọn) Nạp lịch sử 5 tháng gần nhất, chạy trên máy cần có `git`:
   ```bash
   pip install -r requirements.txt
   python scripts/backfill_hat_ccq.py
   git add data && git commit -m "data: backfill" && git push
   ```
   Chạy backfill **trước** lần chạy thật đầu tiên ở bước 4 để mốc đầu của bạn nối tiếp lịch sử. Ngày commit mirror chỉ là xấp xỉ, không phải publication time. Nếu chỉ muốn thêm lịch sử nghiên cứu và đảm bảo không sửa bất kỳ CSV legacy nào (kể cả NAV), chạy `python scripts/backfill_hat_ccq.py --provenance-only`; lệnh chỉ append provenance/quality sidecars, và các ngày mirror vẫn bị loại khỏi PIT mặc định.
6. (Tùy chọn) Telegram: thêm secrets `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` ở **Settings → Secrets and variables → Actions**.

Chạy trên máy: `python scripts/run_daily.py --dry-run --limit 3 --lang en`, `pytest -q` để chạy test. Báo cáo hỗ trợ `--lang vi|en|both` (mặc định theo `[report].lang`); `both` ghi thêm `latest.en.md` và báo cáo ngày `.en.md`. Phân tích phản ứng hỗ trợ `python scripts/analyze_reaction.py --lang en`.

## Đọc kết quả

- `reports/latest.md`: báo cáo lần chạy gần nhất (thay đổi vị thế ước tính, watchlist, cảnh báo và chi tiết từng quỹ).
- `data/changes.csv`: nhật ký mọi thay đổi (dùng để phân tích tiếp).
- `data/holdings/{QUY}.csv`: mỗi kỳ công bố một khối dòng. `observed_date` là trường ngày legacy, không có giờ; xem hướng dẫn `observed_date_basis` ở mục Point-In-Time trước khi dùng trong kiểm định lịch sử. Timestamp chính xác nằm ở sidecar, không sửa schema holdings cũ.

Liên kết với `smart_money_quant.py`: `python scripts/export_holdings_long.py` tạo `exports/holdings_long.csv` đúng schema `holdings` của module đó (`period_end` do suy ra, nên gắn cờ `period_end_inferred`).

## Inventory, validation, provenance và Point-In-Time

Tạo inventory thực tế, không ước lượng số quỹ/rows còn thiếu:

```bash
python scripts/data_inventory.py --reference-date 2026-10-27 --lang vi
```

Lệnh ghi `reports/data_inventory.json` và `.md`; dùng `--stdout-only` để chỉ xem. Báo cáo liệt kê files/schema/row counts, fund/date coverage, quality findings, source events đã lưu và các bước tiếp theo. `missing` không có nghĩa là zero hoặc nguồn rỗng. Snapshot được tính theo ngày tham chiếu; tuổi dữ liệu được hiển thị nhưng không tự gán stale nếu chưa có SLA.

Chạy source diagnostic khi môi trường của bạn được phép truy cập Fmarket:

```bash
python scripts/diagnose_sources.py [--skip-nav] [--delay 1.0]
```

Probe có rate limit theo config và kiểm tra catalogue, một chi tiết quỹ, cùng NAV history (trừ khi `--skip-nav`). Kết quả phân loại `authentication_error`, `rate_limit`, `network_error`, `schema_error`, `empty_response` hoặc `success`; lệnh ghi JSON và `data/source_runs.csv` nhưng không lưu response body hay credential. Diagnostic **không** xác nhận điều khoản sử dụng, quyền tái sử dụng dữ liệu, tính đầy đủ hay tính đúng của số liệu. Checkout này chưa có kết quả live Fmarket; cần chạy từ môi trường có kết nối và kiểm tra terms riêng.

Các bảng CSV hiện hữu giữ schema. Metadata mới nằm ở sidecar `research_observations.csv` và `quality_reports.csv`:

- `effective_date` = kỳ/ngày kinh tế do nguồn nêu; không xác lập khả năng quan sát.
- `observed_at` = thời điểm collector nhận response thực tế (UTC); `published_at` chỉ được ghi khi nguồn cung cấp/xác minh; `ingested_at` = lúc ledger ghi nhận. Thời gian thiếu để trống.
- `data_quality_status` là kết quả kiểm tra cấu trúc/giá trị; `verification_status` riêng biệt đánh dấu nguồn provider/mirror/URL được ghi nhận nhưng chưa được kiểm chứng độc lập. Không có record nào hiện được gán là verified chỉ vì qua schema checks.
- `select_as_of(records, cutoff, key_columns=...)` trong `vnfa.temporal` chỉ giữ bản ghi có observed time và publication delay đã qua cutoff; dùng bản revision mới nhất còn khả dụng. Bản ghi chỉ có `effective_date`, chỉ có `published_at`, timestamp tương lai, hoặc thời gian không biết bị loại. Ngày hiệu lực trong tương lai cũng bị loại.
- `observed_date` date-only chỉ được PIT khi `observed_date_basis` được đánh dấu `verified_date_only`/`source_observed_date`/`date_only`; ngày không tag bị loại mặc định. Backfill mirror có basis `approximate`, luôn bị loại khỏi PIT mặc định. `allow_legacy_date_only=True` là opt-in rõ ràng cho người kiểm tra thủ công, không nên dùng cho kiểm định.
- Cutoff dạng `YYYY-MM-DD` là cuối ngày Việt Nam; cutoff timestamp phải có timezone. Ví dụ:

```python
from vnfa.provenance import ProvenanceStore
from vnfa.temporal import select_as_of

observations = ProvenanceStore("data", write=False).read_observations()
asof = select_as_of(
    observations, "2026-10-27",
    key_columns=["dataset", "fund", "record_key"],
)
```

`import_full_holdings.py` chấp nhận thêm `observed_at`, `published_at`, `observed_date`, `observed_date_basis`; timestamp phải kèm timezone. Khi sửa dữ liệu cùng kỳ, `holdings_full.csv` vẫn là latest materialized view, còn các phiên bản trước/sau được giữ trong sidecar theo record hash và source reference. Timestamp không được suy ra từ `period_end` hay ngày chạy. `backfill_hat_ccq.py` đánh dấu ngày commit chỉ là xấp xỉ; dữ liệu mirror không tự động được coi PIT-safe.

## Phản ứng của quỹ trước một mốc thị trường

```bash
python scripts/analyze_reaction.py --peak 2026-05-07
```
Báo cáo `reports/reaction_<mốc>.md` gồm: NAV từ mốc theo loại quỹ, dịch chuyển cơ cấu tài sản khi hai kỳ có đủ trường, thay đổi vị thế cổ phiếu ước tính theo tháng và các mã có thay đổi lớn nhất. Ngày của snapshot NAV là ngày quan sát; chỉ dùng chuỗi có điểm quan sát mới trong `--fresh-days` ngày. Không có benchmark hoặc bằng chứng về lệnh giao dịch trong báo cáo này.

## Nguồn ngoài Fmarket (quỹ không có trên Fmarket, danh mục đầy đủ)

- Trong `data/profile.csv` hiện tại (69 dòng), website công ty quản lý nằm ở cột `website` (ví dụ `https://abf.com.vn/`, `vfm.com.vn`, `vcbf.com`, `wm.vinacapital.com`, `ssiam.com.vn`).
- **Không tự động hóa nguồn ngoài khi chưa kiểm tra điều khoản.** Nhiều trang tổng hợp (ví dụ Smoney) cấm sao chép/tái sử dụng dữ liệu nếu không có văn bản đồng ý, nên không có scraper cho các trang đó trong dự án.
- Với trang của công ty quản lý quỹ: kiểm tra `robots.txt` và điều khoản, rồi tải báo cáo tháng (PDF/HTML), trích bảng danh mục vào CSV và dùng `scripts/import_full_holdings.py` (bắt buộc ghi URL `http(s)` vào `source_url`). Nếu tỷ trọng theo % nhưng tất cả giá trị ≤ 1, phải chỉ rõ `--weight-unit percent`; `auto` không thể suy ra đơn vị trong trường hợp đó. Cách này cũng áp dụng cho DCDS (không có trên Fmarket).

## Giới hạn cần nhớ

- Chỉ có **top 10** mỗi quỹ (cổ phiếu) và vài trái phiếu lớn nhất. Quỹ MMF không có danh mục chi tiết trên Fmarket, nên chỉ có NAV, phí, hồ sơ.
- `report_date` của cơ cấu tài sản là ngày theo giờ Việt Nam của `reportTime` (nửa đêm 01/09 ứng với kỳ báo cáo tháng 8): hãy hiểu là cuối kỳ trước đó.
- Kiểm chứng trên `data/nav.csv` hiện tại xác nhận lịch sử NAV trên endpoint `POST /product/get-nav-history` của Fmarket đang dừng ở `2025-10-31` (59/69 quỹ có chuỗi, 47.121 điểm; 10 quỹ trả về `empty_response`: `ABEF`, `AEIF`, `DCBA`, `KSIF`, `LHFCF`, `LPLF`, `SSIPDF`, `UMMF`, `USIF`, `VBIF`). `nav.csv` và `nav_snapshots.csv` được giữ là hai chuỗi tách biệt; `analyze_reaction.py` chỉ ghép có kiểm soát cho báo cáo mô tả khi vượt qua kiểm tra `validate_nav_snapshot` và gắn nhãn rõ khoảng trống chuỗi nguồn đến ngày quan sát.
- Thiếu danh mục đầy đủ nên không biết quỹ có bán hết một mã hay không.
- `observed_date` là ngày phát hiện, không phải ngày quỹ giao dịch. Quỹ giao dịch trong tháng nhưng danh mục chỉ cập nhật theo kỳ.
- Tác giả README trước đây ghi nhận chênh khoảng 0,1 điểm % ở VCBF-BCF; không có snapshot/factsheet kèm trong checkout để tái xác minh. Đối chiếu số liệu chính thức trước khi ra quyết định.
- Ngưỡng 5% và mức gắn cờ quyền là tham số kinh nghiệm, chưa được hiệu chỉnh với dữ liệu dài. Đọc `changes.csv` vài tuần rồi chỉnh `config.toml`.
- Scheduled workflow của GitHub có thể trễ vài phút, và bị tắt nếu repo không có hoạt động 60 ngày. Kiểm tra tab Actions định kỳ.

## Xử lý lỗi

| Hiện tượng | Cách xử lý |
|---|---|
| `HTTP 403/429` | Tăng `delay_seconds` trong `config.toml`; kiểm tra điều khoản sử dụng của Fmarket; nếu bị chặn, chuyển sang thư viện `vnstock` (lớp `Fund`) thay cho `FmarketClient` |
| Danh sách quỹ rỗng | Cấu trúc API đã đổi: in `client.list_products()` và sửa `parse_product_row` |
| Workflow đỏ dù đã chạy | Có hơn 30% quỹ lỗi hoặc không lấy được danh sách: xem log từng quỹ |
| Push bị từ chối | Kiểm tra quyền *Read and write* ở bước 2 |

## Nguồn dữ liệu và giấy phép

- Dữ liệu: API công khai của Fmarket (không có tài liệu chính thức). Dùng cho nghiên cứu cá nhân, giữ tần suất thấp, và tự kiểm tra điều khoản sử dụng trước khi chia sẻ hoặc tái phân phối dữ liệu.
- Script `scripts/backfill_hat_ccq.py` hỗ trợ đọc lịch sử git của [huyhn-ba-po/hat-ccq](https://github.com/huyhn-ba-po/hat-ccq) vào thư mục staging (`--data-dir` / `--dry-run` / `--max-commits`). Lưu ý pháp lý: README của `hat-ccq` dẫn liên kết `[MIT](LICENSE)` nhưng repository gốc không có tệp `LICENSE` (`HTTP 404` trên `/license`), đồng thời quyền tái phân phối dữ liệu Fmarket chưa được xác minh độc lập; theo quyết định quản trị dự án hiện tại, dữ liệu mirror **không** được publish vào `data/` của nhánh chính cho tới khi xác nhận điều khoản. Không có mã nguồn nào của repo đó được sao chép vào dự án này.
- Đây là công cụ nghiên cứu, không phải khuyến nghị đầu tư.

Trạng thái collector/ngân sách ghi trong job không chứng minh kết quả workflow cuối cùng. Dashboard chỉ hiển thị kết quả hoàn tất lịch sử khi nhận event hoàn tất từ GitHub hoặc bản ghi API đã xác minh; thời điểm GitHub cập nhật và thời điểm quan sát được giữ riêng, không chứng minh dữ liệu hiện còn mới.

### Kiểm tra bổ sung NAV lịch sử

Khi `[collect].nav_refresh_existing = true` (mặc định), daily thử tải tiếp lịch sử NAV của quỹ đã có dữ liệu từ **ngày hiệu lực cuối cùng đã lưu, bao gồm ngày đó**, đến ngày chạy. Dữ liệu phải qua validator; bản ghi ngoài khoảng yêu cầu bị loại, và phản hồi rỗng/lỗi không xóa lịch sử cũ. Snapshot NAV theo ngày quan sát không được ghép vào chuỗi lịch sử. Có thể tắt bằng `nav_refresh_existing = false`. Nguồn có thể trả dữ liệu cũ, không đầy đủ hoặc bỏ qua bộ lọc ngày; chạy thành công không chứng minh dữ liệu đã cập nhật.

Dòng log `NAV history` và `data/last_run.json` (khi không dry-run) ghi riêng số phản hồi hợp lệ kể cả rỗng (`n_nav_funds`), số rỗng, số không có dòng trong khoảng, số lỗi, dòng được chấp nhận, dòng bị loại ngoài khoảng, ngày hiệu lực lớn nhất được chấp nhận và số dòng thêm/đổi. Ngày lớn nhất chỉ là cực đại của những dòng được chấp nhận, **không** chứng minh mọi quỹ cập nhật tới ngày đó. Test live có thể dùng workflow daily với `limit=2`, `dry_run=true` trước khi chạy toàn bộ trên main; dry-run không ghi archive; `write_enabled=False` và số dòng thêm/đổi chỉ là dự kiến. Không chia sẻ token, query string hoặc payload nguồn khi gửi log.

**Yuanta:** `scanned_ok` hiện chỉ cho biết tác vụ mã hoàn tất, không chứng minh mọi endpoint thành công. Chế độ incremental/module-select có rủi ro ghi đè phần checkpoint cũ khi endpoint trả rỗng/lỗi hoặc module không được chọn; chưa nên coi là refresh an toàn cho archive duy nhất. Giữ bản sao cache/artifact trước khi kiểm thử và đối chiếu diagnostics từng mã; chưa suy diễn độ đầy đủ từ workflow xanh.
