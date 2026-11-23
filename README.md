# 岚川测读交换卷

读板设备每次只交出当前页。中心先将本盘原位资料写入业务账，再办理退页。最后一页退出后，档案员按固定口径或尾签定稿。晚班在设备停用后按盘领取报告、首执和页内出处。

## 运行

使用 Python 3.11 和标准库，在项目根目录执行。核验入口由运行平台提供。

```sh
python3 -B check_project.py
python3 -B demo.py
```

直接运行同一套测试可用 `python3 -B -m unittest discover -s tests -t . -v`。演示读取 `samples/workday.json`，在独立临时目录开立设备账和业务账，结束后清理。样例包含一盘固定口径、两盘以相同载荷选择不同读法的尾签盘，以及一盘待办。双字节盘的首页会退出并失答。

## 一天的工作

1. 早班登记员在本批登记盘号、设备及固定口径。重开或换班重投同一身份会领回初次登记。
2. 接管员从设备取当前页，核对票据、归属和载荷校验，先持久实收，再退页。设备退页后才交下一页。
3. 档案员用固定标记签发齐盘。样例 `01 07 02 03 04` 的两行值均为 7，总量为 14。
4. 午班以相同的 `01 01` 和 `01 01 01 01` 办理两盘尾签盘。尾签到达前，档案查询只提示待办。
5. 一盘首页退出后失答。接班员重开业务账，按原盘和原页号核证退出，再接下一页。
6. 档案员以两份尾签分别定稿。单字节盘签出三条 1，总量 3。双字节盘签出两条 2，总量 4。双字节首行跨过两个物理页。
7. 停用设备后，晚班按盘号领取首次报告、完整出处及原首执。
8. 晚班用原位窗 `[2,3)` 核对双字节盘首行。该行的全盘区间为 `[0,3)`，两段页内出处分别为 `[0,2)` 和 `[0,1)`。
9. 晚班按原交班批检查待办和可领取清单，把未签的盘交给下一班。

## Python 接口

```python
from archive.publish import finalize
from archive.seal import TailSeal
from custody.receive import receive_page
from custody.resume import resume_page
from reading.framing import Dialect
from registry.identity import RegistrationMode
from registry.intake import register
from registry.session import open_session
from review.handover import handover_list
from review.reconcile import audit_range
from review.report import get_report

with open_session('exchange.sqlite', 'day-1', device_path='device.sqlite') as session:
    session.device.load_reel('reader-1', 'reel-1', [('p1', bytes.fromhex('01 01 01'))])
    register(session, 'reel-1', 'reader-1', RegistrationMode.TAIL)
    page = session.device.current_page('reader-1', 'reel-1', 'p1')
    receive_page(session, 'reel-1', 'p1', page)
    first = finalize(session, 'reel-1', TailSeal('reel-1', ('p1',), Dialect.DOUBLE))

with open_session('exchange.sqlite', 'evening') as session:
    original = get_report(session, 'reel-1')
    rows = audit_range(session, 'reel-1', 2, 3)
    batch = handover_list(session, 'day-1')
```

`open_session(ledger_path, batch_id, *, device_path=None)` 可重复打开已有账本。`initialize(session)` 只补建结构。设备账与业务账是两个文件，各自提交。`load_reel` 把新盘的物理页按给定顺序装入本地设备端口，每个设备依装盘和物理页顺序交出当前页。空盘用空页表声明。设备账保有权威页号清单和已退出事实，退出时清除该页的设备载荷。按原页请求读取已退出页会抛出 `DevicePageUnavailable`。`schedule_release_loss` 可安排设备提交前或提交后丢失一次退页响应。

- `register(session, reel_id, device_id, mode)` 返回不可变登记。`RegistrationMode` 有 `FIXED_SINGLE`、`FIXED_DOUBLE` 和 `TAIL`。固定盘登记后以 `Dialect.SINGLE` 或 `Dialect.DOUBLE` 作为 `finalize` 的标记。
- `receive_page(session, reel_id, page_id, page)` 实收 `DevicePage`，返回 `ReleaseOutcome.RELEASED` 或 `ReleaseOutcome.UNKNOWN`。同页同件重投沿用原实收，已确认退出页可以用原票据重投。`resume_page(session, reel_id, page_id)` 查询原页，确认仍是当前页时可重试。结果未知时继续待查。退出未确认的前页须先续办，再实收次页。
- `finalize(session, reel_id, authorization=None)` 返回 `PublishedReport`，其中 `snapshot` 有盘号、口径、载荷长度、原序行、总量和入档凭据，`receipt` 有独立首执号及盘号。尾签是由交接方提交的结构化声明，包含盘号、有序页号元组及读法，由档案方对照设备权威页清单。已实收完整且确认退出的盘可脱离设备定稿和重投。零页盘首次定稿时须能取得设备的齐盘依据。
- `get_report(session, reel_id)` 原样返回固定成果。`audit_range(session, reel_id, start, end)` 返回原序相交行，保留各行完整出处。`handover_list(session, batch_id)` 返回 `ready` 和 `pending` 两个盘号元组，各自遵循原登记顺序。

登记盘无定稿、缺少齐盘资料、尾签尚未提交或退出未确认时，调用方可捕获 `PendingReel`。未知盘、原页或批次报 `NotFound`。同号异件报 `Conflict`。坏页、错盘、错序尾签及非法复核窗报 `InvalidMaterial`。非法记录报 `InvalidReading`，含问题记录的全盘起点。SQLite 写入或提交错误直接交给调用方，签发事务回滚，先前实收、退出与旧定稿保留。应用接口自行管理事务，调用前应结束调用方的手写 SQL 事务。

## 分帧与核账口径

记录标记只能是十六进制 `01` 或 `02`，分别表示一个或两个样本。口径决定每样本占一或两字节。把记录中的样本字节都视为 0 到 255 的无符号值，精确相加。标记占原位，行值由样本贡献。总量单位为样本字节值之和。记录可以跨任意页边界。行的全盘区间和各段页内原据均为半开区间，页内片段按物理页序排列。

齐盘后使用指定口径读取到整盘边界。非法标记和未闭合记录都会阻止定稿。已证实完整的空载荷签出零行、零总量。复核窗必须满足 `0 <= start <= end <= 载荷长度`，两端均为整数。空窗返回空元组，有正长度交集时返回整条签发行。

`registry`、`custody`、`reading`、`archive`、`review` 分别负责登记、实收退出、计算、档案和复核。正常回归覆盖这五类职责的交接，包括坏页保持未退出、真实退出后失答、错序尾签、首执写入失败后的原子回滚、离线快照和区间核账。样例预期直接按字节和区间手算。
