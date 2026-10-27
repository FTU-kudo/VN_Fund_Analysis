# VN_Fund_Analysis

Theo dõi **hằng ngày toàn bộ quỹ mở trên Fmarket** (cổ phiếu, cân bằng, trái phiếu, MMF; 69 quỹ ngày 25/10/2026) đã **mua/bán** gì, chạy hoàn toàn bằng GitHub Actions (chi phí 0 đồng).

Mỗi ngày workflow thu: top 10 cổ phiếu, top trái phiếu, cơ cấu tài sản, tỷ trọng ngành, biểu phí, hồ sơ quỹ và lịch sử NAV của từng quỹ. Mọi bảng chỉ ghi thêm khi nội dung đổi. Sau đó so với kỳ trước, tạo báo cáo Markdown (tùy chọn gửi Telegram).

## Cách phát hiện MUA/BÁN

| Nhãn | Ý nghĩa |
|---|---|
| **MUA / BÁN** | Khối lượng cổ phiếu quỹ nắm giữ đổi từ ngưỡng `min_volume_change_pct` (mặc định 5%), sau khi loại phần đổi đồng loạt theo quy mô quỹ. Dùng **khối lượng** nên không bị nhiễu bởi giá tăng/giảm |
| **Đổi đồng loạt (PRORATA)** | Quỹ đổi gần như mọi mã cùng tỷ lệ (nhận/chi trả vốn). Không phải quan điểm đầu tư, không tính vào dòng tiền ròng |
| **Nghi do quyền (CORP_ACTION?)** | Từ 3 quỹ trở lên cùng đổi khối lượng một mã với tỷ lệ gần như nhau trong `lookback_days` ngày (cổ tức cổ phiếu, thưởng, chia tách). Không tính vào dòng tiền ròng |
| **Vào / Rời top 10** | Mã xuất hiện/biến mất khỏi top 10. Dữ liệu bị kiểm duyệt: chưa chắc là mua mới hay bán hết |

Giá trị ước tính (tỷ đồng) = chênh lệch khối lượng × giá ngầm định tại kỳ báo cáo (giá trị tài sản / khối lượng).

## Cấu trúc

```
VN_Fund_Analysis/
├── .github/workflows/daily.yml    # chạy 08:30 giờ VN hằng ngày + chạy tay
├── config.toml                    # ngưỡng, loại quỹ, watchlist
├── vnfa/
│   ├── fmarket.py                 # client + parser API Fmarket
│   ├── store.py                   # lưu CSV append-only (sep=';')
│   ├── diff.py                    # phát hiện MUA/BÁN, PRORATA, CORP_ACTION?
│   ├── pipeline.py                # ghi snapshot + so sánh (dùng chung)
│   ├── report.py / notify.py      # báo cáo Markdown, Telegram
│   └── config.py
├── scripts/
│   ├── run_daily.py               # lệnh chạy hằng ngày (mọi quỹ, mọi bảng)
│   ├── analyze_reaction.py        # phản ứng của quỹ trước một mốc thị trường
│   ├── import_full_holdings.py    # nhập danh mục ĐẦY ĐỦ từ báo cáo chính thức (vd. DCDS)
│   ├── backfill_hat_ccq.py        # nạp lịch sử từ 16/05/2026 (chạy 1 lần)
│   └── export_holdings_long.py    # xuất bảng cho smart_money_quant.py
├── tests/test_core.py
├── data/                          # tự sinh:
│   ├── holdings/{QUY}.csv         #   top 10 cổ phiếu mỗi kỳ công bố
│   ├── bonds/{QUY}.csv            #   top trái phiếu mỗi kỳ công bố
│   ├── nav_snapshots.csv          #   NAV/CCQ chụp hằng ngày (fund;date;nav), chỉ ghi khi NAV đổi
│   ├── nav.csv                    #   lịch sử NAV dài (tải 1 lần, THƯA và dừng ở cuối 2025, xem Giới hạn)
│   ├── allocation.csv             #   cổ phiếu/trái phiếu/tiền theo kỳ báo cáo
│   ├── industry.csv, fees.csv, profile.csv
│   ├── changes.csv                #   nhật ký MUA/BÁN (cổ phiếu và trái phiếu, cột asset_class)
│   └── funds.csv, last_run.json
└── reports/                       # tự sinh: latest.md và YYYY-MM-DD.md
```

## Triển khai (khoảng 10 phút)

1. Tạo repo **VN_Fund_Analysis** trên GitHub, copy các file vào đúng thư mục rồi push.
2. **Settings → Actions → General → Workflow permissions** chọn *Read and write permissions* (để workflow commit được dữ liệu).
3. Tab **Actions → Daily fund holdings check → Run workflow**, nhập `limit = 3` và bật `dry_run`. Đọc log: phải thấy danh sách quỹ và số mã của mỗi quỹ.
   **Lần chạy thật đầu tiên là bước kiểm tra quan trọng nhất**: code được kiểm thử trên dữ liệu Fmarket đã lưu của repo khác, chưa được gọi trực tiếp vào API Fmarket từ môi trường phát triển. Nếu log báo 403/429 hoặc thiếu trường, xem mục Xử lý lỗi.
