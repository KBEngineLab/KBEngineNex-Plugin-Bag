# -*- coding: utf-8 -*-
"""bag_storage SQL 生成与结果解码单元测试 — 不依赖 KBE 运行时，pytest 直接跑。"""

import sys
import os
import pytest

_here = os.path.dirname(os.path.abspath(__file__))
_plugins_root = os.path.normpath(os.path.join(_here, "..", "..", "..", ".."))
if _plugins_root not in sys.path:
    sys.path.insert(0, _plugins_root)

from plugins.Bag.common.bag_storage import (
    TABLE_NAME,
    OP_LOG_TABLE_NAME,
    META_TABLE_NAME,
    OP_ADD, OP_UPDATE, OP_REMOVE, OP_CLEAR,
    OP_MOVE, OP_SPLIT, OP_MERGE, OP_SORT, OP_TRANSFER,
    op_name,
    create_table_sql,
    create_op_log_table_sql,
    create_meta_table_sql,
    insert_op_log_sql,
    insert_item_sql,
    remove_item_by_bid_sql,
    delete_zero_item_by_bid_sql,
    clear_sql,
    split_source_sql,
    merge_items_sql,
    update_item_owner_sql,
    update_bag_index_sql,
    select_all_sql,
    select_one_by_bid_sql,
    select_stackable_item_sql,
    select_capacity_sql,
    select_capacity_and_used_sql,
    upsert_capacity_sql,
    escape_sql_text,
    decode_items,
    decode_first_item,
    decode_first_int,
    cell_int,
    cell_text,
)


OWNER = 10001
TARGET = 10002
BID = 42


# ---------------------------------------------------------------------------
# op_name
# ---------------------------------------------------------------------------
class TestOpName:
    def test_known_ops(self):
        assert op_name(OP_ADD) == "ADD"
        assert op_name(OP_UPDATE) == "UPDATE"
        assert op_name(OP_REMOVE) == "REMOVE"
        assert op_name(OP_CLEAR) == "CLEAR"
        assert op_name(OP_MOVE) == "MOVE"
        assert op_name(OP_SPLIT) == "SPLIT"
        assert op_name(OP_MERGE) == "MERGE"
        assert op_name(OP_SORT) == "SORT"
        assert op_name(OP_TRANSFER) == "TRANSFER"

    def test_unknown(self):
        assert op_name(999) == "UNKNOWN"
        assert op_name(None) == "UNKNOWN"


# ---------------------------------------------------------------------------
# create_table_sql
# ---------------------------------------------------------------------------
class TestCreateTableSql:
    def test_contains_table_name(self):
        sql = create_table_sql()
        assert TABLE_NAME in sql
        assert "CREATE TABLE IF NOT EXISTS" in sql

    def test_has_primary_key(self):
        sql = create_table_sql()
        assert "PRIMARY KEY (`bid`)" in sql

    def test_has_indexes(self):
        sql = create_table_sql()
        assert "idx_owner_index" in sql
        assert "idx_owner_item" in sql
        assert "idx_owner_item_stack" in sql
        assert "idx_owner_bid" in sql

    def test_engine_innodb(self):
        sql = create_table_sql()
        assert "ENGINE=InnoDB" in sql


class TestCreateOpLogTableSql:
    def test_contains_table_name(self):
        sql = create_op_log_table_sql()
        assert OP_LOG_TABLE_NAME in sql

    def test_has_log_id_primary_key(self):
        sql = create_op_log_table_sql()
        assert "PRIMARY KEY (`logID`)" in sql

    def test_has_op_id_index(self):
        sql = create_op_log_table_sql()
        assert "KEY `idx_op_id`" in sql


class TestCreateMetaTableSql:
    def test_contains_table_name(self):
        sql = create_meta_table_sql()
        assert META_TABLE_NAME in sql

    def test_owner_dbid_is_primary(self):
        sql = create_meta_table_sql()
        assert "PRIMARY KEY (`ownerDBID`)" in sql


