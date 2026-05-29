# Bag 插件

这是一个挂载到 `Avatar` 的背包组件插件样板。

核心设计：

- `BagComponent` 是 `Avatar` 的组件，不再是独立实体。
- 背包物品列表不使用 KBE 自动持久化属性。
- 插件启动后创建独立表 `kbe_plugin_bag_items` 和 `kbe_plugin_bag_op_logs`。
- 添加、删除、拆分、交换、合并、整理、清空、分页查询都通过 `KBEngine.executeRawDatabaseCommand` 访问数据库。
- 同一 `ownerDBID` 的写操作会进入 Python 层串行队列，避免高频客户端操作导致 SQL 回调乱序。
- 插件自己的 `BagManager` base 实体会在 baseapp ready 后自动创建，用来周期性调用 `tickBagQueues()`。
- 背包容量通过组件属性 `capacity` 暴露给外部设置，`0` 表示不限制容量。
- 每个客户端回调都有独立开关：`notifyBagList`、`notifyBagUpdated`、`notifyBagPage`、`notifyBagError`。
- 客户端只允许请求全量/分页查询，通过组件回调接收全量、分页和增量数据。
- 添加、删除、清空是服务端写接口，不能 Exposed 给客户端。
- 拆分、交换、合并、整理是客户端可发起的整理类操作，但仍由服务端校验后执行。

## 文件结构

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

`Avatar.def` 里挂载：

```xml
<bag>
    <Type> BagComponent </Type>
    <Persistent> false </Persistent>
</bag>
```

## 类型

`BagItem` 是单个物品结构：

- `bid`
- `itemID`
- `count`
- `bagIndex`
- `stackable`
- `maxStack`
- `bindType`
- `expireAt`
- `locked`
- `extra`：附加属性 JSON 字符串，数据库使用 `TEXT` 保存。

`BagItems` 是 `ARRAY <of> BagItem </of>`，用于客户端全量、分页同步。

## 服务端 API

背包核心不依赖 Avatar 实体实例，assets 业务代码推荐直接按 databaseID 取得背包服务：

```python
from plugins.Bag.common.bag_service import getBagForEntityID

bag = getBagForEntityID(avatar.databaseID)
bag.addItem(1001, 3, 1)
bag.addItem(2001, 1, 0, '{"atk": 12, "quality": "rare"}')
bag.removeItem(bid, 1)
bag.splitItem(bid, 2)
bag.swapItem(bid1, bid2)
bag.moveItem(bid, bagIndex)
bag.mergeItem(fromBID, toBID)
bag.sortItems()
bag.setCapacity(120)
bag.transferItems(targetDBID, [{"bid": bid1, "count": 1}, {"bid": bid2, "count": 3}])
bag.getItemCount(1001, callback)
bag.pageItems(1, 20, callback)
```

这种方式可以覆盖在线 Avatar、离线发奖、邮件附件、商城补单和 GM 工具等场景。
所有查询接口都是异步接口，必须提供 callback；写接口可以不传 callback，但失败会写 error 日志。

## Base 组件方法

这些方法声明在 `BagComponent.def` 的 `BaseMethods` 中：

- `addItem(itemID, count, stackable=1, extra="")`：服务端调用，不暴露客户端。
- `removeItem(bid, count)`：服务端调用，不暴露客户端。
- `setCapacity(capacity)`：服务端调用，设置当前玩家背包容量；`0` 表示不限制。
- `setCallbackSwitch(callbackName, enabled)`：服务端调用，单独开关 `list`、`updated`、`page`、`error` 回调。
- `splitItem(bid, count)`：客户端可调用，服务端校验后执行。
- `swapItem(bid1, bid2)`：客户端可调用，服务端校验后执行。
- `moveItem(bid, bagIndex)`：客户端可调用，移动到空位置；目标位置已有物品时自动交换。
- `mergeItem(fromBID, toBID)`：客户端可调用，服务端校验后执行。
- `sortItems()`：客户端可调用，服务端校验后执行。
- `transferItems(targetDBID, itemsJson)`：服务端调用，多物品交易；`itemsJson` 形如 `[{"bid":1,"count":2}]`。
- `clear()`：服务端调用，不暴露客户端。
- `requestBagList()`：客户端可调用，只读查询。
- `requestBagPage(page, pageSize)`：客户端可调用，只读查询。

安全原则：客户端不能直接调用奖励发放、扣除、清空等高风险方法。拆分、合并、整理这类整理操作可以由客户端发起，但服务端必须校验物品归属、数量、是否可堆叠等条件后再执行。

## Client 回调

客户端或 bots 侧组件接收：

- `onBagList(items)`：全量背包。
- `onBagUpdated(op, index, item)`：单条增量。
- `onBagPage(page, pageSize, total, items)`：分页结果。
- `onBagError(message)`：错误提示。

`op` 约定：

- `1` 新增
- `2` 更新
- `3` 删除
- `4` 清空
- `5` 移动/交换位置
- `6` 拆分
- `7` 合并
- `8` 整理

