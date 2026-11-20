# Phân tích workflow và dữ liệu archive

**Mốc kiểm tra:** snapshot repository tại commit `0338c399` và các run GitHub ghi ngày 20/11/2026. Đây là phân tích mô tả dựa trên archive có sẵn, không phải dữ liệu live, khuyến nghị đầu tư hay bằng chứng giao dịch.

## 1. Workflow

| Workflow / run | Kết quả GitHub ghi nhận | Bằng chứng |
|---|---|---|
| Daily `44706314609` | failure | Bước kiểm thử Python lỗi; bước `Check holdings` bị bỏ qua. Bước kiểm tra budget và ghi trạng thái vẫn chạy. |
| Pages `44706415545` | failure | Bước kiểm thử Python lỗi; bước export và deploy bị bỏ qua. |
| Pages `41603202772`, 03/11 | failure | Là lần Pages đỏ đầu tiên trong chuỗi được kiểm tra; dừng ở kiểm thử Python, sau daily `41602600159` thành công. |
| Yuanta `43646146635`, 14/11 | success | Scan và deploy được GitHub ghi nhận thành công; không chứng minh mọi endpoint hay dữ liệu đều đầy đủ. |

Trên snapshot hiện tại, kiểm thử `test_compact_provenance_reconciles_nav_series_and_enforces_90d_budget` tái hiện lỗi: nó yêu cầu `projected_total_mb < 25` trên `data/` thật. Theo Git tree, dữ liệu được theo dõi tại commit của Pages thất bại ngày 03/11 là 11.156.622 byte, cho dự phóng cùng mô hình khoảng 25,129 MB; commit trước đó là 10.032.394 byte, dự phóng khoảng 24,005 MB. Nội dung test và bộ tính budget không thay đổi giữa các commit này. Đây là bằng chứng mạnh rằng dữ liệu tăng đã kích hoạt assertion, nhưng log lỗi chi tiết từ GitHub không tải được trong môi trường này, nên chưa đối chiếu trực tiếp traceback của từng run.

**Thay đổi đề xuất trong nhánh:** unit test dùng dữ liệu tổng hợp trong thư mục tạm, không phụ thuộc kích thước archive thật. Kiểm tra budget thật vẫn chạy riêng trong workflow. Dự phóng vượt 25 MB được báo `warn`, không gây lỗi; các ngưỡng cứng cho dung lượng hiện tại, tệp đơn lẻ và NAV provenance chi tiết vẫn được giữ. Không sửa hoặc xóa dữ liệu archive để vượt kiểm thử.

## 2. Tình trạng archive tại ngày tham chiếu 20/11/2026

| Dữ liệu | Quan sát và giới hạn |
|---|---|
| Catalogue | 70 quỹ: 35 cổ phiếu, 11 cân bằng, 24 trái phiếu/MMF. |
| Danh mục Top-N cổ phiếu | 407 dòng, 46 quỹ; ngày cập nhật nguồn 08–14/10/2026. Không phải danh mục đầy đủ. |
| Danh mục Top-N trái phiếu | 180 dòng, 33 quỹ; có thể trùng quỹ với bảng cổ phiếu. |
| Lịch sử NAV có ngày nguồn | 47.121 dòng, 59 quỹ, khoảng 20/05/2004–31/10/2025 toàn archive. Ngày cuối toàn archive không có nghĩa mọi quỹ đều đến ngày đó. |
| Snapshot NAV | 564 dòng, 70 quỹ, ngày quan sát cuối 03/11/2026; ngày hiệu lực NAV không xác định. Có 10 dòng thuộc 5 khóa quỹ/ngày bị trùng, mỗi khóa có hai giá trị. Định dạng collector cho phép ghi thay đổi trong ngày, nhưng không có đủ thời gian trên từng dòng để độc lập xác minh thứ tự hay bản chất các bản ghi; giữ nguyên và xem finding chất lượng, không ghép vào lịch sử NAV. |
| Phân bổ tài sản | 158 dòng, 68 quỹ; report date mới nhất 01/09/2026, quá SLA 45 ngày tại mốc kiểm tra. |
| Provenance | 16.432 quan sát, tất cả thiếu `published_at`; không suy ra thời điểm công bố từ ngày hiệu lực hoặc ngày quan sát. |
| Bộ dữ liệu dự kiến | 12/15 khả dụng; thiếu full holdings, changes và performance. Thiếu không đồng nghĩa bằng 0 hoặc chứng minh nguồn không có dữ liệu. |

