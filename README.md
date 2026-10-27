# VN_Fund_Analysis

Theo dõi các quỹ mở có trên Fmarket (cổ phiếu, cân bằng, trái phiếu/MMF) bằng GitHub Actions; số quỹ thực tế được xác định khi chạy từ API. README trước đây nêu 69 quỹ vào 25/10/2026, nhưng con số đó chưa được xác minh lại trong checkout hiện tại.

Mỗi lần chạy thu top 10 cổ phiếu, top trái phiếu, cơ cấu tài sản, tỷ trọng ngành, biểu phí, hồ sơ quỹ và dữ liệu NAV có sẵn. Các bảng lịch sử holdings/factsheet chỉ ghi thêm khi nội dung đổi; riêng `nav_snapshots.csv` ghi một lần quan sát mỗi ngày, kể cả NAV không đổi. Sau đó hệ thống so sánh holdings giữa các snapshot và tạo báo cáo Markdown (tùy chọn gửi Telegram). Repository hiện chỉ có mã nguồn, chưa kèm bộ dữ liệu `data/` hoặc báo cáo thực tế.

Audit kiến trúc và giới hạn: [`docs/AUDIT_2026-10-27.md`](docs/AUDIT_2026-10-27.md). Tranche 2, PIT/provenance và bước tiếp theo: [`docs/TRANCHE_2_DATA_FOUNDATION_2026-10-27.md`](docs/TRANCHE_2_DATA_FOUNDATION_2026-10-27.md). Inventory thực tế: [`docs/DATA_INVENTORY_2026-10-27.md`](docs/DATA_INVENTORY_2026-10-27.md). Đây là pipeline nghiên cứu, chưa phải hệ thống tạo khuyến nghị hoặc backtest đã xác thực.

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

## Cấu trúc

```
VN_Fund_Analysis/
├── .github/workflows/daily.yml    # chạy 08:30 giờ VN hằng ngày + chạy tay
├── config.toml                    # ngưỡng, loại quỹ, watchlist
├── vnfa/
│   ├── fmarket.py                 # client + parser Fmarket, source-event metadata
│   ├── store.py                   # legacy CSV append-only (sep=';'; schema giữ nguyên)
│   ├── quality.py                 # validators, hard errors và warnings
│   ├── temporal.py                # cutoff Point-In-Time, lọc late/future/unknown
│   ├── provenance.py              # source/observation/quality sidecar append-only
│   ├── diagnostics.py             # chẩn đoán nguồn có phân loại kết quả
│   ├── inventory.py               # kiểm kê dữ liệu có thật trên đĩa
│   ├── diff.py                    # so sánh thay đổi vị thế suy luận, PRORATA, cờ quyền
│   ├── pipeline.py                # ghi snapshot + so sánh (dùng chung)
│   ├── report.py / notify.py      # báo cáo Markdown, Telegram
│   └── config.py
├── scripts/
│   ├── run_daily.py               # lệnh chạy hằng ngày (mọi quỹ, mọi bảng)
│   ├── diagnose_sources.py        # probe Fmarket, phân loại auth/network/schema/rate-limit/empty
│   ├── data_inventory.py          # inventory JSON/Markdown dựa trên CSV hiện có
│   ├── analyze_reaction.py        # phản ứng của quỹ trước một mốc thị trường
│   ├── import_full_holdings.py    # nhập danh mục ĐẦY ĐỦ từ báo cáo chính thức (vd. DCDS)
│   ├── backfill_hat_ccq.py        # nạp lịch sử từ 16/05/2026 (chạy 1 lần)
│   └── export_holdings_long.py    # xuất bảng cho smart_money_quant.py
├── tests/test_core.py
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

Chạy trên máy: `python scripts/run_daily.py --dry-run --limit 3`, `pytest -q` để chạy test.

## Đọc kết quả

- `reports/latest.md`: báo cáo lần chạy gần nhất (thay đổi vị thế ước tính, watchlist, cảnh báo và chi tiết từng quỹ).
- `data/changes.csv`: nhật ký mọi thay đổi (dùng để phân tích tiếp).
- `data/holdings/{QUY}.csv`: mỗi kỳ công bố một khối dòng. `observed_date` là trường ngày legacy, không có giờ; xem hướng dẫn `observed_date_basis` ở mục Point-In-Time trước khi dùng trong kiểm định lịch sử. Timestamp chính xác nằm ở sidecar, không sửa schema holdings cũ.

Liên kết với `smart_money_quant.py`: `python scripts/export_holdings_long.py` tạo `exports/holdings_long.csv` đúng schema `holdings` của module đó (`period_end` do suy ra, nên gắn cờ `period_end_inferred`).

## Inventory, validation, provenance và Point-In-Time

Tạo inventory thực tế, không ước lượng số quỹ/rows còn thiếu:

```bash
python scripts/data_inventory.py --reference-date 2026-10-27
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

- Nếu dữ liệu `data/profile.csv` đã được thu thập, website công ty quản lý có thể có ở cột `website` (ví dụ vfm.com.vn, vcbf.com, wm.vinacapital.com, ssiam.com.vn); checkout hiện tại không kèm bảng này để xác minh số lượng/độ mới.
- **Không tự động hóa nguồn ngoài khi chưa kiểm tra điều khoản.** Nhiều trang tổng hợp (ví dụ Smoney) cấm sao chép/tái sử dụng dữ liệu nếu không có văn bản đồng ý, nên không có scraper cho các trang đó trong dự án.
- Với trang của công ty quản lý quỹ: kiểm tra `robots.txt` và điều khoản, rồi tải báo cáo tháng (PDF/HTML), trích bảng danh mục vào CSV và dùng `scripts/import_full_holdings.py` (bắt buộc ghi URL `http(s)` vào `source_url`). Nếu tỷ trọng theo % nhưng tất cả giá trị ≤ 1, phải chỉ rõ `--weight-unit percent`; `auto` không thể suy ra đơn vị trong trường hợp đó. Cách này cũng áp dụng cho DCDS (không có trên Fmarket).

## Giới hạn cần nhớ

- Chỉ có **top 10** mỗi quỹ (cổ phiếu) và vài trái phiếu lớn nhất. Quỹ MMF không có danh mục chi tiết trên Fmarket, nên chỉ có NAV, phí, hồ sơ.
- `report_date` của cơ cấu tài sản là ngày theo giờ Việt Nam của `reportTime` (nửa đêm 01/09 ứng với kỳ báo cáo tháng 8): hãy hiểu là cuối kỳ trước đó.
- README trước đây ghi lịch sử NAV trên endpoint Fmarket/hat-ccq có thể thưa và dừng ở 2025; dữ liệu nguồn không có trong checkout này nên tình trạng đó chưa được tái kiểm chứng. `nav.csv` được tải cho lịch sử dài khi quỹ chưa có dữ liệu; `nav_snapshots.csv` ghi NAV theo ngày hệ thống quan sát. `analyze_reaction.py` ghép hai nguồn nhưng ngày snapshot không xác nhận ngày NAV có hiệu lực.
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

- Dữ liệu: API công khai của Fmarket (không có tài liệu chính thức). Dùng cho nghiên cứu cá nhân, giữ tần suất thấp, và tự kiểm tra điều khoản sử dụng trước khi chia sẻ dữ liệu.
- Script backfill đọc lịch sử git của [huyhn-ba-po/hat-ccq](https://github.com/huyhn-ba-po/hat-ccq) (MIT). Không có mã nguồn nào của repo đó được sao chép vào dự án này.
- Đây là công cụ nghiên cứu, không phải khuyến nghị đầu tư.
