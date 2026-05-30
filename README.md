# Bag 插件使用帮助

Bag 是一个挂载在 `Avatar` 上的背包组件插件。它把背包从“独立实体”改成“组件 + 独立数据表”的模式，适合做装备、道具、材料、邮件附件、离线发奖、商城补单和 GM 工具。

## 1. 这套插件解决什么问题

这套实现的目标很直接：

- 背包挂在 `Avatar` 组件上，跟玩家账号生命周期一致，业务侧直接用 `self.bag`。
- 背包实例数据落在独立数据库表里，不依赖 KBE 自动持久化属性。
- 同一玩家的写操作按顺序串行，避免 SQL 异步回调乱序。
- 客户端只负责读和展示，真正的写逻辑都在服务端。
- 插件自带 `BagManager`，负责 watchdog 扫描，避免某条回调丢失后把队列卡住。
- 背包容量、最大堆叠、绑定、过期、锁定这些常见业务点都留了结构。

如果你只是想快速接入，可以先看第 3 节和第 5 节。

## 2. 文件结构

```text
plugins/Bag/
  plugin.json
  entity_defs/
    BagManager.def
    types.xml
    components/
      BagComponent.def
  base/
    BagManager.py
    plugin_entry.py
    components/
      BagComponent.py
  bots/
    plugin_entry.py
    components/
      BagComponent.py
  common/
    bag_model.py
    bag_service.py
    bag_storage.py
```

### 关键文件

- `plugin.json`：插件注册入口。
- `entity_defs/types.xml`：背包类型定义，决定 `BagItem` 和 `BagItems` 的结构。
- `entity_defs/components/BagComponent.def`：`Avatar` 上的背包组件接口。
- `base/components/BagComponent.py`：base 侧组件实现。
- `common/bag_service.py`：背包核心逻辑。
- `common/bag_storage.py`：SQL 生成和结果解码。
- `base/plugin_entry.py`：base 插件生命周期入口。

## 3. 如何接入 Avatar

在 `Avatar.def` 里挂载组件：

```xml
<bag>
    <Type> BagComponent </Type>
    <Persistent> false </Persistent>
</bag>
```

含义很简单：

- `Type` 必须是 `BagComponent`。
- `Persistent=false` 表示不走 KBE 自动持久化整套组件状态，背包数据由数据库管理。

挂上以后，`Avatar` 的 base 侧就会多一个 `bag` 组件调用入口，客户端侧也会多一个 `bag` 组件回调入口。

## 4. 启动流程

插件启动时的流程大概是：

1. `plugin.json` 注册插件。
2. `base/plugin_entry.py` 初始化插件。
3. `onComponentReady()` 时创建三张表：
   - `kbe_plugin_bag_items`
   - `kbe_plugin_bag_op_logs`
   - `kbe_plugin_bag_meta`
4. 同时创建一个 `BagManager` 常驻实体。
5. `BagManager` 用定时器周期性执行 `tickBagQueues()`。

你不用手动启动 watchdog。只要插件加载正常，它就会工作。

## 5. 最快上手

### 5.1 给玩家加物品

```python
bag = self.bag
bag.addItem(1001, 3, 1)
bag.addItem(2001, 1, 0, '{"atk": 12, "quality": "rare"}')
```

### 5.2 查询背包

```python
bag.listItems(callback)
bag.pageItems(1, 20, callback)
bag.getItem(bid, callback)
bag.getItemCount(1001, callback)
```

### 5.3 设置容量

```python
bag.setCapacity(120)
```

### 5.4 多物品交易

```python
bag.transferItems(targetDBID, [{"bid": bid1, "count": 1}, {"bid": bid2, "count": 3}])
```

## 6. 数据模型

### 6.1 `BagItem`

`BagItem` 是单个物品实例，字段如下：

- `bid`：实例主键，数据库自增 ID。
- `itemID`：物品配置 ID。
- `count`：数量。
- `bagIndex`：背包格子序号。
- `stackable`：是否允许堆叠，`1` 允许，`0` 不允许。
- `maxStack`：单格最大堆叠数量。
- `bindType`：绑定类型。
- `expireAt`：过期时间戳，`0` 表示不过期。
- `locked`：锁定状态，`1` 表示锁定。
- `extra`：附加属性 JSON 字符串。