Kết quả được lưu trong `last_run.json` ngày 03/11: 70 quỹ được xử lý; endpoint lịch sử NAV có 70 phản hồi hợp lệ, trong đó 11 rỗng, 59 dòng được chấp nhận trong khoảng và 47.062 dòng ngoài khoảng yêu cầu bị loại; không có dòng NAV mới/đổi. Ngày hiệu lực lớn nhất được chấp nhận vẫn là 31/10/2025. Đây là kết quả lần chạy đó, không chứng minh tình trạng nguồn hiện tại.

### Minh họa mức độ xuất hiện trong Top-N

Trong 46 quỹ có ít nhất một dòng Top-N cổ phiếu, TCB và MBB mỗi mã xuất hiện ở 31 quỹ, MWG ở 29 quỹ. Đây là **số quỹ có dòng được công bố**, không phải tổng sở hữu, tỷ trọng theo AUM, giao dịch mua/bán hay tín hiệu đầu tư. Các quỹ không có dòng tương ứng vẫn có thể nắm giữ mã ngoài Top-N.

### NAV lịch sử: ví dụ giới hạn theo mẫu

Với các quỹ có NAV dương ở **cả hai ngày chính xác 02/01 và 31/10/2025**, trung vị thay đổi NAV đơn giản là +13,09% cho 19/35 quỹ cổ phiếu, +13,04% cho 2/11 quỹ cân bằng và +4,90% cho 12/24 quỹ trái phiếu/MMF. Công thức `(NAV cuối / NAV đầu − 1) × 100`; mỗi quỹ đủ điều kiện có trọng số như nhau. Mẫu thiếu dữ liệu và rất khác nhau về quy mô, không đại diện toàn bộ, không phải hiệu suất hiện tại, tổng lợi nhuận đã xác minh hay so sánh điều chỉnh rủi ro. Quy ước phân phối và phí chưa được xác minh độc lập. Snapshot ngày quan sát không được nối vào chuỗi này.

## 3. Dung lượng và bước kiểm chứng

`data/` trong snapshot có 11,157 MB; tệp lớn nhất `research_observations.csv` khoảng 8,269 MB; không có NAV history provenance chi tiết theo từng điểm. Mô hình mặc định giả định 69 quỹ, 90 ngày và mức tăng cố định; catalogue thực có 70 quỹ, vì vậy 25,129 MB chỉ là dự phóng theo giả định, không phải giá trị đo được hay cận trên đáng tin cậy. Ngưỡng cảnh báo 25 MB và ngưỡng lỗi 80 MB đối với dung lượng hiện tại, cùng ngưỡng 20 MB/tệp, không bị nâng lên.

Sau khi triển khai bản sửa, chạy daily `limit=2`, `dry_run=true` và kiểm tra đầy đủ kết quả test, dòng `NAV history` và budget; dry-run không ghi archive. Sau đó mới cân nhắc chạy đầy đủ trên `main` và theo dõi Pages. Không chia sẻ token, URL có query nhạy cảm hay payload nguồn. Nếu bước khác vẫn lỗi, cần xem log đã làm sạch; bản sửa chưa chứng minh kết nối hoặc chất lượng dữ liệu live. Scanner Yuanta incremental/module-select vẫn có rủi ro ghi đè checkpoint khi một phần trả rỗng/lỗi hoặc bị bỏ chọn; lưu bản sao cache/artifact trước khi thử.

Nguồn: các tệp `data/` trong snapshot nêu trên, `scripts/data_inventory.py`, `scripts/check_data_budget.py` và metadata các run GitHub được nêu. Không thu thập thêm dữ liệu provider trong phân tích này.
