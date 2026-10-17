# GDS3725 Private PIN Bridge for Home Assistant OS

**一个独立的 Home Assistant Custom Integration（不是 Add-on）**。参照 Grandstream 官方 [`grandstream_home`](https://github.com/GrandstreamEngineering/grandstream_home) 的项目组织方式重写；当前只保留 PIN 事件识别用途的框架和模块。

将你已成功测试的 Python `GDS3725 PIN Bridge` 迁入 HA 原生 Webhook/事件架构：

```text
GDS3725: Private PIN success
    └─ P15529 Valid Password Input / Action URL (HTTP GET)
        └─ Home Assistant /api/webhook/<secret>
            └─ async GDS client (user account + cached SID + auto Challenge relogin)
                └─ POST /cgi-bin/api-gds_config?cmd=get&type=door_record
                    └─ reconcile new records, de-duplicate by event_uuid
                        ├─ HA event: grandstream_pin_bridge_pin_open
                        ├─ sensor: Last Private PIN user ID
                        └─ sensor: Private PIN events
```

## 安装（HAOS）

1. 解压项目 ZIP。
2. 复制 `custom_components/grandstream_pin_bridge/` **整个文件夹**到 HA 配置目录 `/config/custom_components/grandstream_pin_bridge/`（用 Samba、Studio Code Server、File Editor 等可访问配置目录的工具）。
3. 重启 Home Assistant Core。
4. 设置 → 设备与服务 → 添加集成 → 搜索 **GDS3725 Private PIN Bridge**。
5. 在配置中填 GDS3725 的设备 IP、Web 端口（通常 443）、Web 协议、Web 账号用户名和密码。**用你设置过的 `user`**。集成会实测登录和 `door_record` 查询，校验通过才添加。
6. 添加完成后，打开此集成的 **配置（Configure）** 页面。页面说明会给出一个完整的回调**路径** `/api/webhook/<随机ID>`。将它拼接到你的 **HA 局域网地址**，例如：

   ```text
   http://192.168.9.174/api/webhook/这里复制配置页的随机ID
   ```

   此处 `192.168.9.174` 仅为示例；若 HAOS 实际 IP 不同，请换成 HAOS IP。HA 如果已启用 HTTPS，改用 `https://` 及实际端口。**HA Webhook 的 URL 与老程序的 `http://IP:8088/pin-open` 不同，务必重新设置设备 URL**。
7. GDS3725 网页 → **Maintenance → Event Notification → Valid Password Input**，填写上面的完整 URL，保存。此事件参数就是实测的 **P15529**。
8. 使用现有 Private PIN 开门一次。HA 开发者工具 → 事件 → 监听 `grandstream_pin_bridge_pin_open`；同时查看集成的两个传感器。无需额外开放 8088 端口，也不需要 MQTT、邮件、云账户。

> **安全**：Webhook 的随机路径是口令，勿公开或贴到论坛。监听仅限局域网、HTTP `GET` 且校验设备源 IP（必须是填写的设备 IP）。如果从反向代理转发，可能因来源 IP 改变而被拒绝；推荐 GDS 直连 HAOS LAN IP。不要将这类 webhook 放到互联网。

## HA 事件字段

事件名称：`grandstream_pin_bridge_pin_open`，例子：

```yaml
event_type: grandstream_pin_bridge_pin_open
data:
  user_id: "35"
  name: "n35"
  door_id: "1"
  open_door_time: "2026-10-16 15:20:32"
  record_uuid: "34"
  event_uuid: "3b0240c3-29da-415c-ad63-fcfb51648d3d"
  device: "192.168.9.230"
  received_at: "2026-10-16T22:20:32+00:00"
```

HA 自动化示例（只做通知、日志，不建议作二次门禁授权）：

```yaml
- alias: "GDS PIN 35 事件记录"
  triggers:
    - trigger: event
      event_type: grandstream_pin_bridge_pin_open
      event_data:
        user_id: "35"
  actions:
    - action: logbook.log
      data:
        name: "GDS3725"
        message: "用户 35 使用 Private PIN 开门"
```

上例是自动化动作段落示意；在 `automations.yaml` 文件中应该使用顶层 `- alias:` 列表（不要加 `automations:` 键）。

## SID 和数据处理

- 复用 SID，不需要心跳；当 API 返回权限错误、非 JSON 或非成功结果时，自动再次 Challenge 登陆并重试一次。
- 单个协程执行所有 GDS 查询，不会因并发触发在同一个集成内互踢会话。
- 等待开门记录落盘（默认最多 6 秒），最多一次取最新 50 条；收到多次相邻 PIN 通知会批处理。新开门记录数必须与 PIN 通知数相等，且每个记录都有 `user_name` 与 `event_uuid`，才发布 HA 事件。
- 事件通过 `event_uuid` 去重，保存到 HA 的 `.storage`，以及最后一次的用户/事件计数。不依赖 SQLite，使用 HA 的 `Store`，包括未发送成功的转发队列（最多 200 条，定时重试）。
- 重启时从 GDS **当前最新记录**建立基线，避免将没有对应通知的离线旧记录误认成 PIN。重启期间的事件可能漏报。
- `open_door_type` 和 `card_num` 不能作为 PIN 的充分判据；此前用户实测 PIN 记录的 `open_door_type=3`、卡号仍有值，因此不要据此分类。

## 重要的归因限制

P15529 的 HTTP GET 没有用户身份，`user_name` 来自独立的 `door_record` 查询。
如果刷卡、Guest PIN、第三方开门在同一时刻交错发生，通知数与新记录数相等时**仍有误匹配的可能**，目前无法从设备公开接口证明两者具有原子对应关系。数量不一致、记录缺字段时主动丢弃该批关联并输出警告。

**本项目用于自动化通知和日志，不能作为下游门禁权限的唯一依据**。设备自身继续负责验证 PIN 和开门。

## 排障

- 设置集成时 `invalid_auth`：检查设备 `user` 账号密码及 `door_record` 权限；你之前已经实测了此权限。
- 设备 GET 后没有事件：检查设备已修改为 HA 的完整 Webhook URL；在 HA 日志里查 `grandstream_pin_bridge`；确认不是通过反向代理，并检查 HA 网段/VLAN 是否能接受 GDS 请求。
- SSL 验证失败：GDS 使用自签证书时，默认关闭 `verify_ssl` 对应 `curl -k`；若设备使用受信任证书可开启。
- 没有收到事件但设备仍可开门：P15529 事件推送与设备开门是独立操作，HA 重启或网络中断时会漏报，设备不会补发。
- 两个 `user` 会话互踢：停止旧 Python 常驻脚本；新集成内部串行处理，但其他客户端使用同一账户可能挤掉 SID，集成会自动登录修复。
- 日志警告 `Uncertain PIN attribution`：同时间新增非 PIN 记录或设备响应延迟过长；可在 Configure 增大最大等待时间。

## 文件与许可

```text
custom_components/grandstream_pin_bridge/
  __init__.py       HA 生命周期 + GET webhook + IP 白名单
  manifest.json     HA 自定义集成元数据
  config_flow.py    独立 user/admin 账号配置和选项
  const.py          常量
  api.py            GDS API / Challenge / SID 重登
  bridge.py         异步队列、批次匹配、HA event、Store
  matcher.py        纯逻辑匹配与去重
  sensor.py         两个传感器（可扩展的 Sensor 基类）
  strings.json      界面默认字符串
  translations/     简体中文与英文翻译
reference/REUSE_NOTES.md  和官方仓库的关系、保留/移除清单
tests/                  不需要 HAOS 即可运行的基本回归测试
```

## 可选转发到外部 HTTP 服务器

在设备集成 **Configure（配置）** 中设置 `Optional forwarding URL`，例如 `http://192.168.9.200:9000/events`；如需下游 Bearer Token，填写 `New forwarding Bearer token`。处理到确定的事件时同时发布 HA 事件并向该 URL 发送 JSON POST：

```json
{
  "event": "private_pin_open",
  "user_id": "35",
  "name": "n35",
  "door_id": "1",
  "record_uuid": "34",
  "event_uuid": "3b0240c3-29da-415c-ad63-fcfb51648d3d",
  "open_door_time": "2026-10-16 15:20:32",
  "device": "192.168.9.230",
  "received_at": "2026-10-16T22:20:32+00:00"
}
```

收到 2xx 状态码才认为转发成功；非 2xx、超时、网络故障时保留在 Store 中并约每 10 秒重试（最多 200 条待发，超过后舍弃最旧者），因此目标服务应按 `event_uuid` 去重。Token 不会打印到日志。此功能可选，单纯在 HA 中消费事件时留空即可。

## Daypass 座位与订单 check-in（0.3.0）

以 HA 管理员打开 GDS3725 **设备详情页**，在设备信息下方查看 **Access Control Users** 卡片。卡片按座位展示状态、座位号（`user_id`）、当前订单号（`username`）、当前订单入场次数和首次 check-in 时间。配置菜单中的旧“门禁用户”入口已移除，配置按钮直接打开回调与连接设置。

| 状态 | 条件 |
| --- | --- |
| 🔴 未售出／已失效 | `enable_card=0` |
| 🟡 未 check-in | `enable_card=1`，订单和有效期完整，当前订单入场次数为 0 |
| 🟢 已 check-in | 当前订单已有有效 PIN 入场记录 |
| ⚪ 未知 | 启用字段、订单或有效期不完整／异常 |

售票系统负责“一订单、一座位、一天”，写入订单号、启用用户，并维护设备 `SOD` / `EOD` 有效期；失效时禁用用户。不在 HA 零点清空计数。新订单从 0 次开始，禁用后重新启用同一个订单继续原次数。

打开卡片、点击刷新及页面停留时每 30 秒同步用户资料，不触发 check-in。每次确认 PIN 事件时也会重新读取用户资料；只有记录中的开门时间位于当前订单设备有效期内，才记入该订单。第一次为 check-in，之后累计次数；重复 `event_uuid` 不重复计数。资料读取失败时卡片清空旧结果，事件仍保留，但不会用旧订单映射猜测归属。

设备页“活动”新增记录：**订单号 · 开门时间 · 座位号 · 第 N 次入场**，关联到本设备的最近 PIN 用户传感器。显示依赖 HA Logbook / Recorder 正常启用并记录该实体；原有传感器活动记录仍保留。事件及可选 HTTP 转发新增 `ticket_id`、`entry_count`，仅在订单归属确认时提供。

座位当前映射、订单统计、入场历史及去重 ID 通过 HA Store 持久化。座位换订单不删除旧订单历史。首次安装不推断过去是否入场；HA 离线事件补录暂不实现。这里统计确认收到的 PIN 开门事件，不代表实际进门人数或当前在场人数。

只读取和保存用户 ID、订单号、启用状态和有效期；不保存设备返回的 PIN、卡号等其他资料。有效期及开门时间使用同一设备本地时间，界面显示设备原始时间，不将其误当成 UTC。

### 统一设备 API 入口

所有 GDS 业务接口通过 `GDSClient.request()` 调用；`records()`、`users()` 仅封装接口参数及业务数据提取，界面层不负责登录或重试。后续新增 GDS 接口也应使用这个入口，不能直接调用底层 `_request()`。

统一入口串行执行完整请求流程，复用 SID；HTTP 401/403、GDS 非成功结果、无法解析的 JSON/XML 或缺失结果结构会清除 SID，重新登录并重发一次。第二次仍失败则向调用方返回异常，不无限重试。设备非成功结果可能代表权限不足或参数错误，重登无法解决时同样返回错误。超时、连接/TLS 错误、重定向和其他 HTTP 错误统一转为 `DeviceError`，不会盲目重发；任务取消正常向上传播并释放锁。错误消息不包含响应内容、密码或 SID。

当前入口面向已有查询 API；将来增加写操作时，应先核实设备失败响应和重发的幂等语义。Challenge 登录本身使用底层传输，避免递归触发登录；可选的外部 HTTP 事件转发并非 GDS API，仍使用独立的持久化转发队列。


## 设备详情页用户卡片（0.3.0）

更新整个 `custom_components/grandstream_pin_bridge/` 目录（包括新增的 `frontend/` 和 `device_panel.py`），重启 HA，再刷新浏览器页面。以 HA 管理员进入 GDS3725 的设备详情页，**设备信息卡片下方**会显示 **Access Control Users** 卡片；无需手动添加 Lovelace 资源。

每次进入设备页实时读取用户列表，点击 **刷新** 仅重新读取数据，SID 和错误重试由统一 API 客户端处理。列表不写入实体或 Recorder；后端仅返回座位、订单、启用状态、有效期和入场统计，并校验管理员权限及 GDS 设备归属。读取失败清空旧列表，避免把旧结果显示成当前数据。

HA 没有公开的设备详情页自定义 Card 扩展接口，因此本功能随集成加载一个前端模块，在 `ha-config-device-page` 的 `ha-device-info-card` 后插入卡片。该功能依赖 HA 前端内部结构，HA 升级后可能需要适配；若卡片未出现，请检查前端版本和 HA 日志。

已通过 Chromium 的模拟设备页测试（挂载、刷新、文本转义、错误清空和管理员可见性）及后端模拟权限测试；尚未在真实 HA 实例和 GDS 硬件上联调。