# ---------------------------------------------------------------------------
# insert_op_log_sql
# ---------------------------------------------------------------------------
class TestInsertOpLogSql:
    def test_all_fields_non_null(self):
        sql = insert_op_log_sql(
            owner_dbid=OWNER, op_type="ADD", bid=BID, item_id=1001, count=3,
            before_count=5, after_count=8, before_index=2, after_index=3,
            target_dbid=TARGET, target_bid=99,
            op_id="trade_001", status="DONE", reason="TRADE", context="ctx",
        )
        assert "INSERT INTO" in sql
        assert OP_LOG_TABLE_NAME in sql
        assert "trade_001" in sql
        assert "DONE" in sql
        assert "TRADE" in sql

    def test_null_counts_output_null_keyword(self):
        sql = insert_op_log_sql(
            owner_dbid=OWNER, op_type="REMOVE", bid=BID, item_id=0, count=1,
        )
        # before_count/after_count/before_index/after_index should be NULL
        assert "NULL" in sql

    def test_escapes_single_quote(self):
        sql = insert_op_log_sql(
            owner_dbid=OWNER, op_type="ADD", bid=BID, item_id=1001, count=1,
            reason="test'reason",
        )
        assert "test\\'reason" in sql or "test''reason" in sql


# ---------------------------------------------------------------------------
# insert_item_sql
# ---------------------------------------------------------------------------
class TestInsertItemSql:
    def test_subquery_for_bag_index(self):
        sql = insert_item_sql(OWNER, 1001, 3)
        assert "COALESCE(MAX(`bagIndex`) + 1, 0)" in sql

    def test_includes_stackable_and_max_stack(self):
        sql = insert_item_sql(OWNER, 1001, 3, stackable=1, max_stack=50)
        assert "50" in sql  # maxStack value


# ---------------------------------------------------------------------------
# remove / delete / clear SQL
# ---------------------------------------------------------------------------
class TestRemoveItemSql:
    def test_uses_if_for_no_negative(self):
        sql = remove_item_by_bid_sql(OWNER, BID, 5)
        assert "IF(`count`>5, `count`-5, 0)" in sql

    def test_includes_owner_and_bid(self):
        sql = remove_item_by_bid_sql(OWNER, BID, 1)
        assert str(OWNER) in sql
        assert str(BID) in sql


class TestDeleteZeroItemSql:
    def test_requires_count_zero(self):
        sql = delete_zero_item_by_bid_sql(OWNER, BID)
        assert "`count`=0" in sql


class TestClearSql:
    def test_deletes_all_for_owner(self):
        sql = clear_sql(OWNER)
        assert "DELETE FROM" in sql
        assert str(OWNER) in sql


# ---------------------------------------------------------------------------
# split / merge / transfer SQL
# ---------------------------------------------------------------------------
class TestSplitSourceSql:
    def test_requires_count_gt_split(self):
        sql = split_source_sql(OWNER, BID, 3)
        assert "`count`>`count`-3" not in sql
        assert "`count`-3" in sql
        assert "`count`>3" in sql


class TestMergeItemsSql:
    def test_join_conditions(self):
        sql = merge_items_sql(OWNER, 10, 20)
        assert "JOIN" in sql
        assert "dst.itemID=src.itemID" in sql
        assert "dst.stackable=1" in sql
        assert "COALESCE(dst.extra, '')=COALESCE(src.extra, '')" in sql
        assert "dst.`count`+src.`count`<=dst.`maxStack`" in sql


class TestUpdateItemOwnerSql:
    def test_target_dbid_in_set(self):
        sql = update_item_owner_sql(OWNER, TARGET, BID)
        assert str(TARGET) in sql
        assert "SET ownerDBID" in sql


class TestUpdateBagIndexSql:
    def test_sets_bag_index(self):
        sql = update_bag_index_sql(OWNER, BID, 7)
        assert "`bagIndex`=7" in sql