`index` 是按 `bagIndex ASC, bid ASC` 排序后的 0 基下标。删除时 `item.count` 为 0。

## 操作日志

普通 `addItem`、`removeItem`、`splitItem`、`swapItem`、`moveItem`、`mergeItem`、`sortItems`、`clear` 成功修改背包后，会写入 `kbe_plugin_bag_op_logs`。

日志字段包含：

- `opID`：业务侧传入的操作号，可用于追踪奖励、交易、GM 指令等来源。
- `ownerDBID`：背包所属玩家。
- `targetDBID`：目标玩家，普通单人操作默认为 0。
- `opType`：`ADD`、`REMOVE`、`CLEAR` 等。
- `bid`、`targetBID`、`itemID`、`count`、`beforeCount`、`afterCount`、`beforeIndex`、`afterIndex`：物品、数量和位置变化。
- `reason`、`context`：业务原因和附加上下文。

普通操作当前不是数据库事务，背包变更成功后再写日志；如果日志写入失败，会打 `ERROR`，但不会把已经成功的背包操作回调成失败。`transferItems()` 支持一次交易多个物品，会按传入顺序执行多条 SQL；它适合样板验证和低风险内部工具，正式高价值交易后续应把源背包扣减、目标背包增加和日志写入放进同一个数据库事务。

## 写操作队列

所有公开写接口都会先进入 `ownerDBID` 维度的串行队列：

- `addItem`
- `removeItem`
- `splitItem`
- `swapItem`
- `moveItem`
- `mergeItem`
- `sortItems`
- `clear`

第一版队列粒度是“一个公开写 API 调用 = 一个 Operation”。队列不会自动合并多次拖拽或多次整理请求，因为这些操作可能对应不同日志、客户端表现和业务校验。

队列只保证同一个玩家的背包写操作按请求顺序执行；不同玩家之间仍可并发。真正的数据库事务需要 raw DB 命令在同一个数据库连接上执行，当前插件先保留 Operation 边界，后续如果引擎补充 raw transaction 接口，可以在不改变业务调用方式的前提下把单个 Operation 内部切到 `BEGIN/COMMIT/ROLLBACK`。

队列带有卡死保护：单条写操作默认 30 秒没有结束会记录 `ERROR`，给当前操作返回失败，并继续执行该玩家队列里的下一条操作。插件层提供 `tickBagQueues()` 作为 watchdog 扫描入口；同时每次新操作入队前也会主动扫描一次超时队列。迟到的 raw DB 回调会被忽略，避免同一个操作向业务层回调两次。

日志里会直接显示操作名，例如 `ADD`、`MOVE`、`SPLIT`、`MERGE`，方便从 base 日志快速定位哪一类背包操作卡住了。

## 容量和堆叠

`BagComponent.capacity` 是外部可设置的组件属性，默认 `0` 表示无限容量。调用 `setCapacity(capacity)` 会同步写入 `kbe_plugin_bag_meta`，服务层后续 `addItem()`、`splitItem()` 和 `transferItems()` 都会检查容量。

`BagItem.maxStack` 表示单格最大堆叠数量。`addItem()` 会先填充同 `itemID`、同 `extra`、未满的可堆叠格子，剩余数量再按 `maxStack` 自动拆到新格子；容量不足时会失败并回调错误。

`bindType`、`expireAt`、`locked` 已经进入 `BagItem` 和数据库表结构，方便后续业务实现绑定、限时道具和锁定保护。当前插件只负责存取和同步这些字段；真正的“禁止交易/禁止删除/过期清理”规则应由业务层或后续规则模块接入。

每个客户端回调都可以单独关闭：

```python
avatar.bag.notifyBagUpdated = 0
avatar.bag.setCallbackSwitch("page", 0)
```

## 数据库

插件在 baseapp ready 时执行：

```sql
CREATE TABLE IF NOT EXISTS kbe_plugin_bag_items (...)
CREATE TABLE IF NOT EXISTS kbe_plugin_bag_op_logs (...)
```

`kbe_plugin_bag_items` 使用 `bid BIGINT AUTO_INCREMENT` 作为主键。`ownerDBID + itemID` 不是唯一键，因为同一种物品可以拆分成多条实例记录。位置由 `bagIndex` 表示，新物品默认填充为当前最大 `bagIndex + 1`。

`stackable=1` 时，`addItem` 会优先查找同 `itemID` 且允许堆叠的实例并叠加数量；找不到才新增实例。`stackable=0` 时永远新增实例，适合装备、带随机属性的物品等。

推荐实时写数据库。背包、货币、道具这类高价值数据如果依赖引擎定时归档，baseapp 崩溃时会丢掉归档间隔内的操作。实时写库更可靠，代价是每次背包操作都会产生 SQL 压力。

后续性能优化可以做：

- 同一玩家背包操作队列化。
- 对高频整理、拆分、合并操作增加数据库事务。
- 使用事务包裹奖励发放和背包更新。
- 把高频背包服务拆成独立 inventory service。

第一版先保证可靠性：每次增删立即落库，成功后再同步客户端。
