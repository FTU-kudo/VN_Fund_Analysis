# Thay đổi danh mục quỹ mở — 2026-10-28

- Quỹ kiểm tra đầy đủ: **70/70** (lỗi: 0; ghi một phần: 0)
- Quỹ có kỳ công bố mới: **0** | Ghi mốc lần đầu: 0
- Cảnh báo chất lượng dữ liệu: **1** quỹ cổ phiếu/cân bằng trả về danh mục rỗng; không suy ra là không nắm giữ.
- Validation: **222** report; 0 lỗi cứng; 607 cảnh báo; 0 dataset phụ bị từ chối.
- Provenance: **1922** observation rows appended; observed/published times không biết được giữ trống, không suy ra từ kỳ hiệu lực.
- Source trace: **82** request events appended; trạng thái này không xác nhận điều khoản sử dụng hoặc độ đầy đủ của nguồn.
- Bảng bổ sung trả rỗng (allocation: 2, industry: 25); snapshot cũ được giữ lại, không xem là dữ liệu hiện tại hoặc tỷ trọng 0.

Không có thay đổi vượt ngưỡng trong lần chạy này.

## Lưu ý

- Fmarket chỉ công bố **top 10** cổ phiếu mỗi quỹ: "Vào/Rời top 10" chưa chắc là mua mới/bán hết.
- Tăng/giảm vị thế là **suy luận** từ khối lượng sau khi dùng trung vị các mã chung làm proxy quy mô quỹ; không phải dữ liệu lệnh hay xác nhận giao dịch.
- Khi có dưới 4 mã chung với khối lượng hợp lệ, không thể tách đáng tin thay đổi vị thế khỏi dòng tiền vào/ra; hệ thống gắn "Chưa xác nhận" và loại khỏi dòng tiền ròng.
- Khi chỉ có tỷ trọng mà thiếu khối lượng, tỷ trọng có thể đổi do giá, dòng tiền hoặc corporate actions; hệ thống chỉ ghi "Tỷ trọng đổi — chưa suy ra giao dịch".
- "Nghi do quyền" là heuristic từ các quỹ cùng đổi khối lượng gần nhau (cổ tức cổ phiếu, thưởng, chia tách); chưa được xác minh bằng lịch corporate actions chính thức.
- Giá trị nắm giữ ước tính theo giá tại kỳ báo cáo, không phải giá mua/bán thực tế. Sidecar provenance tách effective/published/observed/ingested; legacy observed_date chỉ có độ chính xác theo ngày, còn ngày mirror commit là xấp xỉ và không PIT-safe.