# ---------------------------------------------------------------------------
# select SQLs
# ---------------------------------------------------------------------------
class TestSelectSqls:
    def test_select_all_orders_by_index_then_bid(self):
        sql = select_all_sql(OWNER)
        assert "ORDER BY bagIndex ASC, bid ASC" in sql

    def test_select_one_by_bid(self):
        sql = select_one_by_bid_sql(OWNER, BID)
        assert "LIMIT 1" in sql
        assert str(BID) in sql

    def test_select_stackable(self):
        sql = select_stackable_item_sql(OWNER, 1001, '{"a":1}')
        assert "stackable=1" in sql
        assert "`count`<`maxStack`" in sql

    def test_select_capacity(self):
        sql = select_capacity_sql(OWNER)
        assert "COALESCE(`capacity`, 0)" in sql

    def test_select_capacity_and_used(self):
        sql = select_capacity_and_used_sql(OWNER)
        assert "COUNT(*)" in sql

    def test_upsert_capacity(self):
        sql = upsert_capacity_sql(OWNER, 120)
        assert "ON DUPLICATE KEY UPDATE" in sql
        assert "120" in sql


# ---------------------------------------------------------------------------
# escape_sql_text
# ---------------------------------------------------------------------------
class TestEscapeSqlText:
    def test_single_quote(self):
        assert "\\'" in escape_sql_text("it's")

    def test_backslash(self):
        escaped = escape_sql_text("a\\b")
        assert "\\\\" in escaped

    def test_empty(self):
        assert escape_sql_text("") == ""

    def test_none(self):
        assert escape_sql_text(None) == ""


# ---------------------------------------------------------------------------
# cell_text / cell_int
# ---------------------------------------------------------------------------
class TestCellText:
    def test_bytes_decoded(self):
        assert cell_text(b"hello") == "hello"

    def test_str_unchanged(self):
        assert cell_text("hello") == "hello"

    def test_utf8_bytes(self):
        # b'\xe4\xb8\xad' 是 "中" 的 UTF-8 编码
        assert cell_text(b'\xe4\xb8\xad') == '\u4e2d'


class TestCellInt:
    def test_int_string(self):
        assert cell_int("42") == 42

    def test_bytes_int(self):
        assert cell_int(b"42") == 42


# ---------------------------------------------------------------------------
# decode_items / decode_first_item / decode_first_int
# ---------------------------------------------------------------------------
class TestDecodeItems:
    def test_normal_rows(self):
        result = [
            [b"1", b"1001", b"5", b"0", b"1", b"99", b'{"q":"rare"}', b"0", b"0", b"0"],
        ]
        items = decode_items(result)
        assert len(items) == 1
        item = items[0]
        assert item["bid"] == 1
        assert item["itemID"] == 1001
        assert item["count"] == 5

    def test_short_rows_skipped(self):
        result = [[b"1"]]  # only 1 column
        items = decode_items(result)
        assert items == []

    def test_empty_result(self):
        assert decode_items(None) == []

    def test_sorts_result(self):
        result = [
            [b"2", b"2001", b"1", b"5", b"1", b"99", b"", b"0", b"0", b"0"],
            [b"1", b"1001", b"1", b"0", b"1", b"99", b"", b"0", b"0", b"0"],
        ]
        items = decode_items(result)
        assert items[0]["bid"] == 1
        assert items[1]["bid"] == 2


class TestDecodeFirstItem:
    def test_returns_first(self):
        result = [
            [b"1", b"1001", b"3", b"0", b"1", b"99", b"", b"0", b"0", b"0"],
            [b"2", b"2001", b"1", b"1", b"1", b"99", b"", b"0", b"0", b"0"],
        ]
        item = decode_first_item(result)
        assert item["bid"] == 1

    def test_empty_returns_none(self):
        assert decode_first_item([]) is None
        assert decode_first_item(None) is None


class TestDecodeFirstInt:
    def test_returns_int(self):
        assert decode_first_int([["42"]]) == 42

    def test_empty_returns_default(self):
        assert decode_first_int([], default=-1) == -1

    def test_none_returns_default(self):
        assert decode_first_int(None) == 0

    def test_null_cell_returns_default(self):
        assert decode_first_int([[None]]) == 0
