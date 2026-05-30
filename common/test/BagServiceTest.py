# -*- coding: utf-8 -*-
"""
BagService KBE 运行时集成测试。

仿照 common/RawDatabaseTest.py 的回调链模式，在 baseapp 启动后按序执行完整背包操作流程。
每一步异步验证回调结果，失败打 ERROR_MSG 并终止后续步骤。

启动方式：
    在 plugin_entry.onComponentReady 中调用:
        from plugins.Bag.common.test.BagServiceTest import start
        start()

    或在 baseapp 初始化完成后手动调用。

测试使用专用 DBID 区间 99999901~99999902，不会污染真实玩家数据。
"""

import KBEngine
from KBEDebug import ERROR_MSG, INFO_MSG, WARNING_MSG
from plugins.Bag.common import bag_service, bag_storage

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BAG_SERVICE_TEST_ENABLED = True

TEST_OWNER_DBID = 99999901
TEST_TARGET_DBID = 99999902
TEST_ITEM_ID_A = 90001
TEST_ITEM_ID_B = 90002


def start(owner_dbid=None, target_dbid=None):
    """启动 BagService 集成测试。"""
    if not BAG_SERVICE_TEST_ENABLED:
        INFO_MSG("[BagServiceTest] 测试已关闭。")
        return

    owner = int(owner_dbid or TEST_OWNER_DBID)
    target = int(target_dbid or TEST_TARGET_DBID)
    INFO_MSG("[BagServiceTest] 开始测试：ownerDBID=%s, targetDBID=%s" % (owner, target))

    runner = _BagServiceTestRunner(owner, target)
    runner.start()