### 6.2 `BagItems`

`BagItems` 是 `ARRAY<BagItem>`，用于：

- `onBagList(items)`
- `onBagPage(page, pageSize, total, items)`

### 6.3 物品排序规则

客户端展示和服务端列表都会按以下顺序排序：

1. `bagIndex`
2. `bid`

## 7. Base 组件接口

这些接口定义在 `entity_defs/components/BagComponent.def` 的 `BaseMethods` 中。

### 7.1 写接口

- `addItem(itemID, count, stackable=1, extra="")`
  - 服务端调用。
  - 不暴露给客户端。
  - 会优先叠加到同 `itemID`、同 `extra` 的未满堆叠格。

- `removeItem(bid, count)`
  - 服务端调用。
  - 不暴露给客户端。
  - 按实例扣数量，扣到 0 会删除该行。

- `setCapacity(capacity)`
  - 服务端调用。
  - `0` 表示不限制容量。
  - 会把容量同步到 `kbe_plugin_bag_meta`。

- `setCallbackSwitch(callbackName, enabled)`
  - 服务端调用。
  - `callbackName` 支持：
    - `list`
    - `updated`
    - `page`
    - `error`
  - `enabled` 传 `0/1`。

- `splitItem(bid, count)`
  - 客户端可调用。
  - 服务端会校验数量和容量。

- `swapItem(bid1, bid2)`
  - 客户端可调用。
  - 交换两个实例位置。

- `moveItem(bid, bagIndex)`
  - 客户端可调用。
  - 目标格空则移动，有物品则交换。

- `mergeItem(fromBID, toBID)`
  - 客户端可调用。
  - 要求 `itemID`、`extra`、`stackable` 一致，并且合并后不超过 `maxStack`。

- `sortItems()`
  - 客户端可调用。
  - 按 `itemID` 重排 `bagIndex`。

- `transferItems(targetDBID, itemsJson)`
  - 服务端调用。
  - 支持一次交易多个物品。
  - `itemsJson` 形如：

```json
[{"bid": 1, "count": 2}, {"bid": 2, "count": 1}]
```

- `clear()`
  - 服务端调用。
  - 不暴露给客户端。

### 7.2 读接口

- `requestBagList()`
  - 客户端请求完整背包。

- `requestBagPage(page, pageSize)`
  - 客户端请求分页背包。

### 7.3 安全建议

- 奖励发放、扣除、商城补单、邮件附件、GM 指令，都应该走服务端写接口。
- 客户端不要直接拿到 `addItem`、`removeItem`、`clear` 这类高危写入口。
- `splitItem`、`mergeItem`、`moveItem`、`sortItems` 可以开放给客户端，但必须由服务端校验物品归属和规则。

## 8. 服务端 Python API

如果你不想走 `Avatar.bag` 组件，也可以直接按 `databaseID` 取服务对象；日常业务更推荐直接用 `self.bag`。这个入口适合离线发奖、后台补单、GM 工具和不持有 Avatar 实例的业务：

```python
from plugins.Bag.common.bag_service import getBagForEntityID

bag = getBagForEntityID(databaseID)
bag.addItem(1001, 3, 1, "", opID="mail_1001", reason="MAIL")
bag.removeItem(bid, 1, opID="gm_1002", reason="GM")
```

如果你在 `Avatar` 上下文里，还是直接这样写最顺：

```python
bag = self.bag
```

### 8.1 常用方法