4. Chạy lại không `dry_run` và không `limit` để ghi mốc đầu tiên cho mọi quỹ.
5. (Tùy chọn) Nạp lịch sử 5 tháng gần nhất, chạy trên máy cần có `git`:
   ```bash
   pip install -r requirements.txt
   python scripts/backfill_hat_ccq.py
   git add data && git commit -m "data: backfill" && git push
   ```
   Chạy backfill **trước** lần chạy thật đầu tiên ở bước 4 để mốc đầu của bạn nối tiếp lịch sử.
6. (Tùy chọn) Telegram: thêm secrets `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` ở **Settings → Secrets and variables → Actions**.

Chạy trên máy: `python scripts/run_daily.py --dry-run --limit 3`, `pytest -q` để chạy test.

## Đọc kết quả

- `reports/latest.md`: báo cáo lần chạy gần nhất (dòng tiền ròng, watchlist, chi tiết từng quỹ).
- `data/changes.csv`: nhật ký mọi thay đổi (dùng để phân tích tiếp).
- `data/holdings/{QUY}.csv`: mỗi kỳ công bố một khối dòng. `observed_date` là ngày hệ thống **thấy** danh mục lần đầu, dùng làm mốc Point-In-Time.

Liên kết với `smart_money_quant.py`: `python scripts/export_holdings_long.py` tạo `exports/holdings_long.csv` đúng schema `holdings` của module đó (`period_end` do suy ra, nên gắn cờ `period_end_inferred`).

## Phản ứng của quỹ trước một mốc thị trường

```bash
python scripts/analyze_reaction.py --peak 2026-05-07
```
Báo cáo `reports/reaction_<mốc>.md` gồm: hiệu suất NAV từ mốc theo loại quỹ, dịch chuyển cơ cấu tài sản (cổ phiếu/tiền), dòng tiền mua/bán chủ động theo tháng và mã mua/bán ròng nhiều nhất. Chỉ dùng quỹ có NAV cập nhật trong `--fresh-days` ngày.

## Nguồn ngoài Fmarket (quỹ không có trên Fmarket, danh mục đầy đủ)

- Danh bạ trang web chính thức của 26 công ty quản lý nằm ở cột `website` của `data/profile.csv` (ví dụ vfm.com.vn, vcbf.com, wm.vinacapital.com, ssiam.com.vn).
- **Không tự động hóa nguồn ngoài khi chưa kiểm tra điều khoản.** Nhiều trang tổng hợp (ví dụ Smoney) cấm sao chép/tái sử dụng dữ liệu nếu không có văn bản đồng ý, nên không có scraper cho các trang đó trong dự án.
- Với trang của công ty quản lý quỹ: kiểm tra `robots.txt` và điều khoản, rồi tải báo cáo tháng (PDF/HTML), trích bảng danh mục vào CSV và dùng `scripts/import_full_holdings.py` (bắt buộc ghi `source_url`). Cách này cũng áp dụng cho DCDS (không có trên Fmarket).

## Giới hạn cần nhớ

- Chỉ có **top 10** mỗi quỹ (cổ phiếu) và vài trái phiếu lớn nhất. Quỹ MMF không có danh mục chi tiết trên Fmarket, nên chỉ có NAV, phí, hồ sơ.
- `report_date` của cơ cấu tài sản là ngày theo giờ Việt Nam của `reportTime` (nửa đêm 01/09 ứng với kỳ báo cáo tháng 8): hãy hiểu là cuối kỳ trước đó.
- **Endpoint lịch sử NAV của Fmarket không đáng tin cho giai đoạn gần đây**: trong dữ liệu hat-ccq, chuỗi NAV của mọi quỹ dừng ở 2025-10-31 (có lần dừng ở 2025-12), thưa (~tuần/lần) và ngày cuối còn lùi theo thời gian. Vì vậy NAV hiện tại được chụp mỗi ngày từ chi tiết quỹ vào `nav_snapshots.csv`; `nav.csv` chỉ dùng cho lịch sử xa. `analyze_reaction.py` ghép hai nguồn và loại quỹ có NAV quá cũ.
- Thiếu danh mục đầy đủ nên không biết quỹ có bán hết một mã hay không.
- `observed_date` là ngày phát hiện, không phải ngày quỹ giao dịch. Quỹ giao dịch trong tháng nhưng danh mục chỉ cập nhật theo kỳ.
- Số liệu Fmarket có thể lệch nhẹ so với factsheet chính thức (đã thấy chênh khoảng 0,1 điểm % ở VCBF-BCF). Đối chiếu khi ra quyết định.
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
