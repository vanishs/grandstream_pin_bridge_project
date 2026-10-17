# Changelog

## 0.3.1

- 移除设备 PIN 事件总累计传感器，升级时清理其实体注册。
- 最近 PIN 用户传感器简称“入场”，活动正文显示订单号、座位和入场次数，不重复时间。
- 传感器属性补充 ticket_id 和 entry_count；保留既有实体唯一标识及订单历史。

## 0.3.0

- 移除集成配置菜单的门禁用户入口，配置按钮直接打开原连接设置。
- 设备页展示座位的红／黄／绿／未知状态、当前订单、入场次数、首次 check-in。
- 按订单持久化入场历史与去重 ID，新订单从零计数，旧订单保留。
- 有效期内首次确认 PIN 开门触发 check-in，活动日志关联订单、座位和次数。
- 卡片停留时每 30 秒同步，事件计数前实时同步；失败时不使用旧订单映射归因。

## 0.2.1

- 修复设备页用户列表 WebSocket 使用不存在的权限检查方法导致请求失败。
- 区分管理员权限、集成未加载、接口未注册、后端错误和设备连接错误。
- 修正 WebSocket 连接测试模型，并更新前端资源版本。

## 0.2.0

- 设备详情页新增 Access Control Users 卡片，打开读取，支持手动刷新。
- 管理员 WebSocket 接口检查设备归属，只返回用户 ID 和名称。
- 统一 GDS API 会话、响应校验与单次重登重发逻辑；保留配置面板备用入口。

## 0.1.0 (2026-10-16)

- 独立的 Home Assistant Custom Integration 域 `grandstream_pin_bridge`
- 从实机验证的 Python 3 PIN Bridge 迁移 Web `user`/`admin` Challenge/MD5 登录、缓存 SID、失效自动重登、`door_record` JSON 查询。
- HA 内建 LAN Webhook 接收 P15529 的 GET 回调，带随机密钥 ID 和设备来源 IP 校验。
- PIN 通知对新开门记录做批次核对，不确定时拒绝归因；基于 event_uuid 去重。
- 输出 HA 事件、最后用户传感器和 PIN 事件计数传感器，状态通过 HA Store 持久化。
- 可选转发外部 JSON POST，失败时在 HA Store 中排队重试。
- 中文/英文配置 UI、源代码、文档、模拟 API 测试、旧版脚本存档。