- `bag.addItem(itemID, count, stackable=1, extra="", callback=None, opID="", reason="ADD", context="", maxStack=99)`
- `bag.removeItem(bid, count, callback=None, opID="", reason="REMOVE", context="")`
- `bag.splitItem(bid, count, callback=None, opID="", reason="SPLIT", context="")`
- `bag.swapItem(bid1, bid2, callback=None, opID="", reason="SWAP", context="")`
- `bag.moveItem(bid, bagIndex, callback=None, opID="", reason="MOVE", context="")`
- `bag.mergeItem(fromBID, toBID, callback=None, opID="", reason="MERGE", context="")`
- `bag.sortItems(callback=None, opID="", reason="SORT", context="")`
- `bag.clear(callback=None, opID="", reason="CLEAR", context="")`
- `bag.setCapacity(capacity, callback=None)`
- `bag.transferItems(targetDBID, items, callback=None, opID="", reason="TRANSFER", context="")`
- `bag.listItems(callback)`
- `bag.pageItems(page, pageSize, callback)`
- `bag.getItem(bid, callback)`
- `bag.getItemCount(itemID, callback)`

### 8.2 回调约定

写接口回调统一形如：

```python
callback(success, op, index, item, message)
```

读接口回调按各自方法签名返回。

### 8.3 `items` 参数格式

`transferItems()` 既支持 Python list，也支持 JSON 字符串。

Python 侧推荐：

```python
bag.transferItems(targetDBID, [{"bid": 10001, "count": 1}, {"bid": 10002, "count": 3}])
```

如果你从 KBE RPC 直接传参，建议传 JSON 字符串：

```python
avatar.bag.transferItems(targetDBID, '[{"bid":10001,"count":1},{"bid":10002,"count":3}]')
```

## 9. 客户端回调

客户端和 bots 侧组件都能收到这些回调：

- `onBagList(items)`
- `onBagUpdated(op, index, item)`
- `onBagPage(page, pageSize, total, items)`
- `onBagError(message)`

### 9.1 回调含义

- `onBagList(items)`：全量背包。
- `onBagUpdated(op, index, item)`：单条增量。
- `onBagPage(page, pageSize, total, items)`：分页结果。
- `onBagError(message)`：错误提示。

### 9.2 `onBagUpdated` 的 `op`

- `1`：新增
- `2`：更新
- `3`：删除
- `4`：清空
- `5`：移动/交换位置
- `6`：拆分
- `7`：合并
- `8`：整理
- `9`：多物品交易

### 9.3 回调开关

这四个回调可以分别关掉：

- `notifyBagList`
- `notifyBagUpdated`
- `notifyBagPage`
- `notifyBagError`

示例：

```python
avatar.bag.notifyBagUpdated = 0
avatar.bag.setCallbackSwitch("page", 0)
```

## 10. 容量和堆叠

### 10.1 容量

`capacity` 是背包容量，单位是格子数。

- `0`：不限制容量。
- `>0`：最多允许这么多条实例物品存在于背包里。

`setCapacity(capacity)` 会把容量写入 `kbe_plugin_bag_meta`，后续这些操作都会检查容量：

- `addItem()`
- `splitItem()`
- `transferItems()`

### 10.2 堆叠

`maxStack` 是单格最大堆叠数。

规则如下：

- `stackable=1` 时，`addItem()` 会优先叠加到同 `itemID`、同 `extra` 的未满堆叠格。
- 如果单个堆叠格已经满了，剩余数量会自动拆到新格子。
- `stackable=0` 时，每次都会新增独立实例。

### 10.3 绑定、过期、锁定

`bindType`、`expireAt`、`locked` 已经进入 `BagItem` 和数据库结构。

当前插件只负责：

- 保存
- 读取
- 透传
- 同步

真正的业务规则，例如：

- 是否允许交易
- 是否允许删除
- 到期后怎么处理
- 锁定状态下是否允许整理

这些建议由业务层继续接。

## 11. 数据库表

插件会创建三张表：

```sql
CREATE TABLE IF NOT EXISTS kbe_plugin_bag_items (...)
CREATE TABLE IF NOT EXISTS kbe_plugin_bag_op_logs (...)
CREATE TABLE IF NOT EXISTS kbe_plugin_bag_meta (...)
```

### 11.1 `kbe_plugin_bag_items`

用途：保存所有背包实例物品。

核心字段：

- `bid`
- `ownerDBID`
- `itemID`
- `count`
- `bagIndex`
- `stackable`
- `maxStack`
- `bindType`
- `expireAt`
- `locked`
- `extra`

