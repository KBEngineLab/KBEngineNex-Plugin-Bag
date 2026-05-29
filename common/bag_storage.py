# -*- coding: utf-8 -*-
from plugins.Bag.common.bag_model import empty_item as make_empty_item
from plugins.Bag.common.bag_model import make_item, normalize_count, normalize_item_id
from plugins.Bag.common.bag_model import normalize_max_stack, normalize_stackable, normalize_items


# 插件表统一使用 kbe_plugin_<插件>_<业务> 的命名，避免和 assets 自身业务表冲突。
TABLE_NAME = "kbe_plugin_bag_items"
OP_LOG_TABLE_NAME = "kbe_plugin_bag_op_logs"
META_TABLE_NAME = "kbe_plugin_bag_meta"
DEFAULT_MAX_STACK = 99


OP_ADD = 1
OP_UPDATE = 2
OP_REMOVE = 3
OP_CLEAR = 4
OP_MOVE = 5
OP_SPLIT = 6
OP_MERGE = 7
OP_SORT = 8
OP_TRANSFER = 9


OP_NAME_MAP = {
    OP_ADD: "ADD",
    OP_UPDATE: "UPDATE",
    OP_REMOVE: "REMOVE",
    OP_CLEAR: "CLEAR",
    OP_MOVE: "MOVE",
    OP_SPLIT: "SPLIT",
    OP_MERGE: "MERGE",
    OP_SORT: "SORT",
    OP_TRANSFER: "TRANSFER",
}


def op_name(op):
    """把内部操作码转换成可读字符串，便于日志排查。"""
    return OP_NAME_MAP.get(int(op or 0), "UNKNOWN")


def empty_item(bid=0):
    """构造空物品，用于删除/清空这类不需要完整物品内容的增量通知。"""
    return make_empty_item(bid)


