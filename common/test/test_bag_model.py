# -*- coding: utf-8 -*-
"""bag_model 纯函数单元测试 — 不依赖 KBE 运行时，pytest 直接跑。"""

import sys
import os
import pytest

# 确保 plugins 包在 sys.path 中
_here = os.path.dirname(os.path.abspath(__file__))
_plugins_root = os.path.normpath(os.path.join(_here, "..", "..", "..", ".."))
if _plugins_root not in sys.path:
    sys.path.insert(0, _plugins_root)

from plugins.Bag.common.bag_model import (
    normalize_bid,
    normalize_item_id,
    normalize_count,
    normalize_bag_index,
    normalize_stackable,
    normalize_max_stack,
    normalize_extra,
    make_item,
    empty_item,
    normalize_item,
    normalize_items,
    page_items,
)


# ---------------------------------------------------------------------------
# normalize_bid
# ---------------------------------------------------------------------------
class TestNormalizeBid:
    def test_positive(self):
        assert normalize_bid(42) == 42

    def test_zero(self):
        assert normalize_bid(0) == 0

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="bid must be >= 0"):
            normalize_bid(-1)

    def test_string_coerced(self):
        assert normalize_bid("5") == 5


# ---------------------------------------------------------------------------
# normalize_item_id
# ---------------------------------------------------------------------------
class TestNormalizeItemId:
    def test_positive(self):
        assert normalize_item_id(1001) == 1001

    def test_zero(self):
        assert normalize_item_id(0) == 0

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            normalize_item_id(-1)


# ---------------------------------------------------------------------------
# normalize_count
# ---------------------------------------------------------------------------
class TestNormalizeCount:
    def test_positive(self):
        assert normalize_count(10) == 10

    def test_zero(self):
        assert normalize_count(0) == 0

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            normalize_count(-5)


# ---------------------------------------------------------------------------
# normalize_bag_index
# ---------------------------------------------------------------------------
class TestNormalizeBagIndex:
    def test_positive(self):
        assert normalize_bag_index(3) == 3

    def test_zero(self):
        assert normalize_bag_index(0) == 0

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            normalize_bag_index(-1)


# ---------------------------------------------------------------------------
# normalize_stackable
# ---------------------------------------------------------------------------
class TestNormalizeStackable:
    def test_one(self):
        assert normalize_stackable(1) == 1

    def test_nonzero(self):
        assert normalize_stackable(99) == 1

    def test_zero(self):
        assert normalize_stackable(0) == 0

    def test_none(self):
        assert normalize_stackable(None) == 0


# ---------------------------------------------------------------------------
# normalize_max_stack
# ---------------------------------------------------------------------------
class TestNormalizeMaxStack:
    def test_default_when_zero(self):
        # 0 是 falsy，被 int(0 or 99) 视为"不传，用默认 99"
        assert normalize_max_stack(0) == 99

    def test_default_when_none(self):
        assert normalize_max_stack(None) == 99

    def test_clamp_negative_to_one(self):
        # -5 是 truthy，经过 < 1 检查后 clamp 到 1
        assert normalize_max_stack(-5) == 1

    def test_custom_value(self):
        assert normalize_max_stack(50) == 50

    def test_default_explicit(self):
        assert normalize_max_stack(99) == 99


