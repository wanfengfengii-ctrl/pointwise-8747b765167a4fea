# 同一份载荷交给两台读板机

## 发行

在 `archive.publish` 增加 `issue_shared(session, single_reel, double_reel)`。两个盘号分别指向同一业务账中已签的 `Dialect.SINGLE` 和 `Dialect.DOUBLE` 定稿。固定盘和尾签盘均可使用，按定稿口径判侧别。发行使用已签定稿，设备停用后也可办理。未知盘报 `NotFound`，已登记但未定稿报 `PendingReel`。错侧别或无共同编码报 `InvalidMaterial`。

返回字典 `{'receipts': (single_receipt, double_receipt), 'payload': payload}`。`receipts` 是两份原定稿的 `FirstReceipt` 对象，单侧在前，双侧在后。`payload` 是一份连续的 `bytes`，由两台旧机直接消费。其分帧及行值按 README 的原口径解释。每侧整流解出的值序列须与该侧原定稿的值序列完全相等。允许重选标记和样本字节，有共同编码即可选择任一可行件。双空交付 `b''`，单空拒发。发行与点收只读。

## 夜班点收

在 `review.reconcile` 增加 `reconcile_shared(package, single_report, double_report)`。夜班另路取得两份可信的 `PublishedReport`，它们是同一发行账的 `get_report` 返回值。点收只凭这两份报告和实收字典办理。分别核对两侧报告口径、盘号及完整首执，等值异源定稿也须拒绝。对实收 `payload` 实际按两种口径读到末尾，并比较两份完整有序行值。

返回 `(single_rows, double_rows)`。每侧是原序元组，每项形如 `((wire_start, wire_end), original)`，其中 `original` 是该侧原定稿相同行序的 `ReadingRecord`，含原盘区间、值与页内出处。实收坐标和原盘坐标都从零开始，使用半开区间。双空点收返回 `((), ())`。字典形状或字段类型错误、错口径、错绑、截断、非法标记、行数或行值不符均抛出 `InvalidMaterial`，票面只认可完整的两侧结果。

## 样例和运行

`python3 -B shared_workday.py` 用 `samples/shared_payload.json` 走原业务流程，生成这些定稿并核对旧领取和原位核账。`shared_workday.load_sample()` 可供新核验复用。夹具通过正常装盘和实收生成定稿。

| 单侧定稿 | 双侧定稿 | 发行和点收预期 |
| --- | --- | --- |
| `single-three` | `double-two` | 六字节共同件，单侧值 `1,1,1`，双侧值 `2,2`。单侧实收首行 `[0,2)` 对原行 `[0,3)`，原出处为 `S1[0,2)` 与 `S2[0,1)`。双侧实收首行 `[0,3)` 对原出处 `D1[0,2)` 与 `D2[0,1)`。 |
| `single-zero` | `double-two` | 八字节共同件，单侧值 `0,1,1`，双侧值 `2,2`。实收区间依次为单侧 `[0,3),[3,6),[6,8)`、双侧 `[0,5),[5,8)`。 |
| `single-zero` | `double-three-one` | 无共同编码，抛出 `InvalidMaterial`。两份原载荷长度均为六字节。 |
| `empty-single` | `empty-double` | 空载荷，两侧均无行。 |

首例的首执不变，把实收载荷换成 `double-two` 的原载荷，须整件拒收。用 `single-three-copy` 代替首例的单侧独立定稿也须整件拒收。两份单侧定稿读数相同，首执及原出处不同。

在现有 `.pointwise-runtime.json` 的 `verify` 保留完整原回归，并接入共同发行和点收核验。核验失败须以非零退出。`demo` 用样例展示实际共同载荷、两侧实收区间和原页出处，在关闭设备和档案连接后完成点收。