说明：

- `bid` 是主键。
- `ownerDBID + itemID` 不是唯一键。
- 同一种物品可以拆成多个实例。

### 11.2 `kbe_plugin_bag_op_logs`

用途：保存背包操作日志。

核心字段：

- `opID`
- `ownerDBID`
- `targetDBID`
- `opType`
- `bid`
- `targetBID`
- `itemID`
- `count`
- `beforeCount`
- `afterCount`
- `beforeIndex`
- `afterIndex`
- `status`
- `reason`
- `context`

### 11.3 `kbe_plugin_bag_meta`

用途：保存背包元数据，目前主要是容量。

字段：

- `ownerDBID`
- `capacity`

## 12. 写操作队列

所有公开写接口都会先进入 `ownerDBID` 维度的串行队列：

- `addItem`
- `removeItem`
- `splitItem`
- `swapItem`
- `moveItem`
- `mergeItem`
- `sortItems`
- `clear`
- `transferItems`

### 12.1 为什么要队列

KBE 的 `executeRawDatabaseCommand` 是异步的。如果同一玩家连续点很多次：

- 拖拽
- 拆分
- 合并
- 整理

没有队列时，后发请求有可能先完成，造成：

- 数据库位置乱序
- 客户端增量顺序乱掉
- 业务日志和实际状态对不上

### 12.2 队列粒度

当前粒度是：

```text
一次公开写 API 调用 = 一个 Operation
```

这意味着：

- 不会自动把多次拖拽合并成一批。
- 不会自动把多次整理合并成一批。
- 每个操作的日志和回调都保留独立边界。

### 12.3 超时保护

默认超时时间是 `30s`。

如果某条 raw DB 回调丢了：

- 会打 `ERROR`
- 当前操作会失败
- 队列会继续跑下一条

插件里的 `BagManager` 会周期性调用 `tickBagQueues()`。

## 13. 操作日志

成功修改背包后会写操作日志。普通单人操作都是“先改背包，再写日志”。

### 13.1 记录什么

- 来源：`opID`
- 谁操作：`ownerDBID`
- 目标玩家：`targetDBID`
- 操作类型：`opType`
- 物品变化：`bid`、`targetBID`、`itemID`、`count`
- 数量变化：`beforeCount`、`afterCount`
- 位置变化：`beforeIndex`、`afterIndex`
- 额外信息：`reason`、`context`

### 13.2 重要说明

日志写失败不会把已经成功的背包操作回滚成失败，但会打 `ERROR`。

### 13.3 日志分级

背包操作日志支持三级过滤，可以通过 `setBagLogConfig(logLevel=...)` 在运行时动态调整。

| 等级 | 常量 | 记录范围 |
| ---- | ---- | -------- |
| L1 | `BAG_LOG_LEVEL_L1` | 添加、删除、清空、交易 |
| L2 | `BAG_LOG_LEVEL_L2` | L1 + 拆分、合并 |
| L3 | `BAG_LOG_LEVEL_L3` | 全部操作（L2 + 移动、交换、整理） |

各操作类型与等级的对应关系：

```python
# 内置映射（在 bag_service.py 中）
_BAG_LOG_TYPE_LEVEL = {
    "ADD":      L1,   # 添加物品
    "REMOVE":   L1,   # 删除物品
    "CLEAR":    L1,   # 清空背包
    "TRANSFER": L1,   # 多物品交易
    "SPLIT":    L2,   # 拆分
    "MERGE":    L2,   # 合并
    "MOVE":     L3,   # 移动/交换位置
    "SWAP":     L3,   # 交换两个物品位置
    "SORT":     L3,   # 整理
}
```

默认等级为 L3，即记录所有操作。如果你只需要追踪核心变动，建议切到 L2 或 L1：

- **L1**：只记录资产增减和交易，适合后台审计。
- **L2**：额外记录拆分/合并，适合排查堆叠问题。
- **L3**：记录全部操作（含移动、整理），适合开发调试。

### 13.4 输出类型

