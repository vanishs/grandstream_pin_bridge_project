# GDS3725 Private PIN → HTTP 用户 ID 事件

只需要 Python 3.9+ 标准库，不依赖第三方 Python 包。

## 1. 设备配置

在 GDS3725 的 **Maintenance → Event Notification** 中，把 **Valid Password Input（参数 P15529）** 的 Action URL 设置为：

```text
http://192.168.9.174:8088/pin-open
```

请按你自己的服务器 IP 修改。本程序只处理 `GET /pin-open`，不会依赖 `P15525` 或 `P15527`。`P15529` 发的是通知，**不会直接带用户 ID**；收到通知后，程序再读取 `door_record` JSON 获取 `user_name`（例如 `35`）。

## 2. Linux 启动

将 `gds3725_pin_bridge.py` 放到目录中后运行：

```bash
export GDS_BASE='https://192.168.9.230'
export GDS_USER='user'
export DEVICE_IP='192.168.9.230'
export LISTEN_HOST='0.0.0.0'
export LISTEN_PORT='8088'
export DB_PATH="$PWD/gds3725_events.sqlite3"

read -r -s -p 'GDS Web user password: ' GDS_PASSWORD
printf '\n'
export GDS_PASSWORD

python3 gds3725_pin_bridge.py
```

访问 `http://192.168.9.174:8088/health` 应显示 `OK`。

**注意**：程序启动时会先登录设备，读取当前最新的开门记录编号作为基线；以前的记录不会重新当成 PIN 事件发送。

## 3. 示例输出

使用绑定了用户 `35` 的 Private PIN 成功开门时，终端预计出现类似：

```text
INFO Processing 1 PIN notification(s)
INFO PIN OPEN user_id=35 name=n35 uuid=34 time=2026-10-16 15:20:32 event_uuid=...
```

这只是输出格式示例，不代表设备上的实际新事件。

## 4. 可选：推送到另一台 HTTP 服务器

程序启动前再设置：

```bash
export FORWARD_URL='http://192.168.9.200:9000/events'
# 如果下游需要 Bearer 认证，还可以设置：
# export FORWARD_TOKEN='your-downstream-token'
```

每个确定匹配的事件将以 **POST application/json** 发送：

```json
{
  "event": "private_pin_open",
  "user_id": "35",
  "name": "n35",
  "door_id": "1",
  "open_door_time": "2026-10-16 15:20:32",
  "record_uuid": "34",
  "event_uuid": "3b0240c3-29da-415c-ad63-fcfb51648d3d",
  "device": "192.168.9.230",
  "received_at": "2026-10-16T22:20:32+00:00"
}
```

下游 HTTP 返回 2xx 表示成功。推送失败会存入本地 SQLite，下次重试（最多每隔几秒），不必依赖邮件或云平台。下游请按 `event_uuid` 去重：网络故障时可能重复收到同一事件。

## 5. 会话和安全

- 使用独立 Web 账号 `user`，不占用管理员 `admin` 的会话。
- 缓存 SID；检测到 SID 失效或接口返回非预期结果时，重新 Challenge 登录并重试一次。
- GDS HTTPS 默认允许设备的自签名证书，和之前 `curl -k` 的行为相同。如果设备安装了受信任证书，可设置 `GDS_VERIFY_TLS=1`。
- 默认只接受来源 IP 为 `DEVICE_IP` 的 `/pin-open` 请求；可以进一步设置 `INBOUND_TOKEN=某个随机令牌`，并在设备 Action URL 中加上 `?token=相同令牌`。
- 请不要把设备密码写进代码、公开日志或网页 URL 中；将数据库和进程环境放在权限受控的目录和账号下。

## 6. 关于匹配准确性（重要）

程序不直接把“最新记录”认作 PIN；它比较启动后的记录编号增量与同时收到的 PIN 通知数，并在记录尚未写入时短暂重试（默认 6 秒），用 `event_uuid` 去重。

**但它仍然不是设备直接提供“PIN 事件 → 用户 ID”原子关联：** 如果刷卡、Guest PIN、Home Assistant 或其他开门事件在同一时间交错发生，单靠独立的 Action URL 通知和开门记录**无法 100% 证明事件之间的对应关系**。记录数不一致时，程序会报警并跳过该批次，避免硬猜 ID；即使条数一致也不能构成绝对证明。此程序适合日志、自动化联动的验证阶段，**不要将匹配结果作为新的门禁授权判定**。

设备/程序断网期间，Action URL 的推送可能丢失；因为无法事后区分所有 PIN 与非 PIN 记录，本程序在重启后不会冒险补报不确定的历史事件。若需要严格审计，应进一步利用设备原生更明确的事件接口或统一事件源。