class _BagServiceTestRunner(object):
    """按序执行背包操作的回调链测试器。"""

    def __init__(self, owner_dbid, target_dbid):
        self.owner = owner_dbid
        self.target = target_dbid
        self.step = 0
        self._tracked_bid_a = 0   # 记录 ADD 的第一个 bid
        self._tracked_bid_b = 0   # 记录 ADD 的第二个 bid
        self._split_new_bid = 0   # 记录 SPLIT 产生的新 bid
        self._failed = False

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def start(self):
        self._log("1.ensureTable", "创建背包三张数据表")
        bag_service.ensureTable(self._on_ensure_table_done)

    # ------------------------------------------------------------------
    # 步骤回调
    # ------------------------------------------------------------------
    def _on_ensure_table_done(self, success, message):
        if not success:
            return self._fail("ensureTable 失败: %s" % message)
        self._pass("三张表就绪")
        self._next("2.setCapacity", "设置容量=100")
        bag_service.getBagForEntityID(self.owner).setCapacity(100, self._on_set_capacity_done)

    def _on_set_capacity_done(self, success, message):
        if not success:
            return self._fail("setCapacity 失败: %s" % message)
        self._pass()
        self._next("3.getCapacity", "读取容量")
        bag_service.getBagForEntityID(self.owner).getCapacity(self._on_get_capacity_done)

    def _on_get_capacity_done(self, success, capacity, message):
        if not success:
            return self._fail("getCapacity 失败: %s" % message)
        if capacity != 100:
            return self._fail("getCapacity 期望 100，实际 %s" % capacity)
        self._pass("capacity=%s" % capacity)
        self._next("4.addItem-A", "添加可堆叠物品 90001 x3")
        bag_service.getBagForEntityID(self.owner).addItem(
            TEST_ITEM_ID_A, 3, 1, "", self._on_add_a_done, opID="test_add_a", reason="TEST")

    def _on_add_a_done(self, success, op, index, item, message):
        if not success:
            return self._fail("addItem-A 失败: %s" % message)
        if item["itemID"] != TEST_ITEM_ID_A:
            return self._fail("addItem-A itemID 不匹配")
        self._tracked_bid_a = item["bid"]
        self._pass("bid=%s, count=%s, itemID=%s" % (item["bid"], item["count"], item["itemID"]))
        self._next("5.addItem-B", "添加不可堆叠物品 90002 x1 + extra")
        bag_service.getBagForEntityID(self.owner).addItem(
            TEST_ITEM_ID_B, 1, 0, '{"atk":12}', self._on_add_b_done, opID="test_add_b", reason="TEST")

    def _on_add_b_done(self, success, op, index, item, message):
        if not success:
            return self._fail("addItem-B 失败: %s" % message)
        if item["stackable"] != 0:
            return self._fail("addItem-B stackable 期望 0")
        self._tracked_bid_b = item["bid"]
        self._pass("bid=%s, stackable=%s, extra=%s" % (item["bid"], item["stackable"], item["extra"]))
        self._next("6.listItems", "查询完整背包")
        bag_service.getBagForEntityID(self.owner).listItems(self._on_list_done)

    def _on_list_done(self, success, items, message):
        if not success:
            return self._fail("listItems 失败: %s" % message)
        if len(items) < 2:
            return self._fail("listItems 期望 >=2 条，实际 %s" % len(items))
        self._pass("items=%s" % len(items))
        self._next("7.pageItems", "分页查询 page=1, pageSize=1")
        bag_service.getBagForEntityID(self.owner).pageItems(1, 1, self._on_page_done)

    def _on_page_done(self, success, page, page_size, total, items, message):
        if not success:
            return self._fail("pageItems 失败: %s" % message)
        if total < 2:
            return self._fail("pageItems total 期望 >=2，实际 %s" % total)
        if len(items) != 1:
            return self._fail("pageItems items 期望 1，实际 %s" % len(items))
        self._pass("page=%s, total=%s, items=%s" % (page, total, len(items)))
        self._next("8.getItem", "按 bid 查询单物品")
        bag_service.getBagForEntityID(self.owner).getItem(self._tracked_bid_a, self._on_get_item_done)

    def _on_get_item_done(self, success, item, message):
        if not success:
            return self._fail("getItem 失败: %s" % message)
        if item is None or item["bid"] != self._tracked_bid_a:
            return self._fail("getItem bid 不匹配")
        self._pass("bid=%s, itemID=%s" % (item["bid"], item["itemID"]))
        self._next("9.getItemCount", "查询 itemID=90001 总数")
        bag_service.getBagForEntityID(self.owner).getItemCount(TEST_ITEM_ID_A, self._on_item_count_done)

    def _on_item_count_done(self, success, total_count, message):
        if not success:
            return self._fail("getItemCount 失败: %s" % message)
        if total_count != 3:
            return self._fail("getItemCount 期望 3，实际 %s" % total_count)
        self._pass("count=%s" % total_count)
        self._next("10.splitItem", "拆分 bid=%s 出 1 个" % self._tracked_bid_a)
        bag_service.getBagForEntityID(self.owner).splitItem(
            self._tracked_bid_a, 1, self._on_split_done, opID="test_split", reason="TEST")

    def _on_split_done(self, success, op, index, item, message):
        if not success:
            return self._fail("splitItem 失败: %s" % message)
        if op != bag_storage.OP_SPLIT:
            return self._fail("splitItem op 期望 OP_SPLIT(%s)，实际 %s" % (bag_storage.OP_SPLIT, op))
        if item["itemID"] != TEST_ITEM_ID_A:
            return self._fail("splitItem 新物品 itemID 不匹配")
        self._split_new_bid = item["bid"]
        self._pass("新 bid=%s, itemID=%s, count=%s" % (item["bid"], item["itemID"], item["count"]))
        self._next("11.mergeItem", "合并 from=%s → to=%s" % (self._split_new_bid, self._tracked_bid_a))
        bag_service.getBagForEntityID(self.owner).mergeItem(
            self._split_new_bid, self._tracked_bid_a,
            self._on_merge_done, opID="test_merge", reason="TEST")

    def _on_merge_done(self, success, op, index, item, message):
        if not success:
            return self._fail("mergeItem 失败: %s" % message)
        if op != bag_storage.OP_MERGE:
            return self._fail("mergeItem op 期望 OP_MERGE(%s)" % bag_storage.OP_MERGE)
        self._pass("合并后 bid=%s, count=%s" % (item["bid"], item["count"]))
        self._next("12.swapItem", "交换 bid=%s ↔ bid=%s" % (self._tracked_bid_a, self._tracked_bid_b))
        bag_service.getBagForEntityID(self.owner).swapItem(
            self._tracked_bid_a, self._tracked_bid_b,
            self._on_swap_done, opID="test_swap", reason="TEST")

    def _on_swap_done(self, success, op, index, item, message):
        if not success:
            return self._fail("swapItem 失败: %s" % message)
        self._pass("交换完成")
        self._next("13.moveItem", "移动 bid=%s → bagIndex=5" % self._tracked_bid_a)
        bag_service.getBagForEntityID(self.owner).moveItem(
            self._tracked_bid_a, 5, self._on_move_done, opID="test_move", reason="TEST")

    def _on_move_done(self, success, op, index, item, message):
        if not success:
            return self._fail("moveItem 失败: %s" % message)
        if item["bagIndex"] != 5:
            return self._fail("moveItem bagIndex 期望 5，实际 %s" % item["bagIndex"])
        self._pass("bagIndex=%s" % item["bagIndex"])
        self._next("14.sortItems", "整理背包")
        bag_service.getBagForEntityID(self.owner).sortItems(
            self._on_sort_done, opID="test_sort", reason="TEST")

    def _on_sort_done(self, success, op, index, item, message):
        if not success:
            return self._fail("sortItems 失败: %s" % message)
        self._pass("整理完成")
        self._next("15.removeItem", "删除 bid=%s 全部数量" % self._tracked_bid_b)
        # 先查数量
        def _on_before_remove(success2, item2, msg2):
            if not success2 or item2 is None:
                return self._fail("removeItem 前查询失败")
            count_to_remove = item2["count"]
            bag_service.getBagForEntityID(self.owner).removeItem(
                self._tracked_bid_b, count_to_remove,
                self._on_remove_done, opID="test_remove", reason="TEST")

        bag_service.getBagForEntityID(self.owner).getItem(self._tracked_bid_b, _on_before_remove)

    def _on_remove_done(self, success, op, index, item, message):
        if not success:
            return self._fail("removeItem 失败: %s" % message)
        self._pass("删除完成")
        self._next("16.transferItems", "转移剩余物品给 targetDBID=%s" % self.target)
        # 查询当前剩余物品的 bid
        def _on_before_transfer(success2, items2, msg2):
            if not success2 or len(items2) < 1:
                return self._fail("transferItems 前查询失败或无物品")
            transfer = [{"bid": items2[0]["bid"], "count": items2[0]["count"]}]
            bag_service.getBagForEntityID(self.owner).transferItems(
                self.target, transfer,
                self._on_transfer_done, opID="test_transfer", reason="TEST")

        bag_service.getBagForEntityID(self.owner).listItems(_on_before_transfer)

    def _on_transfer_done(self, success, op, index, item, message):
        if not success:
            return self._fail("transferItems 失败: %s" % message)
        self._pass("转移完成")
        self._next("17.clear", "清空背包")
        bag_service.getBagForEntityID(self.owner).clear(
            self._on_clear_done, opID="test_clear", reason="TEST")

    def _on_clear_done(self, success, op, index, item, message):
        if not success:
            return self._fail("clear 失败: %s" % message)
        self._pass("清空完成")
        self._next("18.verifyEmpty", "验证背包已空")
        bag_service.getBagForEntityID(self.owner).listItems(self._on_verify_empty_done)

    def _on_verify_empty_done(self, success, items, message):
        if not success:
            return self._fail("verifyEmpty 失败: %s" % message)
        if len(items) != 0:
            return self._fail("背包未清空，剩余 %s 条" % len(items))
        self._pass("背包为空，全部测试通过")
        # 同步清理目标背包
        self._next("19.cleanupTarget", "清理目标背包")
        bag_service.getBagForEntityID(self.target).clear(self._on_cleanup_target_done)

    def _on_cleanup_target_done(self, success, op, index, item, message):
        if not success:
            WARNING_MSG("[BagServiceTest] 清理目标背包失败: %s" % message)
        else:
            self._pass("目标背包已清理")
        self._finish()

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _next(self, step_label, desc):
        self.step += 1
        INFO_MSG("[BagServiceTest] [%s] %s" % (step_label, desc))

    def _pass(self, detail=""):
        msg = "[BagServiceTest]   ✓ 通过"
        if detail:
            msg += " — %s" % detail
        INFO_MSG(msg)

    def _fail(self, reason):
        self._failed = True
        ERROR_MSG("[BagServiceTest]   ✗ 失败 — %s" % reason)

    def _finish(self):
        if self._failed:
            ERROR_MSG("[BagServiceTest] 测试结束：存在失败步骤。")
        else:
            INFO_MSG("[BagServiceTest] 测试结束：全部 %s 步通过。" % self.step)

    def _log(self, step_label, desc):
        INFO_MSG("[BagServiceTest] [%s] %s" % (step_label, desc))