日志支持两种输出目标，通过 `setBagLogConfig(outputType=...)` 切换。

| 类型 | 常量 | 说明 |
| ---- | ---- | ---- |
| 数据库 | `BAG_LOG_OUTPUT_DATABASE` (1) | 写入 `kbe_plugin_bag_op_logs` 表，支持 SQL 查询与审计追溯 |
| 文件 | `BAG_LOG_OUTPUT_FILE` (2) | 写入磁盘日志文件，支持日期+大小滚动分割 |

默认输出到数据库。两种输出可以随时切换，不需要重启服务。

### 13.5 文件日志参数

当 `outputType` 设为文件时，日志会写入本地磁盘，并自动按**日期**和**文件大小**双重规则滚动分割。

| 参数 | 默认值 | 说明 |
| ---- | ------ | ---- |
| `filePath` | `logs/plugins/bag/bag.log` | 日志文件路径。目录不存在时会自动创建 |
| `maxBytes` | `10485760` (10 MB) | 单个日志文件最大字节数。超过则滚动到新文件 |
| `backupCount` | `30` | 历史日志文件最大保留数量。超出后自动删除最旧文件 |
| `encoding` | `utf-8` | 日志文件编码 |

滚动规则：

- **日期滚动**：跨天自动生成 `bag.2026-05-29.log` 格式的新文件。
- **大小滚动**：同一天内文件超过 `maxBytes` 时，追加编号为 `bag.2026-05-29.1.log`。
- **清理策略**：历史文件总数超过 `backupCount` 时，按修改时间删除最旧的文件。

日志输出格式（行业标准 key=value 管道格式）：

```text
2026-05-29 14:30:15 | INFO | bag-op|level=L1|opType=ADD|ownerDBID=10001|targetDBID=0|bid=42|...
```

### 13.6 配置 API

所有配置通过 Python 方法完成，不需要手动编辑 JSON。可以在 `plugin_entry.py`、业务脚本或任何持有 Avatar 的地方调用。

**完整配置入口：**

```python
from plugins.Bag.common.bag_service import setBagLogConfig

setBagLogConfig(
    logLevel=2,            # 只记录 L2 及以上（含 ADD/REMOVE/CLEAR/TRANSFER/SPLIT/MERGE）
    outputType=2,          # 输出到文件
    filePath="logs/plugins/bag/bag.log",
    maxBytes=10 * 1024 * 1024,   # 10 MB
    backupCount=30,
    encoding="utf-8",
)
```

**快捷方法：**

```python
from plugins.Bag.common.bag_service import (
    setBagLogLevel,
    setBagLogOutputType,
    setBagLogFileConfig,
    getBagLogConfig,
)

# 只改分级
setBagLogLevel(2)

# 只改输出类型
setBagLogOutputType(1)   # 切回数据库

# 只改文件参数
setBagLogFileConfig(
    filePath="logs/plugins/bag/bag.log",
    maxBytes=5 * 1024 * 1024,   # 5 MB
    backupCount=10,
)

# 读取当前配置
config = getBagLogConfig()
print(config)
# {"level": 2, "outputType": 1, "filePath": "logs/plugins/bag/bag.log", "maxBytes": 5242880, ...}
```

所有参数均可选传，只传需要改的项，未传项保持原值不变。

### 13.7 配置示例

**示例 1：生产环境只记数据库、只记核心变动**

```python
from plugins.Bag.common.bag_service import setBagLogConfig

setBagLogConfig(logLevel=1, outputType=1)
```

- 操作类型：ADD、REMOVE、CLEAR、TRANSFER
- 输出：数据库 `kbe_plugin_bag_op_logs` 表

**示例 2：日志落盘，完整追踪所有操作**

```python
setBagLogConfig(logLevel=3, outputType=2, filePath="logs/plugins/bag/bag.log")
```

- 操作类型：全部
- 输出：文件，自动滚动

**示例 3：开发调试时切换到文件、只记核心操作**

```python
setBagLogConfig(logLevel=2, outputType=2, filePath="logs/bag_debug.log", maxBytes=2 * 1024 * 1024)
```

