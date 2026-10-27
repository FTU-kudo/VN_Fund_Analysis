# Tranche 2 — Nền dữ liệu nghiên cứu, kiểm kê và Point-In-Time

**Ngày kiểm kê:** 2026-10-27 (Asia/Ho_Chi_Minh)
**Phạm vi:** lớp chất lượng dữ liệu, provenance, cutoff PIT, source diagnostics và inventory. Không xây Smart Money Score, không kết luận alpha, không đưa khuyến nghị đầu tư.

## 1. Kết quả inventory thực tế

Báo cáo được sinh từ đúng thư mục `data/` trong checkout, không thêm fixture hay ước lượng số liệu:

- Thư mục `data/` **không tồn tại** tại thời điểm kiểm kê.
- 15 dataset path được nhận diện theo schema dự án: **0 available, 15 missing**.
- **0** CSV, **0** rows, **0** quỹ được quan sát trong bảng dữ liệu.
- Không có `source_runs.csv`; số request/source diagnostic đã lưu là **0**. Connectivity live không được xác nhận.
- Không có lỗi parse/schema vì không có file để đọc; đây không phải bằng chứng dữ liệu nguồn rỗng.

Artifacts kiểm kê: [`DATA_INVENTORY_2026-10-27.md`](DATA_INVENTORY_2026-10-27.md) và [`DATA_INVENTORY_2026-10-27.json`](DATA_INVENTORY_2026-10-27.json). Tạo lại bằng `python scripts/data_inventory.py --data-dir data --reference-date 2026-10-27`.

## 2. Thành phần đã thêm

| Thành phần | Kết quả |
|---|---|
| `vnfa/quality.py` | `QualityReport`/`QualityFinding`, hard errors vs warnings; validators cho universe, holdings, NAV, allocation; missing không thành zero. Top-N được gắn cờ censored; weight lạ được cảnh báo thay vì tự loại nếu chưa vi phạm quy tắc cứng. |
| `vnfa/provenance.py` | Sidecar CSV phân tách source request, observation revisions và quality reports; `effective_date`, `observed_at`, `published_at`, `ingested_at`, record hash, source reference, coverage, data-quality và verification status. Semicolon separator giữ cùng quy ước kho hiện tại. |
| `vnfa/temporal.py` | `add_availability()`/`select_as_of()` lọc theo thông tin khả dụng và trả số lượng eligible/excluded. Bản sửa mới nhất theo natural key chỉ được chọn nếu đã khả dụng tại cutoff. |
| `vnfa/diagnostics.py`, `scripts/diagnose_sources.py` | Probe catalogue, một detail và tùy chọn NAV history; outcome phân biệt auth/permission, rate limit, network, schema, empty response và success; body không lưu. |
| `vnfa/inventory.py`, `scripts/data_inventory.py` | Inventory JSON/Markdown từ files hiện có: schema, row/fund counts, date ranges/age, quality, source attempts, provenance coverage và next steps. |
| Ingestion/import | Daily collector ghi source events và normalized observations có response receipt time; official holdings importer giữ phiên bản cũ/mới trong ledger; historical mirror backfill được đánh dấu ngày quan sát xấp xỉ. CSV schema legacy không bị thay đổi. |

## 3. Quy tắc thời gian PIT và revision

- `effective_date` là ngày hiệu lực/kỳ báo cáo, **không** chứng minh bản ghi đã được công bố hoặc quan sát.
- `observed_at` do adapter ghi tại lúc nhận response. `published_at` chỉ nhận giá trị nguồn thực sự cung cấp/xác minh; unknown để trống. Nếu cả hai có giá trị, availability là thời điểm muộn hơn. Chỉ có `published_at` mà thiếu `observed_at` vẫn bị loại vì chưa biết hệ thống có thể quan sát khi nào.
- `ingested_at` được ghi khi append sidecar; không thay thế publication/observation time.
- Timestamp quan sát sau cutoff bị loại; ngày hiệu lực ở tương lai bị loại. Bản sửa sau cutoff không thay bản cũ trong universe lịch sử; bản mới chỉ được chọn ở cutoff đủ muộn.
- Date-only `observed_date` chỉ có thể vào PIT khi được gắn basis đã xác minh (`verified_date_only`, `source_observed_date`, `date_only`) và được coi là khả dụng vào cuối ngày Việt Nam. Untagged legacy date bị loại mặc định; `allow_legacy_date_only=True` là opt-in có cảnh báo. Mirror commit date được gắn `approximate` và không PIT-safe.
- Cutoff `YYYY-MM-DD` là cuối ngày Asia/Ho_Chi_Minh. Timestamp cutoff bắt buộc có timezone.
- Lặp lại cùng nội dung từ cùng source không tạo thêm observation revision; source request vẫn có event riêng. Payload đổi tạo record hash/observation ID khác và ledger giữ cả phiên bản cũ.