# ---------------------------------------------------------------------------
# normalize_extra
# ---------------------------------------------------------------------------
class TestNormalizeExtra:
    def test_normal_json(self):
        assert normalize_extra('{"atk":12}') == '{"atk":12}'

    def test_none_becomes_empty(self):
        assert normalize_extra(None) == ""

    def test_empty_string(self):
        assert normalize_extra("") == ""

    def test_truncate_over_4096(self):
        long_str = "x" * 5000
        result = normalize_extra(long_str)
        assert len(result) == 4096
        assert result == long_str[:4096]

    def test_exactly_4096(self):
        s = "y" * 4096
        assert normalize_extra(s) == s

    def test_strips_whitespace(self):
        assert normalize_extra("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# make_item
# ---------------------------------------------------------------------------
class TestMakeItem:
    def test_full_fields(self):
        item = make_item(
            bid=10, item_id=1001, count=5, bag_index=2,
            stackable=1, max_stack=99, extra='{"q":"rare"}',
            bind_type=2, expire_at=1717000000, locked=1,
        )
        assert item["bid"] == 10
        assert item["itemID"] == 1001
        assert item["count"] == 5
        assert item["bagIndex"] == 2
        assert item["stackable"] == 1
        assert item["maxStack"] == 99
        assert item["extra"] == '{"q":"rare"}'
        assert item["bindType"] == 2
        assert item["expireAt"] == 1717000000
        assert item["locked"] == 1

    def test_defaults(self):
        item = make_item(bid=1, item_id=2001, count=1, bag_index=0)
        assert item["stackable"] == 1
        assert item["maxStack"] == 99
        assert item["extra"] == ""
        assert item["bindType"] == 0
        assert item["expireAt"] == 0
        assert item["locked"] == 0

    def test_locked_coerces_nonzero(self):
        item = make_item(bid=1, item_id=1, count=1, bag_index=0, locked=5)
        assert item["locked"] == 1

    def test_locked_zero_stays_zero(self):
        item = make_item(bid=1, item_id=1, count=1, bag_index=0, locked=0)
        assert item["locked"] == 0

    def test_max_stack_zero_uses_default(self):
        # 0 被 normalize_max_stack 视为"不传，用默认 99"
        item = make_item(bid=1, item_id=1, count=1, bag_index=0, max_stack=0)
        assert item["maxStack"] == 99

    def test_max_stack_negative_clamped(self):
        item = make_item(bid=1, item_id=1, count=1, bag_index=0, max_stack=-5)
        assert item["maxStack"] == 1


# ---------------------------------------------------------------------------
# empty_item
# ---------------------------------------------------------------------------
class TestEmptyItem:
    def test_all_zero(self):
        item = empty_item()
        assert item["bid"] == 0
        assert item["itemID"] == 0
        assert item["count"] == 0
        assert item["bagIndex"] == 0
        assert item["stackable"] == 0
        assert item["maxStack"] == 99   # 0→默认99 (int(0 or 99) = 99)
        assert item["extra"] == ""

    def test_with_bid(self):
        item = empty_item(bid=99)
        assert item["bid"] == 99


# ---------------------------------------------------------------------------
# normalize_item
# ---------------------------------------------------------------------------
class TestNormalizeItem:
    def test_cleans_all_fields(self):
        raw = {
            "bid": "5", "itemID": 1001, "count": 3, "bagIndex": 1,
            "stackable": 0, "maxStack": "50", "extra": None,
            "bindType": 1, "expireAt": 0, "locked": 0,
        }
        item = normalize_item(raw)
        assert item["bid"] == 5
        assert item["itemID"] == 1001
        assert item["count"] == 3
        assert item["stackable"] == 0
        assert item["maxStack"] == 50
        assert item["extra"] == ""

    def test_missing_keys_get_defaults(self):
        item = normalize_item({})
        assert item["bid"] == 0
        assert item["count"] == 0
        assert item["maxStack"] == 99


# ---------------------------------------------------------------------------
# normalize_items
# ---------------------------------------------------------------------------
class TestNormalizeItems:
    def test_sorts_by_bag_index_then_bid(self):
        raw = [
            {"bid": 3, "itemID": 3001, "count": 1, "bagIndex": 5},
            {"bid": 1, "itemID": 1001, "count": 2, "bagIndex": 0},
            {"bid": 2, "itemID": 2001, "count": 3, "bagIndex": 0},
        ]
        result = normalize_items(raw)
        assert [item["bid"] for item in result] == [1, 2, 3]

    def test_filters_count_zero(self):
        raw = [
            {"bid": 1, "itemID": 1001, "count": 0, "bagIndex": 0},
            {"bid": 2, "itemID": 2001, "count": 5, "bagIndex": 0},
        ]
        result = normalize_items(raw)
        assert len(result) == 1
        assert result[0]["bid"] == 2

    def test_empty_list(self):
        assert normalize_items([]) == []

    def test_none_list(self):
        assert normalize_items(None) == []


# ---------------------------------------------------------------------------
# page_items
# ---------------------------------------------------------------------------
class TestPageItems:
    def _make_items(self, n):
        return [{"bid": i, "itemID": 1000 + i, "count": 1, "bagIndex": i} for i in range(n)]

    def test_first_page(self):
        items = self._make_items(10)
        page = page_items(items, 1, 3)
        assert len(page) == 3
        assert page[0]["bid"] == 0

    def test_second_page(self):
        items = self._make_items(10)
        page = page_items(items, 2, 3)
        assert len(page) == 3
        assert page[0]["bid"] == 3

    def test_last_partial_page(self):
        items = self._make_items(10)
        page = page_items(items, 4, 3)
        assert len(page) == 1
        assert page[0]["bid"] == 9

    def test_page_zero_clamped_to_one(self):
        items = self._make_items(5)
        page = page_items(items, 0, 3)
        assert len(page) == 3

    def test_page_size_zero_clamped_to_one(self):
        items = self._make_items(5)
        page = page_items(items, 1, 0)
        assert len(page) == 1

    def test_out_of_range_returns_empty(self):
        items = self._make_items(5)
        page = page_items(items, 10, 3)
        assert page == []

    def test_sorts_before_paging(self):
        raw = [
            {"bid": 3, "itemID": 3001, "count": 1, "bagIndex": 5},
            {"bid": 1, "itemID": 1001, "count": 2, "bagIndex": 0},
        ]
        page = page_items(raw, 1, 10)
        assert page[0]["bid"] == 1
        assert page[1]["bid"] == 3