- 操作类型：ADD、REMOVE、CLEAR、TRANSFER、SPLIT、MERGE
- 输出：文件，每 2 MB 滚动

## 14. 生命周期和插件入口

### 14.1 `base/plugin_entry.py`

base 侧入口会做这些事：

- 初始化检查
- 建表
- 创建 `BagManager`
- 退出时销毁 `BagManager`

### 14.2 `bots/plugin_entry.py`

bots 侧入口主要用于验证插件 common 模块可导入，方便机器人测试环境接入。

## 15. 常见使用场景

### 15.1 登录后显示背包

```python
def onClientEnabled(self):
    self.bag.requestBagList()
```

### 15.2 发放奖励

```python
def giveReward(self, avatar, itemID, count):
    bag = avatar.bag
    bag.addItem(itemID, count, 1, "", opID="reward_202605", reason="REWARD")
```

### 15.3 邮件附件

```python
bag = receiverAvatar.bag
bag.addItem(2001, 1, 0, '{"quality":"rare"}', opID="mail_1001", reason="MAIL")
```

### 15.4 多物品交易

```python
bag.transferItems(
    targetDBID,
    [
        {"bid": 101, "count": 1},
        {"bid": 102, "count": 3},
    ],
    opID="trade_9001",
    reason="TRADE"
)
```

### 15.5 关闭某个回调

```python
avatar.bag.setCallbackSwitch("error", 0)
```

## 16. 建议和限制

### 建议

- 优先用服务端 API，不要让客户端直接碰高危写接口。
- 发奖励、扣物品、交易、邮件附件，最好都带 `opID`。
- 需要稳定追踪时，把 `reason` 和 `context` 填上。
- 容量、回调开关最好用 `setCapacity()`、`setCallbackSwitch()` 统一设置。

### 限制

- `transferItems()` 目前不是同连接事务版。
- 绑定、过期、锁定字段已经有了，但业务规则还需要你自己定义。
- 客户端回调开关只是“是否通知”，不是“是否执行操作”。
- `capacity` 现在按实例条数统计，不是按重量、体积或 stack 数量统计。

## 17. 一个完整例子

```python
def onAvatarReady(avatar):
    bag = avatar.bag

    bag.setCapacity(120)
    bag.setCallbackSwitch("page", 1)
    bag.setCallbackSwitch("updated", 1)

    bag.addItem(1001, 3, 1, "", opID="login_bonus", reason="LOGIN")
    bag.addItem(2001, 1, 0, '{"atk": 12, "quality": "rare"}', opID="gift_01", reason="GIFT")

    bag.requestBagList()
```

这就是最常见的接法：先定容量，再发物品，再刷新列表。

## 18. 测试

测试代码在 `common/test/` 下，分两层：

### 18.1 纯函数测试（pytest，不依赖 KBE）

```bash
cd plugins/Bag/common/test
pytest test_bag_model.py test_bag_storage.py -v
```

| 文件 | 覆盖范围 |
|------|---------|
| `test_bag_model.py` | 数据规整：normalize_*、make_item、empty_item、page_items 等 12 个函数 |
| `test_bag_storage.py` | SQL 生成：create_table_sql、insert_op_log_sql、escape_sql_text、decode_* 等 |

### 18.2 KBE 集成测试（baseapp 运行时）

在 `base/plugin_entry.py` 的 `onComponentReady` 末尾加入：

```python
from plugins.Bag.common.test.BagServiceTest import start
start()
```

测试流程（19 步回调链）：

1. 建表 → 2. 设置容量 → 3. 读取容量 → 4-5. 添加两种物品 → 6. 全量查询 → 7. 分页查询 → 8. 单物品查询 → 9. 数量统计 → 10. 拆分 → 11. 合并 → 12. 交换 → 13. 移动 → 14. 整理 → 15. 删除 → 16. 跨玩家转移 → 17. 清空 → 18. 验证空背包 → 19. 清理目标背包

测试使用专用 DBID `99999901` / `99999902`，不会污染真实玩家数据。每一步失败会打 `ERROR_MSG` 并终止。
