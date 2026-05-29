# -*- coding: utf-8 -*-


def normalize_bid(bid):
    """把背包物品实例ID规整成非负整数；bid 对应数据库自增主键。"""
    bid = int(bid)
    if bid < 0:
        raise ValueError("bid must be >= 0")
    return bid


def normalize_item_id(item_id):
    """把物品配置ID规整成非负整数，itemID 不再作为数据库唯一键。"""
    item_id = int(item_id)
    if item_id < 0:
        raise ValueError("item_id must be >= 0")
    return item_id


def normalize_count(count):
    """把堆叠数量规整成非负整数。"""
    count = int(count)
    if count < 0:
        raise ValueError("count must be >= 0")
    return count


def normalize_bag_index(bag_index):
    """把背包格子下标规整成非负整数。"""
    bag_index = int(bag_index)
    if bag_index < 0:
        raise ValueError("bag_index must be >= 0")
    return bag_index


def normalize_stackable(stackable):
    """规整是否允许堆叠；1 表示可堆叠，0 表示实例独立存在。"""
    return 1 if int(stackable or 0) else 0


def normalize_max_stack(max_stack):
    """规整物品堆叠上限；最小值为 1，默认 99。"""
    max_stack = int(max_stack or 99)
    if max_stack < 1:
        max_stack = 1
    return max_stack


def normalize_extra(value):
    """规整附加属性 JSON 字符串；服务层只存取，不解析业务结构。"""
    value = str(value or "").strip()
    if len(value) > 4096:
        return value[:4096]
    return value


def make_item(bid, item_id, count, bag_index, stackable=1, max_stack=99, extra="",
              bind_type=0, expire_at=0, locked=0):
    """构造一个符合 BagItem FIXED_DICT 字段的普通 dict。"""
    return {
        "bid": normalize_bid(bid),
        "itemID": normalize_item_id(item_id),
        "count": normalize_count(count),
        "bagIndex": normalize_bag_index(bag_index),
        "stackable": normalize_stackable(stackable),
        "maxStack": normalize_max_stack(max_stack),
        "extra": normalize_extra(extra),
        "bindType": int(bind_type or 0),
        "expireAt": int(expire_at or 0),
        "locked": 1 if int(locked or 0) else 0,
    }


def empty_item(bid=0):
    """构造空物品，用于删除/清空等增量通知。"""
    return make_item(bid, 0, 0, 0, 0, 0, "")


def normalize_item(item):
    """规整一个已存在的物品 dict，常用于查询结果或外部传参后的兜底清洗。"""
    item = dict(item or {})
    return make_item(
        item.get("bid", 0),
        item.get("itemID", 0),
        item.get("count", 0),
        item.get("bagIndex", 0),
        item.get("stackable", 1),
        item.get("maxStack", 99),
        item.get("extra", ""),
        item.get("bindType", 0),
        item.get("expireAt", 0),
        item.get("locked", 0),
    )


def normalize_items(items):
    """
    规整背包列表。

    每条记录都是独立物品实例，不再按 itemID 合并；排序使用 bagIndex，其次 bid，保证客户端展示稳定。
    """
    result = []
    for raw_item in list(items or []):
        item = normalize_item(raw_item)
        if item["count"] > 0:
            result.append(item)

    result.sort(key=lambda x: (x["bagIndex"], x["bid"]))
    return result


def page_items(items, page, page_size):
    """对内存中的背包列表分页，page 从 1 开始。"""
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    normalized = normalize_items(items)
    begin = (page - 1) * page_size
    end = begin + page_size
    return normalized[begin:end]