def create_table_sql():
    """创建背包实例物品表；bid 是物品实例主键，itemID 只代表配置ID。"""
    return (
        "CREATE TABLE IF NOT EXISTS `%s` ("
        "`bid` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
        "`ownerDBID` BIGINT UNSIGNED NOT NULL,"
        "`itemID` INT UNSIGNED NOT NULL,"
        "`count` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`bagIndex` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`stackable` TINYINT UNSIGNED NOT NULL DEFAULT 1,"
        "`maxStack` INT UNSIGNED NOT NULL DEFAULT 99,"
        "`bindType` TINYINT UNSIGNED NOT NULL DEFAULT 0,"
        "`expireAt` BIGINT UNSIGNED NOT NULL DEFAULT 0,"
        "`locked` TINYINT UNSIGNED NOT NULL DEFAULT 0,"
        "`extra` TEXT NULL,"
        "`updatedAt` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "PRIMARY KEY (`bid`),"
        "KEY `idx_owner_index` (`ownerDBID`, `bagIndex`),"
        "KEY `idx_owner_item` (`ownerDBID`, `itemID`),"
        "KEY `idx_owner_item_stack` (`ownerDBID`, `itemID`, `stackable`),"
        "KEY `idx_owner_bid` (`ownerDBID`, `bid`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    ) % TABLE_NAME


def create_meta_table_sql():
    """创建背包元数据表；当前用于保存每个玩家的背包容量。"""
    return (
        "CREATE TABLE IF NOT EXISTS `%s` ("
        "`ownerDBID` BIGINT UNSIGNED NOT NULL,"
        "`capacity` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`updatedAt` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "PRIMARY KEY (`ownerDBID`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    ) % META_TABLE_NAME


def select_capacity_sql(owner_dbid):
    """查询玩家背包容量；0 表示不限制容量。"""
    return "SELECT COALESCE(`capacity`, 0) FROM `%s` WHERE ownerDBID=%s LIMIT 1" % (
        META_TABLE_NAME, int(owner_dbid))


def upsert_capacity_sql(owner_dbid, capacity):
    """设置玩家背包容量；0 表示不限制容量。"""
    return (
        "INSERT INTO `%s` (`ownerDBID`, `capacity`) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE `capacity`=VALUES(`capacity`)"
    ) % (META_TABLE_NAME, int(owner_dbid), int(capacity))


def create_op_log_table_sql():
    """创建背包操作日志表。"""
    return (
        "CREATE TABLE IF NOT EXISTS `%s` ("
        "`logID` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
        "`opID` VARCHAR(64) NOT NULL DEFAULT '',"
        "`ownerDBID` BIGINT UNSIGNED NOT NULL,"
        "`targetDBID` BIGINT UNSIGNED NOT NULL DEFAULT 0,"
        "`opType` VARCHAR(32) NOT NULL,"
        "`bid` BIGINT UNSIGNED NOT NULL DEFAULT 0,"
        "`targetBID` BIGINT UNSIGNED NOT NULL DEFAULT 0,"
        "`itemID` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`count` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`beforeCount` INT UNSIGNED NULL,"
        "`afterCount` INT UNSIGNED NULL,"
        "`beforeIndex` INT UNSIGNED NULL,"
        "`afterIndex` INT UNSIGNED NULL,"
        "`status` VARCHAR(16) NOT NULL DEFAULT 'DONE',"
        "`reason` VARCHAR(64) NOT NULL DEFAULT '',"
        "`context` TEXT NULL,"
        "`createdAt` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "PRIMARY KEY (`logID`),"
        "KEY `idx_owner_time` (`ownerDBID`, `createdAt`),"
        "KEY `idx_op_id` (`opID`),"
        "KEY `idx_bid` (`bid`),"
        "KEY `idx_item` (`itemID`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    ) % OP_LOG_TABLE_NAME


def insert_op_log_sql(owner_dbid, op_type, bid=0, item_id=0, count=0, before_count=None, after_count=None,
                      before_index=None, after_index=None, target_dbid=0, target_bid=0, op_id="",
                      status="DONE", reason="", context=""):
    """构造背包操作日志 SQL。"""
    before_count_value = "NULL" if before_count is None else str(int(before_count))
    after_count_value = "NULL" if after_count is None else str(int(after_count))
    before_index_value = "NULL" if before_index is None else str(int(before_index))
    after_index_value = "NULL" if after_index is None else str(int(after_index))
    return (
        "INSERT INTO `%s` "
        "(`opID`, `ownerDBID`, `targetDBID`, `opType`, `bid`, `targetBID`, `itemID`, `count`, "
        "`beforeCount`, `afterCount`, `beforeIndex`, `afterIndex`, `status`, `reason`, `context`) "
        "VALUES ('%s', %s, %s, '%s', %s, %s, %s, %s, %s, %s, %s, %s, '%s', '%s', '%s')"
    ) % (
        OP_LOG_TABLE_NAME,
        escape_sql_text(op_id),
        int(owner_dbid),
        int(target_dbid),
        escape_sql_text(op_type),
        int(bid),
        int(target_bid),
        int(item_id),
        int(count),
        before_count_value,
        after_count_value,
        before_index_value,
        after_index_value,
        escape_sql_text(status),
        escape_sql_text(reason),
        escape_sql_text(context),
    )


def select_all_sql(owner_dbid):
    """查询某个玩家的完整背包。"""
    return (
        "SELECT bid, itemID, count, bagIndex, stackable, maxStack, COALESCE(extra, ''), bindType, expireAt, locked "
        "FROM `%s` WHERE ownerDBID=%s ORDER BY bagIndex ASC, bid ASC"
    ) % (TABLE_NAME, int(owner_dbid))


def select_page_sql(owner_dbid, page, page_size):
    """分页查询某个玩家的背包。page 从 1 开始。"""
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    offset = (page - 1) * page_size
    return (
        "SELECT bid, itemID, count, bagIndex, stackable, maxStack, COALESCE(extra, ''), bindType, expireAt, locked "
        "FROM `%s` WHERE ownerDBID=%s ORDER BY bagIndex ASC, bid ASC LIMIT %s OFFSET %s"
    ) % (TABLE_NAME, int(owner_dbid), page_size, offset)


def count_sql(owner_dbid):
    """查询某个玩家背包实例物品数量，用于分页 total。"""
    return "SELECT COUNT(*) FROM `%s` WHERE ownerDBID=%s" % (TABLE_NAME, int(owner_dbid))


def used_slots_sql(owner_dbid):
    """查询玩家当前已占用背包格子数；每条实例物品记录占一个格子。"""
    return "SELECT COUNT(*) FROM `%s` WHERE ownerDBID=%s AND `count`>0" % (TABLE_NAME, int(owner_dbid))


def select_capacity_and_used_sql(owner_dbid):
    """查询容量和已占格子数；capacity=0 表示不限制。"""
    return (
        "SELECT COALESCE((SELECT `capacity` FROM `%s` WHERE ownerDBID=%s LIMIT 1), 0), "
        "(SELECT COUNT(*) FROM `%s` WHERE ownerDBID=%s AND `count`>0)"
    ) % (META_TABLE_NAME, int(owner_dbid), TABLE_NAME, int(owner_dbid))


def total_item_count_sql(owner_dbid, item_id):
    """查询某个 itemID 的堆叠总数。"""
    return (
        "SELECT COALESCE(SUM(`count`), 0) FROM `%s` WHERE ownerDBID=%s AND itemID=%s"
    ) % (TABLE_NAME, int(owner_dbid), int(item_id))


def select_one_by_bid_sql(owner_dbid, bid):
    """按实例 bid 查询单个物品。"""
    return (
        "SELECT bid, itemID, count, bagIndex, stackable, maxStack, COALESCE(extra, ''), bindType, expireAt, locked "
        "FROM `%s` WHERE ownerDBID=%s AND bid=%s LIMIT 1"
    ) % (TABLE_NAME, int(owner_dbid), int(bid))


def select_one_by_bag_index_sql(owner_dbid, bag_index):
    """按背包位置查询该格子上的物品；空格子会返回空结果。"""
    return (
        "SELECT bid, itemID, count, bagIndex, stackable, maxStack, COALESCE(extra, ''), bindType, expireAt, locked "
        "FROM `%s` WHERE ownerDBID=%s AND bagIndex=%s ORDER BY bid ASC LIMIT 1"
    ) % (TABLE_NAME, int(owner_dbid), int(bag_index))


def select_index_by_bag_index_sql(owner_dbid, bag_index):
    """按 bagIndex 计算客户端列表下标；返回 0 基下标。"""
    return (
        "SELECT COUNT(*) FROM `%s` WHERE ownerDBID=%s AND bagIndex<%s"
    ) % (TABLE_NAME, int(owner_dbid), int(bag_index))


def select_stackable_item_sql(owner_dbid, item_id, extra=""):
    """查询可堆叠的同 itemID 实例，addItem 会优先叠到最靠前的一条。"""
    return (
        "SELECT bid, itemID, count, bagIndex, stackable, maxStack, COALESCE(extra, ''), bindType, expireAt, locked "
        "FROM `%s` WHERE ownerDBID=%s AND itemID=%s AND stackable=1 "
        "AND COALESCE(extra, '')='%s' AND `count`<`maxStack` "
        "ORDER BY bagIndex ASC, bid ASC LIMIT 1"
    ) % (TABLE_NAME, int(owner_dbid), int(item_id), escape_sql_text(extra))


def update_stack_item_sql(owner_dbid, bid, count):
    """向一个已存在的可堆叠实例增加数量。"""
    return (
        "UPDATE `%s` SET `count`=LEAST(`count`+%s, `maxStack`) "
        "WHERE ownerDBID=%s AND bid=%s AND stackable=1 AND `count`<`maxStack`"
    ) % (TABLE_NAME, int(count), int(owner_dbid), int(bid))


def insert_item_sql(owner_dbid, item_id, count, stackable=1, extra="", max_stack=DEFAULT_MAX_STACK,
                    bind_type=0, expire_at=0, locked=0):
    """新增一条实例物品，bagIndex 默认填充为当前最大 bagIndex + 1。"""
    item_id = normalize_item_id(item_id)
    count = normalize_count(count)
    stackable = normalize_stackable(stackable)
    max_stack = normalize_max_stack(max_stack)
    return (
        "INSERT INTO `%s` "
        "(`ownerDBID`, `itemID`, `count`, `bagIndex`, `stackable`, `maxStack`, `bindType`, `expireAt`, `locked`, `extra`) "
        "SELECT %s, %s, %s, COALESCE(MAX(`bagIndex`) + 1, 0), %s, %s, %s, %s, %s, '%s' "
        "FROM `%s` WHERE ownerDBID=%s"
    ) % (
        TABLE_NAME, int(owner_dbid), item_id, count, stackable, max_stack,
        int(bind_type or 0), int(expire_at or 0), 1 if int(locked or 0) else 0,
        escape_sql_text(extra), TABLE_NAME, int(owner_dbid))


def last_insert_item_sql(owner_dbid):
    """查询当前连接最后插入的实例物品；依赖 executeRawDatabaseCommand 回调中的 insertid 更优先。"""
    return select_one_by_bid_sql(owner_dbid, "LAST_INSERT_ID()")


def remove_item_by_bid_sql(owner_dbid, bid, count):
    """按 bid 扣减数量；不会扣成负数。"""
    return (
        "UPDATE `%s` SET `count`=IF(`count`>%s, `count`-%s, 0) "
        "WHERE ownerDBID=%s AND bid=%s"
    ) % (TABLE_NAME, int(count), int(count), int(owner_dbid), int(bid))


def delete_zero_item_by_bid_sql(owner_dbid, bid):
    """删除数量已经为 0 的实例物品行。"""
    return (
        "DELETE FROM `%s` WHERE ownerDBID=%s AND bid=%s AND `count`=0"
    ) % (TABLE_NAME, int(owner_dbid), int(bid))


def clear_sql(owner_dbid):
    """清空某个玩家的背包。"""
    return "DELETE FROM `%s` WHERE ownerDBID=%s" % (TABLE_NAME, int(owner_dbid))


def split_source_sql(owner_dbid, bid, count):
    """拆分物品第一步：从源实例扣除拆分数量，必须保留至少 1 个在源实例中。"""
    return (
        "UPDATE `%s` SET `count`=`count`-%s "
        "WHERE ownerDBID=%s AND bid=%s AND `count`>%s"
    ) % (TABLE_NAME, int(count), int(owner_dbid), int(bid), int(count))


def insert_split_item_sql(owner_dbid, source_item, count):
    """拆分物品第二步：按源实例 itemID/extra 新增一个实例，位置放到末尾。"""
    return insert_item_sql(owner_dbid, source_item["itemID"], count,
                           source_item.get("stackable", 1), source_item.get("extra", ""),
                           source_item.get("maxStack", DEFAULT_MAX_STACK),
                           source_item.get("bindType", 0), source_item.get("expireAt", 0),
                           source_item.get("locked", 0))


def update_bag_index_sql(owner_dbid, bid, bag_index):
    """更新单个实例物品的背包位置。"""
    return (
        "UPDATE `%s` SET `bagIndex`=%s WHERE ownerDBID=%s AND bid=%s"
    ) % (TABLE_NAME, int(bag_index), int(owner_dbid), int(bid))


def merge_items_sql(owner_dbid, from_bid, to_bid):
    """合并物品第一步：把 from_bid 数量加到 to_bid，要求 itemID/extra 一致且不超过 maxStack。"""
    return (
        "UPDATE `%s` dst JOIN `%s` src "
        "ON dst.ownerDBID=src.ownerDBID AND dst.ownerDBID=%s AND dst.bid=%s AND src.bid=%s "
        "AND dst.itemID=src.itemID AND dst.stackable=1 AND src.stackable=1 "
        "AND COALESCE(dst.extra, '')=COALESCE(src.extra, '') "
        "AND dst.`count`+src.`count`<=dst.`maxStack` "
        "SET dst.`count`=dst.`count`+src.`count`"
    ) % (TABLE_NAME, TABLE_NAME, int(owner_dbid), int(to_bid), int(from_bid))


def update_item_owner_sql(owner_dbid, target_dbid, bid):
    """把完整物品实例转移给另一个 owner，位置放到目标背包末尾。"""
    return (
        "UPDATE `%s` SET ownerDBID=%s, "
        "bagIndex=(SELECT nextIndex FROM (SELECT COALESCE(MAX(bagIndex)+1, 0) AS nextIndex "
        "FROM `%s` WHERE ownerDBID=%s) t) "
        "WHERE ownerDBID=%s AND bid=%s"
    ) % (TABLE_NAME, int(target_dbid), TABLE_NAME, int(target_dbid), int(owner_dbid), int(bid))


def delete_item_by_bid_sql(owner_dbid, bid):
    """按 bid 删除实例物品。"""
    return "DELETE FROM `%s` WHERE ownerDBID=%s AND bid=%s" % (TABLE_NAME, int(owner_dbid), int(bid))


def select_sort_items_sql(owner_dbid):
    """整理背包时读取排序后的实例列表。"""
    return (
        "SELECT bid, itemID, count, bagIndex, stackable, maxStack, COALESCE(extra, ''), bindType, expireAt, locked "
        "FROM `%s` WHERE ownerDBID=%s ORDER BY itemID ASC, bid ASC"
    ) % (TABLE_NAME, int(owner_dbid))


def decode_items(result):
    """把 raw DB 查询结果转换成 BagItems 兼容列表。"""
    items = []
    for row in result or []:
        if len(row) < 7:
            continue

        items.append(make_item(
            cell_int(row[0]),
            cell_int(row[1]),
            cell_int(row[2]),
            cell_int(row[3]),
            cell_int(row[4]),
            cell_int(row[5]),
            cell_text(row[6]),
            cell_int(row[7]) if len(row) > 7 else 0,
            cell_int(row[8]) if len(row) > 8 else 0,
            cell_int(row[9]) if len(row) > 9 else 0,
        ))
    return normalize_items(items)


def decode_first_item(result):
    """从查询结果中读取第一条物品；没有结果时返回 None。"""
    items = decode_items(result)
    return items[0] if items else None


def decode_first_int(result, default=0):
    """从 raw DB 查询结果的第一行第一列读取整数。"""
    if not result or not result[0] or result[0][0] is None:
        return default
    return cell_int(result[0][0])


def cell_text(value):
    """兼容 MySQL bytes 和 PostgreSQL str 两类 raw command 返回值。"""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def cell_int(value):
    """把数据库字段转换成 int。"""
    return int(cell_text(value))


def escape_sql_text(value):
    """最小 SQL 字符串转义；当前插件默认面向 MySQL raw command。"""
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")