`verification_status` tách riêng khỏi validation cấu trúc: Fmarket/mirror/URL được ghi nhận vẫn ở trạng thái chưa kiểm chứng độc lập. Không gán “verified” chỉ vì qua validator.

## 4. Tính không phá hủy và giới hạn tương thích

- `data/holdings/*.csv`, `changes.csv`, NAV và bảng factsheet hiện tại không được thêm cột âm thầm. Metadata mới ghi ở `source_runs.csv`, `research_observations.csv`, `quality_reports.csv`.
- Importer vẫn giữ `holdings_full.csv` như latest materialized view; sidecar append trước khi cập nhật view để có thể truy vết revision. Nếu schema target có cột mới không tương thích, importer dừng thay vì migrate ngầm.
- Backfill từ `hat-ccq` chỉ dùng ngày Git commit làm **xấp xỉ**, không biến thành `observed_at` hoặc publication time. PIT filter mặc định loại dữ liệu này. Backfill tự tránh ghi snapshot holdings/factsheet ngược thời gian vào CSV legacy; `--provenance-only` chỉ append provenance/quality sidecars và được test không sửa các CSV legacy. Merge NAV snapshots giữ nguyên các dòng/cặp khóa đã có.
- CSV sidecar có kiểm tra header chính xác khi append; khác schema thì báo lỗi và không tự viết lại file.

## 5. Kết quả kiểm thử / source connectivity

- Baseline trước khi thêm Tranche 2: **19 passed**.
- Sau các thay đổi Tranche 2 và backfill: **40 passed**. Tests dùng fixtures/mocks cho missing values, unusual values, schema errors, late publication, future observation/effective date, revision cutoffs, unknown timestamps, date-only basis, sidecar idempotence, importer revisions, daily-ingestion provenance, inventory trên data fixture trong `tmp_path`, source diagnostic classification, NAV snapshot merge, và backfill auto-skip/provenance-only không sửa CSV legacy.
- `compileall`, CLI `--help` của backfill/inventory/diagnostic và `git diff --check` đều hoàn tất thành công. Không chạy live-source request.
- **Không gọi live Fmarket** trong checkout này; môi trường phát triển chỉ cho phép outbound tới một tập host giới hạn, không gồm `api.fmarket.vn`. Vì vậy không có live schema, terms, auth, rate-limit hay connectivity result được tuyên bố. `scripts/diagnose_sources.py` cần được chạy từ môi trường được phép và sau khi kiểm tra điều khoản/rate limits.
- GitHub Actions cũng chưa được chạy trong tranche này.

## 6. Rủi ro còn lại và bước tiếp theo

1. **P0 — Lưu/khôi phục data archive thật** theo chính sách lưu trữ dự án; không điền dữ liệu giả vào checkout hiện tại.
2. **P1 — Xác minh Fmarket có điều khoản/rate limit phù hợp**, chạy diagnostic giới hạn, lưu outcome và đối chiếu một số mẫu với factsheet chính thức. Kiểm tra API response schema lại vì endpoint không có tài liệu đã xác minh.
3. **P1 — Tích lũy PIT thật:** response receipt timestamp, nguồn publication timestamp nếu có, source URL/hash, observed/effective date semantics. Giữ unknown là unknown.
4. **P2 — Mở rộng coverage có quyền sử dụng rõ:** danh mục đầy đủ, AUM/flows, benchmark/phí, hành động doanh nghiệp và NAV/giá phù hợp thời điểm; ghi rõ censoring, freshness và nguồn.
5. **P3 — Chỉ cân nhắc score/backtest sau khi đủ lịch sử PIT và đánh giá out-of-sample.** Tranche này không tạo Smart Money Score, không tuyên bố alpha và không kết luận quỹ/cổ phiếu hấp dẫn.
