# -*- coding: utf-8 -*-
from collections import deque
import json
import time
from KBEDebug import ERROR_MSG, INFO_MSG
from plugins.Bag.common import bag_storage


_operation_queues = {}
OPERATION_TIMEOUT_SECONDS = 30.0

try:
    basestring
except NameError:
    basestring = str


def getBagForEntityID(owner_dbid):
    """
    通过实体数据库ID获取背包服务对象。

    服务对象只绑定 databaseID，不持有 Avatar 引用，因此可用于在线和离线背包操作。
    """
    return Bag(owner_dbid)


def noopUpdateCallback(success, op, index, item, message):
    """默认写操作回调；调用方不关心结果时，失败仍会写日志。"""
    if not success:
        ERROR_MSG(message)


def safeEmptyItem(bid=0):
    """构造空物品的兜底版本；客户端/脚本传入非法 bid 时也不能让入队阶段抛异常。"""
    try:
        return bag_storage.empty_item(bid)
    except Exception:
        return bag_storage.empty_item()


def ensureTable(callback=None):
    """创建背包插件需要的数据表。"""
    def _on_meta_table_done(result, rows, insertid, error):
        if error:
            message = "BagService.ensureTable meta table failed: %s" % error
            ERROR_MSG(message)
            if callback:
                callback(False, message)
            return

        INFO_MSG("BagService.ensureTable: tables ready: %s, %s, %s" % (
            bag_storage.TABLE_NAME, bag_storage.OP_LOG_TABLE_NAME, bag_storage.META_TABLE_NAME))
        if callback:
            callback(True, "")

    def _on_op_log_table_done(result, rows, insertid, error):
        if error:
            message = "BagService.ensureTable op log table failed: %s" % error
            ERROR_MSG(message)
            if callback:
                callback(False, message)
            return

        executeRaw(bag_storage.create_meta_table_sql(), _on_meta_table_done)

    def _on_item_table_done(result, rows, insertid, error):
        if error:
            message = "BagService.ensureTable item table failed: %s" % error
            ERROR_MSG(message)
            if callback:
                callback(False, message)
            return

        executeRaw(bag_storage.create_op_log_table_sql(), _on_op_log_table_done)

    executeRaw(bag_storage.create_table_sql(), _on_item_table_done)


def executeRaw(sql, callback):
    """
    统一执行背包 raw DB 命令。

    这里故意不传 threadID：同一玩家的写顺序已经由 Python 层 BagOperationQueue 保证。
    如果固定 threadID，所有玩家背包 SQL 会被 dbmgr 放进同一条队列，容易把无关玩家互相阻塞。
    dbInterfaceName 也不显式传入，交给引擎使用 kbengine_defaults.xml 中配置的 default 接口。
    """
    import KBEngine
    KBEngine.executeRawDatabaseCommand(sql, callback)


class BagWriteOperation(object):
    """
    背包写操作队列里的最小执行单元。

    第一版采用“一个公开写 API 调用 = 一个 Operation”的粒度，不自动把多次调用合并成批次。
    这样可以保证客户端高频拖拽、拆分、合并时，同一个 ownerDBID 的 SQL 回调顺序和请求顺序一致。
    """

    def __init__(self, owner_dbid, name, runner, fail_callback, fail_op, fail_item,
                 timeout_seconds=OPERATION_TIMEOUT_SECONDS):
        self.owner_dbid = int(owner_dbid or 0)
        self.name = name
        self.runner = runner
        self.fail_callback = fail_callback
        self.fail_op = fail_op
        self.fail_item = fail_item
        self.timeout_seconds = float(timeout_seconds or 0)
        self.started_at = 0.0
        self.done = None
        self.finished = False
        self.timed_out = False

    def execute(self, done):
        """执行写操作；无论成功失败，最终都必须调用 done 让队列继续向后跑。"""
        self.started_at = time.time()
        self.done = done

        try:
            self.runner(self.finish)
        except Exception as exc:
            ERROR_MSG("BagWriteOperation.execute failed: name=%s, error=%s" % (self.name, exc))
            if self.fail_callback and not self.timed_out:
                self.fail_callback(False, self.fail_op, 0, self.fail_item, str(exc))
            self.finish()

    def finish(self):
        """结束当前操作；重复 finish 会被忽略，避免超时和迟到 DB 回调双重推进队列。"""
        if self.finished:
            return

        self.finished = True
        done = self.done
        self.done = None
        if done:
            done()

    def checkTimeout(self, now):
        """由队列 tick 调用；超时时失败当前操作并释放队列。"""
        if self.finished or self.timeout_seconds <= 0 or self.started_at <= 0:
            return False

        if now - self.started_at < self.timeout_seconds:
            return False

        self.timed_out = True
        message = "Bag operation timeout: ownerDBID=%s, name=%s, timeout=%.1fs" % (
            self.owner_dbid, self.name, self.timeout_seconds)
        ERROR_MSG(message)
        if self.fail_callback:
            self.fail_callback(False, self.fail_op, 0, self.fail_item, message)
        self.finish()
        return True


class BagOperationQueue(object):
    """
    单个 ownerDBID 的背包写操作串行队列。

    KBE 的 executeRawDatabaseCommand 是异步回调模式；如果同一玩家短时间连续发起 move/split/merge，
    没有队列时后发操作可能先完成，导致数据库位置和客户端增量顺序不一致。这里按玩家维度串行化写操作，
    不同玩家之间仍然可以并发执行。
    """

    def __init__(self, owner_dbid):
        self.owner_dbid = int(owner_dbid or 0)
        self.items = deque()
        self.running = False
        self.current = None

    def push(self, operation):
        """加入一个写操作；如果当前没有操作在跑，立即启动队列。"""
        self.items.append(operation)
        if not self.running:
            self._run_next()

    def _run_next(self):
        """取出下一个操作执行；队列清空后标记为空闲。"""
        if not self.items:
            self.running = False
            self.current = None
            _operation_queues.pop(self.owner_dbid, None)
            return

        self.running = True
        self.current = self.items.popleft()
        self.current.execute(self._run_next)

    def tick(self, now):
        """
        检查当前操作是否超时。

        返回值：(timeoutCount, alive)
        timeoutCount 用来统计这次 tick 是否处理了超时；alive 表示队列里是否还留有未完成操作。
        """
        timeout_count = 0
        if self.current and self.current.checkTimeout(now):
            timeout_count = 1
        return timeout_count, self.running or bool(self.items)


def _enqueue_owner_operation(owner_dbid, operation):
    """按 ownerDBID 获取或创建队列，并把写操作放入队列。"""
    owner_dbid = int(owner_dbid or 0)
    tickBagQueues()

    queue = _operation_queues.get(owner_dbid)
    if queue is None:
        queue = BagOperationQueue(owner_dbid)
        _operation_queues[owner_dbid] = queue

    queue.push(operation)


def tickBagQueues():
    """
    背包队列 watchdog 入口。

    KBE base/cellapp 下没有全局 KBEngine.callback，所以这里不在服务内部依赖不存在的定时 API。
    调用方可以在插件 entry 或业务已有心跳里定期调用；一旦某条 raw DB 回调丢失，超时后会失败该操作并释放队列。
    """
    now = time.time()
    timeout_count = 0
    for owner_dbid, queue in list(_operation_queues.items()):
        queue_timeout, alive = queue.tick(now)
        timeout_count += queue_timeout
        if not alive:
            _operation_queues.pop(owner_dbid, None)
    return timeout_count


class Bag(object):
    """单个玩家的背包服务对象。"""

    def __init__(self, owner_dbid):
        self.owner_dbid = int(owner_dbid or 0)

    def isValid(self):
        """背包必须绑定到已经落库的 Avatar。"""
        return self.owner_dbid > 0

    def setCapacity(self, capacity, callback=None):
        """设置背包容量；0 表示不限制容量。"""
        if not self.isValid():
            if callback:
                callback(False, "Bag owner_dbid is invalid")
            return False

        try:
            capacity = max(0, int(capacity or 0))
        except Exception as exc:
            if callback:
                callback(False, str(exc))
            return False

        def _on_done(result, rows, insertid, error):
            if error:
                message = "Bag.setCapacity failed: %s" % error
                ERROR_MSG(message)
                if callback:
                    callback(False, message)
                return
            if callback:
                callback(True, "")

        executeRaw(bag_storage.upsert_capacity_sql(self.owner_dbid, capacity), _on_done)
        return True

    def getCapacity(self, callback=None):
        """查询背包容量；0 表示不限制容量。"""
        if not callback:
            ERROR_MSG("Bag.getCapacity requires callback because raw DB command is asynchronous.")
            return False
        if not self.isValid():
            callback(False, 0, "Bag owner_dbid is invalid")
            return False

        def _on_done(result, rows, insertid, error):
            if error:
                callback(False, 0, "Bag.getCapacity failed: %s" % error)
                return
            callback(True, bag_storage.decode_first_int(result), "")

        executeRaw(bag_storage.select_capacity_sql(self.owner_dbid), _on_done)
        return True

    def addItem(self, itemID, count, stackable=1, extra="", callback=None, opID="", reason="ADD", context="",
                maxStack=bag_storage.DEFAULT_MAX_STACK):
        """
        添加物品入口。

        公开写接口只负责入队；真正 SQL 写入在 _doAddItem 中执行。这样业务层调用方式不变，
        但同一个 ownerDBID 的背包写操作会严格串行。
        """
        if callable(stackable) and callback is None:
            callback = stackable
            stackable = 1
            extra = ""
        elif callable(extra) and callback is None:
            callback = extra
            extra = ""

        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "addItem",
            lambda queued_callback: self._doAddItem(
                itemID, count, stackable, extra, queued_callback, opID, reason, context, maxStack),
            callback,
            bag_storage.OP_ADD,
            bag_storage.empty_item())

    def _doAddItem(self, itemID, count, stackable=1, extra="", callback=None, opID="", reason="ADD", context="",
                   maxStack=bag_storage.DEFAULT_MAX_STACK):
        """
        添加物品。

        stackable=1 时优先叠加到同 itemID 的可堆叠实例；否则新增一条独立实例。
        """
        if callable(stackable) and callback is None:
            callback = stackable
            stackable = 1
            extra = ""
        elif callable(extra) and callback is None:
            callback = extra
            extra = ""

        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        try:
            item_id = int(itemID)
            count = int(count)
            stackable = 1 if int(stackable or 0) else 0
            max_stack = bag_storage.normalize_max_stack(maxStack)
            if count <= 0:
                raise ValueError("count must be > 0")
        except Exception as exc:
            self._finish_update(callback, False, bag_storage.OP_ADD, 0, bag_storage.empty_item(), str(exc))
            return False

        def _add_remaining(remaining, last_bid=0):
            if remaining <= 0:
                self._query_bid_and_finish(
                    last_bid, bag_storage.OP_ADD, callback, "ADD", count, None, None, opID, reason, context)
                return

            def _insert_new():
                insert_count = min(remaining, max_stack if stackable else remaining)

                def _on_capacity_done(result, rows, insertid, error):
                    if error:
                        self._finish_update(callback, False, bag_storage.OP_ADD, 0, bag_storage.empty_item(),
                                            "Bag.addItem capacity-query failed: %s" % error)
                        return
                    capacity, used = self._decode_capacity_and_used(result)
                    if capacity > 0 and used >= capacity:
                        self._finish_update(callback, False, bag_storage.OP_ADD, 0, bag_storage.empty_item(),
                                            "Bag.addItem capacity full: capacity=%s" % capacity)
                        return

                    sql = bag_storage.insert_item_sql(
                        self.owner_dbid, item_id, insert_count, stackable, extra, max_stack)

                    def _on_add_done(result2, rows2, insertid2, error2):
                        if error2:
                            self._finish_update(callback, False, bag_storage.OP_ADD, 0, bag_storage.empty_item(),
                                                "Bag.addItem failed: %s" % error2)
                            return
                        _add_remaining(remaining - insert_count, int(insertid2))

                    executeRaw(sql, _on_add_done)

                executeRaw(bag_storage.select_capacity_and_used_sql(self.owner_dbid), _on_capacity_done)

            if not stackable:
                _insert_new()
                return

            def _on_stackable_done(result, rows, insertid, error):
                if error:
                    self._finish_update(callback, False, bag_storage.OP_ADD, 0, bag_storage.empty_item(),
                                        "Bag.addItem stack-query failed: %s" % error)
                    return

                stack_item = bag_storage.decode_first_item(result)
                if not stack_item:
                    _insert_new()
                    return

                add_count = min(remaining, max(0, stack_item["maxStack"] - stack_item["count"]))
                if add_count <= 0:
                    _insert_new()
                    return

                def _on_stack_done(result2, rows2, insertid2, error2):
                    if error2:
                        self._finish_update(callback, False, bag_storage.OP_UPDATE, 0, stack_item,
                                            "Bag.addItem stack failed: %s" % error2)
                        return
                    if int(rows2 or 0) <= 0:
                        _insert_new()
                        return
                    _add_remaining(remaining - add_count, stack_item["bid"])

                executeRaw(bag_storage.update_stack_item_sql(self.owner_dbid, stack_item["bid"], add_count),
                           _on_stack_done)

            executeRaw(bag_storage.select_stackable_item_sql(self.owner_dbid, item_id, extra), _on_stackable_done)

        _add_remaining(count)
        return True

    def removeItem(self, bid, count, callback=None, opID="", reason="REMOVE", context=""):
        """删除物品入口；入队后由 _doRemoveItem 串行执行。"""
        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "removeItem",
            lambda queued_callback: self._doRemoveItem(bid, count, queued_callback, opID, reason, context),
            callback,
            bag_storage.OP_REMOVE,
            safeEmptyItem(bid))

    def _doRemoveItem(self, bid, count, callback=None, opID="", reason="REMOVE", context=""):
        """按实例 bid 删除指定数量；数量扣到 0 时删除该实例。"""
        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        try:
            bid = int(bid)
            count = int(count)
            if count <= 0:
                raise ValueError("count must be > 0")
        except Exception as exc:
            self._finish_update(callback, False, bag_storage.OP_REMOVE, 0, bag_storage.empty_item(), str(exc))
            return False

        def _on_before_done(before_result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_REMOVE, 0, bag_storage.empty_item(bid),
                                    "Bag.removeItem before-query failed: %s" % error)
                return

            before_item = bag_storage.decode_first_item(before_result)
            if not before_item:
                self._finish_update(callback, False, bag_storage.OP_REMOVE, 0, bag_storage.empty_item(bid),
                                    "Bag.removeItem item not found")
                return

            def _on_remove_done(result, rows2, insertid2, error2):
                if error2:
                    self._finish_update(callback, False, bag_storage.OP_REMOVE, 0, bag_storage.empty_item(bid),
                                        "Bag.removeItem update failed: %s" % error2)
                    return

                def _on_delete_done(result3, rows3, insertid3, error3):
                    if error3:
                        self._finish_update(callback, False, bag_storage.OP_REMOVE, 0, bag_storage.empty_item(bid),
                                            "Bag.removeItem cleanup failed: %s" % error3)
                        return

                    self._query_bid_and_finish(
                        bid, bag_storage.OP_UPDATE, callback, "REMOVE", count, before_item, None,
                        opID, reason, context)

                executeRaw(bag_storage.delete_zero_item_by_bid_sql(self.owner_dbid, bid), _on_delete_done)

            executeRaw(bag_storage.remove_item_by_bid_sql(self.owner_dbid, bid, count), _on_remove_done)

        executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, bid), _on_before_done)
        return True

    def splitItem(self, bid, count, callback=None, opID="", reason="SPLIT", context=""):
        """拆分物品入口；入队后由 _doSplitItem 串行执行。"""
        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "splitItem",
            lambda queued_callback: self._doSplitItem(bid, count, queued_callback, opID, reason, context),
            callback,
            bag_storage.OP_SPLIT,
            safeEmptyItem(bid))

    def _doSplitItem(self, bid, count, callback=None, opID="", reason="SPLIT", context=""):
        """拆分物品：从源实例扣 count，并在末尾创建一个同 itemID/extra 的新实例。"""
        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        try:
            bid = int(bid)
            count = int(count)
            if count <= 0:
                raise ValueError("count must be > 0")
        except Exception as exc:
            self._finish_update(callback, False, bag_storage.OP_SPLIT, 0, bag_storage.empty_item(bid), str(exc))
            return False

        def _on_source_done(source_result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_SPLIT, 0, bag_storage.empty_item(bid),
                                    "Bag.splitItem source-query failed: %s" % error)
                return

            source_item = bag_storage.decode_first_item(source_result)
            if not source_item or source_item["count"] <= count:
                self._finish_update(callback, False, bag_storage.OP_SPLIT, 0, bag_storage.empty_item(bid),
                                    "Bag.splitItem source count not enough")
                return

            def _on_capacity_done(capacity_result, rows0, insertid0, error0):
                if error0:
                    self._finish_update(callback, False, bag_storage.OP_SPLIT, 0, bag_storage.empty_item(bid),
                                        "Bag.splitItem capacity-query failed: %s" % error0)
                    return
                capacity, used = self._decode_capacity_and_used(capacity_result)
                if capacity > 0 and used >= capacity:
                    self._finish_update(callback, False, bag_storage.OP_SPLIT, 0, bag_storage.empty_item(bid),
                                        "Bag.splitItem capacity full: capacity=%s" % capacity)
                    return

                def _on_reduce_done(result, rows2, insertid2, error2):
                    if error2:
                        self._finish_update(callback, False, bag_storage.OP_SPLIT, 0, bag_storage.empty_item(bid),
                                            "Bag.splitItem reduce failed: %s" % error2)
                        return
                    if int(rows2 or 0) <= 0:
                        self._finish_update(callback, False, bag_storage.OP_SPLIT, 0, bag_storage.empty_item(bid),
                                            "Bag.splitItem reduce affected no rows")
                        return

                    def _on_insert_done(result3, rows3, new_bid, error3):
                        if error3:
                            self._finish_update(callback, False, bag_storage.OP_SPLIT, 0, bag_storage.empty_item(bid),
                                                "Bag.splitItem insert failed: %s" % error3)
                            return

                        self._query_bid_and_finish(
                            int(new_bid), bag_storage.OP_SPLIT, callback, "SPLIT", count, None, None,
                            opID, reason, context)

                    executeRaw(bag_storage.insert_split_item_sql(self.owner_dbid, source_item, count), _on_insert_done)

                executeRaw(bag_storage.split_source_sql(self.owner_dbid, bid, count), _on_reduce_done)

            executeRaw(bag_storage.select_capacity_and_used_sql(self.owner_dbid), _on_capacity_done)

        executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, bid), _on_source_done)
        return True

    def swapItem(self, bid1, bid2, callback=None, opID="", reason="SWAP", context=""):
        """交换位置入口；入队后由 _doSwapItem 串行执行。"""
        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "swapItem",
            lambda queued_callback: self._doSwapItem(bid1, bid2, queued_callback, opID, reason, context),
            callback,
            bag_storage.OP_MOVE,
            safeEmptyItem(bid1))

    def _doSwapItem(self, bid1, bid2, callback=None, opID="", reason="SWAP", context=""):
        """交换两个实例物品的 bagIndex。"""
        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        bid1 = int(bid1)
        bid2 = int(bid2)
        if bid1 == bid2:
            self._finish_update(callback, False, bag_storage.OP_MOVE, 0, bag_storage.empty_item(bid1),
                                "Bag.swapItem bid is same")
            return False

        def _on_first_done(result1, rows1, insertid1, error1):
            if error1:
                self._finish_update(callback, False, bag_storage.OP_MOVE, 0, bag_storage.empty_item(bid1),
                                    "Bag.swapItem first-query failed: %s" % error1)
                return

            item1 = bag_storage.decode_first_item(result1)
            if not item1:
                self._finish_update(callback, False, bag_storage.OP_MOVE, 0, bag_storage.empty_item(bid1),
                                    "Bag.swapItem first item not found")
                return

            def _on_second_done(result2, rows2, insertid2, error2):
                if error2:
                    self._finish_update(callback, False, bag_storage.OP_MOVE, 0, item1,
                                        "Bag.swapItem second-query failed: %s" % error2)
                    return

                item2 = bag_storage.decode_first_item(result2)
                if not item2:
                    self._finish_update(callback, False, bag_storage.OP_MOVE, 0, item1,
                                        "Bag.swapItem second item not found")
                    return

                def _on_update_first(result3, rows3, insertid3, error3):
                    if error3:
                        self._finish_update(callback, False, bag_storage.OP_MOVE, 0, item1,
                                            "Bag.swapItem update first failed: %s" % error3)
                        return

                    def _on_update_second(result4, rows4, insertid4, error4):
                        if error4:
                            self._finish_update(callback, False, bag_storage.OP_MOVE, 0, item1,
                                                "Bag.swapItem update second failed: %s" % error4)
                            return

                        moved = dict(item1)
                        moved["bagIndex"] = item2["bagIndex"]
                        self._write_op_log_and_finish(
                            callback, bag_storage.OP_MOVE, moved["bagIndex"], moved, "SWAP", 0,
                            item1, moved, bid2, opID, reason, context)

                    executeRaw(bag_storage.update_bag_index_sql(self.owner_dbid, bid2, item1["bagIndex"]),
                               _on_update_second)

                executeRaw(bag_storage.update_bag_index_sql(self.owner_dbid, bid1, item2["bagIndex"]),
                           _on_update_first)

            executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, bid2), _on_second_done)

        executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, bid1), _on_first_done)
        return True

    def moveItem(self, bid, bagIndex, callback=None, opID="", reason="MOVE", context=""):
        """移动到指定位置入口；入队后由 _doMoveItem 串行执行。"""
        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "moveItem",
            lambda queued_callback: self._doMoveItem(bid, bagIndex, queued_callback, opID, reason, context),
            callback,
            bag_storage.OP_MOVE,
            safeEmptyItem(bid))

    def _doMoveItem(self, bid, bagIndex, callback=None, opID="", reason="MOVE", context=""):
        """
        移动物品到指定背包位置。

        如果目标 bagIndex 为空，只移动当前物品；如果目标位置已有物品，则交换两个物品的位置。
        """
        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        try:
            bid = int(bid)
            target_index = int(bagIndex)
            if target_index < 0:
                raise ValueError("bagIndex must be >= 0")
        except Exception as exc:
            self._finish_update(callback, False, bag_storage.OP_MOVE, 0, bag_storage.empty_item(), str(exc))
            return False

        def _on_source_done(source_result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_MOVE, 0, bag_storage.empty_item(bid),
                                    "Bag.moveItem source-query failed: %s" % error)
                return

            source_item = bag_storage.decode_first_item(source_result)
            if not source_item:
                self._finish_update(callback, False, bag_storage.OP_MOVE, 0, bag_storage.empty_item(bid),
                                    "Bag.moveItem source item not found")
                return

            if source_item["bagIndex"] == target_index:
                self._write_op_log_and_finish(
                    callback, bag_storage.OP_MOVE, target_index, source_item, "MOVE", 0,
                    source_item, source_item, 0, opID, reason, context)
                return

            def _on_target_done(target_result, rows2, insertid2, error2):
                if error2:
                    self._finish_update(callback, False, bag_storage.OP_MOVE, 0, source_item,
                                        "Bag.moveItem target-query failed: %s" % error2)
                    return

                target_item = bag_storage.decode_first_item(target_result)

                def _on_move_source_done(result3, rows3, insertid3, error3):
                    if error3:
                        self._finish_update(callback, False, bag_storage.OP_MOVE, 0, source_item,
                                            "Bag.moveItem update source failed: %s" % error3)
                        return

                    moved = dict(source_item)
                    moved["bagIndex"] = target_index

                    if not target_item:
                        self._write_op_log_and_finish(
                            callback, bag_storage.OP_MOVE, target_index, moved, "MOVE", 0,
                            source_item, moved, 0, opID, reason, context)
                        return

                    def _on_move_target_done(result4, rows4, insertid4, error4):
                        if error4:
                            self._finish_update(callback, False, bag_storage.OP_MOVE, 0, source_item,
                                                "Bag.moveItem update target failed: %s" % error4)
                            return

                        self._write_op_log_and_finish(
                            callback, bag_storage.OP_MOVE, target_index, moved, "MOVE", 0,
                            source_item, moved, target_item["bid"], opID, reason, context)

                    executeRaw(
                        bag_storage.update_bag_index_sql(self.owner_dbid, target_item["bid"], source_item["bagIndex"]),
                        _on_move_target_done)

                executeRaw(bag_storage.update_bag_index_sql(self.owner_dbid, bid, target_index), _on_move_source_done)

            executeRaw(bag_storage.select_one_by_bag_index_sql(self.owner_dbid, target_index), _on_target_done)

        executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, bid), _on_source_done)
        return True

    def mergeItem(self, fromBID, toBID, callback=None, opID="", reason="MERGE", context=""):
        """合并物品入口；入队后由 _doMergeItem 串行执行。"""
        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "mergeItem",
            lambda queued_callback: self._doMergeItem(fromBID, toBID, queued_callback, opID, reason, context),
            callback,
            bag_storage.OP_MERGE,
            safeEmptyItem(fromBID))

    def _doMergeItem(self, fromBID, toBID, callback=None, opID="", reason="MERGE", context=""):
        """合并两个同 itemID 且 extra 相同的实例，fromBID 会被删除。"""
        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        from_bid = int(fromBID)
        to_bid = int(toBID)
        if from_bid == to_bid:
            self._finish_update(callback, False, bag_storage.OP_MERGE, 0, bag_storage.empty_item(from_bid),
                                "Bag.mergeItem bid is same")
            return False

        def _on_from_done(from_result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_MERGE, 0, bag_storage.empty_item(from_bid),
                                    "Bag.mergeItem from-query failed: %s" % error)
                return

            from_item = bag_storage.decode_first_item(from_result)
            if not from_item:
                self._finish_update(callback, False, bag_storage.OP_MERGE, 0, bag_storage.empty_item(from_bid),
                                    "Bag.mergeItem from item not found")
                return

            def _on_merge_done(result, rows2, insertid2, error2):
                if error2:
                    self._finish_update(callback, False, bag_storage.OP_MERGE, 0, from_item,
                                        "Bag.mergeItem update failed: %s" % error2)
                    return
                if int(rows2 or 0) <= 0:
                    self._finish_update(callback, False, bag_storage.OP_MERGE, 0, from_item,
                                        "Bag.mergeItem requires same itemID, same extra, stackable=1 and enough maxStack")
                    return

                def _on_delete_done(result3, rows3, insertid3, error3):
                    if error3:
                        self._finish_update(callback, False, bag_storage.OP_MERGE, 0, from_item,
                                            "Bag.mergeItem delete failed: %s" % error3)
                        return

                    self._query_bid_and_finish(
                        to_bid, bag_storage.OP_MERGE, callback, "MERGE", from_item["count"], None, from_bid,
                        opID, reason, context)

                executeRaw(bag_storage.delete_item_by_bid_sql(self.owner_dbid, from_bid), _on_delete_done)

            executeRaw(bag_storage.merge_items_sql(self.owner_dbid, from_bid, to_bid), _on_merge_done)

        executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, from_bid), _on_from_done)
        return True

    def sortItems(self, callback=None, opID="", reason="SORT", context=""):
        """整理背包入口；入队后由 _doSortItems 串行执行。"""
        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "sortItems",
            lambda queued_callback: self._doSortItems(queued_callback, opID, reason, context),
            callback,
            bag_storage.OP_SORT,
            bag_storage.empty_item())

    def _doSortItems(self, callback=None, opID="", reason="SORT", context=""):
        """整理背包：按 itemID、bid 排序后重写 bagIndex。"""
        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        def _on_items_done(result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_SORT, 0, bag_storage.empty_item(),
                                    "Bag.sortItems query failed: %s" % error)
                return

            items = bag_storage.decode_items(result)
            self._sort_update_next(items, 0, callback, opID, reason, context)

        executeRaw(bag_storage.select_sort_items_sql(self.owner_dbid), _on_items_done)
        return True

    def listItems(self, callback=None):
        """查询完整背包。"""
        if not callback:
            ERROR_MSG("Bag.listItems requires callback because raw DB command is asynchronous.")
            return False
        if not self._check_valid_for_list(callback):
            return False

        def _on_query_done(result, rows, insertid, error):
            if error:
                callback(False, [], "Bag.listItems failed: %s" % error)
                return
            callback(True, bag_storage.decode_items(result), "")

        executeRaw(bag_storage.select_all_sql(self.owner_dbid), _on_query_done)
        return True

    def pageItems(self, page, pageSize, callback=None):
        """分页查询背包。"""
        if not callback:
            ERROR_MSG("Bag.pageItems requires callback because raw DB command is asynchronous.")
            return False
        if not self._check_valid_for_page(callback, page, pageSize):
            return False

        page = max(1, int(page))
        page_size = max(1, int(pageSize))

        def _on_count_done(result, rows, insertid, error):
            if error:
                callback(False, page, page_size, 0, [], "Bag.pageItems count failed: %s" % error)
                return
            total = bag_storage.decode_first_int(result)

            def _on_page_done(result2, rows2, insertid2, error2):
                if error2:
                    callback(False, page, page_size, total, [], "Bag.pageItems query failed: %s" % error2)
                    return
                callback(True, page, page_size, total, bag_storage.decode_items(result2), "")

            executeRaw(bag_storage.select_page_sql(self.owner_dbid, page, page_size), _on_page_done)

        executeRaw(bag_storage.count_sql(self.owner_dbid), _on_count_done)
        return True

    def transferItems(self, targetDBID, items, callback=None, opID="", reason="TRANSFER", context=""):
        """
        向另一个玩家交易/转移多个物品。

        items 支持 [{"bid": 1, "count": 2}, ...] 或等价 JSON 字符串。当前实现按顺序执行多条 SQL，
        适合插件样板验证；正式交易系统建议接入同连接事务后再用于高价值跨玩家交易。
        """
        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "transferItems",
            lambda queued_callback: self._doTransferItems(
                targetDBID, items, queued_callback, opID, reason, context),
            callback,
            bag_storage.OP_TRANSFER,
            bag_storage.empty_item())

    def _doTransferItems(self, targetDBID, items, callback=None, opID="", reason="TRANSFER", context=""):
        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        try:
            target_dbid = int(targetDBID)
            trade_items = self._normalize_trade_items(items)
            if target_dbid <= 0 or target_dbid == self.owner_dbid:
                raise ValueError("targetDBID is invalid")
            if not trade_items:
                raise ValueError("items is empty")
        except Exception as exc:
            self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, bag_storage.empty_item(), str(exc))
            return False

        def _on_target_capacity_done(capacity_result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, bag_storage.empty_item(),
                                    "Bag.transferItems target capacity-query failed: %s" % error)
                return
            capacity, used = self._decode_capacity_and_used(capacity_result)
            if capacity > 0 and used + len(trade_items) > capacity:
                self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, bag_storage.empty_item(),
                                    "Bag.transferItems target capacity not enough: capacity=%s" % capacity)
                return
            self._transfer_item_next(target_dbid, trade_items, 0, callback, opID, reason, context)

        executeRaw(bag_storage.select_capacity_and_used_sql(target_dbid), _on_target_capacity_done)
        return True

    def getItem(self, bid, callback=None):
        """按 bid 查询单个实例物品。"""
        if not callback:
            ERROR_MSG("Bag.getItem requires callback because raw DB command is asynchronous.")
            return False
        if not self._check_valid_for_item(callback):
            return False

        def _on_done(result, rows, insertid, error):
            if error:
                callback(False, None, "Bag.getItem failed: %s" % error)
                return
            callback(True, bag_storage.decode_first_item(result), "")

        executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, int(bid)), _on_done)
        return True

    def getItemCount(self, itemID, callback=None):
        """查询某个 itemID 的总数量，跨多个实例累加。"""
        if not callback:
            ERROR_MSG("Bag.getItemCount requires callback because raw DB command is asynchronous.")
            return False
        if not self._check_valid_for_count(callback):
            return False

        def _on_done(result, rows, insertid, error):
            if error:
                callback(False, 0, "Bag.getItemCount failed: %s" % error)
                return
            callback(True, bag_storage.decode_first_int(result), "")

        executeRaw(bag_storage.total_item_count_sql(self.owner_dbid, int(itemID)), _on_done)
        return True

    def clear(self, callback=None, opID="", reason="CLEAR", context=""):
        """清空背包入口；入队后由 _doClear 串行执行。"""
        callback = callback or noopUpdateCallback
        return self._enqueue_update(
            "clear",
            lambda queued_callback: self._doClear(queued_callback, opID, reason, context),
            callback,
            bag_storage.OP_CLEAR,
            bag_storage.empty_item())

    def _doClear(self, callback=None, opID="", reason="CLEAR", context=""):
        """清空背包。"""
        callback = callback or noopUpdateCallback
        if not self._check_valid(callback):
            return False

        def _on_count_done(count_result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_CLEAR, 0, bag_storage.empty_item(),
                                    "Bag.clear count failed: %s" % error)
                return
            before_count = bag_storage.decode_first_int(count_result)

            def _on_done(result, rows2, insertid2, error2):
                if error2:
                    self._finish_update(callback, False, bag_storage.OP_CLEAR, 0, bag_storage.empty_item(),
                                        "Bag.clear failed: %s" % error2)
                    return
                self._write_op_log_and_finish(
                    callback, bag_storage.OP_CLEAR, 0, bag_storage.empty_item(), "CLEAR", 0,
                    {"count": before_count, "bagIndex": 0, "itemID": 0, "bid": 0}, None, 0,
                    opID, reason, context)

            executeRaw(bag_storage.clear_sql(self.owner_dbid), _on_done)

        executeRaw(bag_storage.count_sql(self.owner_dbid), _on_count_done)
        return True

    def _query_bid_and_finish(self, bid, op, callback, log_type=None, log_count=0, before_item=None,
                              target_bid=None, op_id="", reason="", context=""):
        """查询 bid 对应物品和列表位置，再统一写日志并回调。"""
        def _on_item_done(item_result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, op, 0, bag_storage.empty_item(bid),
                                    "Bag._query_bid_and_finish item failed: %s" % error)
                return

            item = bag_storage.decode_first_item(item_result)
            if not item:
                item = bag_storage.empty_item(bid)
                final_op = bag_storage.OP_REMOVE
                bag_index = 0
            else:
                final_op = op
                bag_index = item["bagIndex"]

            def _on_index_done(index_result, rows2, insertid2, error2):
                if error2:
                    self._finish_update(callback, False, final_op, 0, item,
                                        "Bag._query_bid_and_finish index failed: %s" % error2)
                    return

                index = bag_storage.decode_first_int(index_result)
                self._write_op_log_and_finish(
                    callback, final_op, index, item, log_type, log_count, before_item, item,
                    target_bid or 0, op_id, reason, context)

            executeRaw(bag_storage.select_index_by_bag_index_sql(self.owner_dbid, bag_index), _on_index_done)

        executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, bid), _on_item_done)

    def _enqueue_update(self, name, runner, callback, fail_op, fail_item):
        """
        把写操作放进 ownerDBID 队列。

        queued_callback 会先把结果转给原始业务 callback，再通知队列执行下一条。后续如果确认
        executeRawDatabaseCommand 支持同连接事务，这里无需改调用方，只需要把 runner 内部换成事务执行器。
        """
        callback = callback or noopUpdateCallback

        def _runner(done):
            finished = [False]

            def _queued_callback(success, op, index, item, message):
                if operation.timed_out:
                    ERROR_MSG("Bag._enqueue_update late callback ignored after timeout: ownerDBID=%s, name=%s" % (
                        self.owner_dbid, name))
                    return

                if finished[0]:
                    ERROR_MSG("Bag._enqueue_update callback repeated: ownerDBID=%s, name=%s" % (
                        self.owner_dbid, name))
                    return

                finished[0] = True
                try:
                    callback(success, op, index, item, message)
                finally:
                    done()

            started = runner(_queued_callback)
            if started is False and not finished[0]:
                _queued_callback(False, fail_op, 0, fail_item, "Bag operation start failed: %s" % name)

        operation = BagWriteOperation(self.owner_dbid, name, _runner, callback, fail_op, fail_item)
        _enqueue_owner_operation(self.owner_dbid, operation)
        return True

    def _sort_update_next(self, items, pos, callback, op_id, reason, context):
        """递归更新整理后的 bagIndex，避免拼多语句 SQL。"""
        if pos >= len(items):
            self._write_op_log_and_finish(
                callback, bag_storage.OP_SORT, 0, bag_storage.empty_item(), "SORT", len(items),
                None, None, 0, op_id, reason, context)
            return

        item = items[pos]

        def _on_done(result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_SORT, pos, item,
                                    "Bag.sortItems update failed: %s" % error)
                return
            self._sort_update_next(items, pos + 1, callback, op_id, reason, context)

        executeRaw(bag_storage.update_bag_index_sql(self.owner_dbid, item["bid"], pos), _on_done)

    def _transfer_item_next(self, target_dbid, trade_items, pos, callback, op_id, reason, context):
        """逐条转移多个物品；任意一步失败都会停止后续物品。"""
        if pos >= len(trade_items):
            self._write_op_log_and_finish(
                callback, bag_storage.OP_TRANSFER, 0, bag_storage.empty_item(), "TRANSFER", len(trade_items),
                None, None, 0, op_id, reason, context)
            return

        spec = trade_items[pos]
        bid = spec["bid"]
        count = spec["count"]

        def _on_source_done(source_result, rows, insertid, error):
            if error:
                self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, bag_storage.empty_item(bid),
                                    "Bag.transferItems source-query failed: %s" % error)
                return

            source_item = bag_storage.decode_first_item(source_result)
            if not source_item or source_item["count"] < count:
                self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, bag_storage.empty_item(bid),
                                    "Bag.transferItems source count not enough")
                return

            if source_item["count"] == count:
                def _on_move_done(result2, rows2, insertid2, error2):
                    if error2:
                        self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, source_item,
                                            "Bag.transferItems move item failed: %s" % error2)
                        return
                    if int(rows2 or 0) <= 0:
                        self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, source_item,
                                            "Bag.transferItems move affected no rows")
                        return
                    self._transfer_item_next(target_dbid, trade_items, pos + 1, callback, op_id, reason, context)

                executeRaw(bag_storage.update_item_owner_sql(self.owner_dbid, target_dbid, bid), _on_move_done)
                return

            def _on_reduce_done(result3, rows3, insertid3, error3):
                if error3:
                    self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, source_item,
                                        "Bag.transferItems reduce failed: %s" % error3)
                    return
                if int(rows3 or 0) <= 0:
                    self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, source_item,
                                        "Bag.transferItems reduce affected no rows")
                    return

                def _on_insert_done(result4, rows4, insertid4, error4):
                    if error4:
                        self._finish_update(callback, False, bag_storage.OP_TRANSFER, 0, source_item,
                                            "Bag.transferItems insert target failed: %s" % error4)
                        return
                    self._transfer_item_next(target_dbid, trade_items, pos + 1, callback, op_id, reason, context)

                executeRaw(
                    bag_storage.insert_item_sql(
                        target_dbid, source_item["itemID"], count, source_item["stackable"],
                        source_item["extra"], source_item["maxStack"]),
                    _on_insert_done)

            executeRaw(bag_storage.remove_item_by_bid_sql(self.owner_dbid, bid, count), _on_reduce_done)

        executeRaw(bag_storage.select_one_by_bid_sql(self.owner_dbid, bid), _on_source_done)

    def _normalize_trade_items(self, items):
        """把交易参数规整成 [{"bid": int, "count": int}]。"""
        if isinstance(items, basestring):
            items = json.loads(items or "[]")

        result = []
        for item in list(items or []):
            bid = int(item.get("bid", 0))
            count = int(item.get("count", 0))
            if bid <= 0 or count <= 0:
                raise ValueError("trade item bid/count is invalid")
            result.append({"bid": bid, "count": count})
        return result

    def _decode_capacity_and_used(self, result):
        """读取 select_capacity_and_used_sql 的结果。"""
        if not result or not result[0]:
            return 0, 0
        return bag_storage.cell_int(result[0][0]), bag_storage.cell_int(result[0][1])

    def _write_op_log_and_finish(self, callback, op, index, item, log_type, log_count, before_item,
                                 after_item, target_bid, op_id, reason, context):
        """写入背包操作日志，日志失败只打 ERROR，避免诱发业务重复写背包。"""
        if not log_type:
            self._finish_update(callback, True, op, index, item, "")
            return

        before_count = before_item.get("count") if isinstance(before_item, dict) else None
        after_count = after_item.get("count") if isinstance(after_item, dict) else None
        before_index = before_item.get("bagIndex") if isinstance(before_item, dict) else None
        after_index = after_item.get("bagIndex") if isinstance(after_item, dict) else None

        try:
            sql = bag_storage.insert_op_log_sql(
                self.owner_dbid,
                log_type,
                item.get("bid", 0),
                item.get("itemID", 0),
                log_count,
                before_count,
                after_count,
                before_index,
                after_index,
                0,
                target_bid,
                op_id,
                "DONE",
                reason,
                context)
        except Exception as exc:
            ERROR_MSG("Bag write op log build failed: %s" % exc)
            self._finish_update(callback, True, op, index, item, "Bag write op log build failed: %s" % exc)
            return

        def _on_log_done(result, rows, insertid, error):
            if error:
                ERROR_MSG("Bag write op log failed: %s" % error)
                self._finish_update(callback, True, op, index, item, "Bag write op log failed: %s" % error)
                return
            self._finish_update(callback, True, op, index, item, "")

        executeRaw(sql, _on_log_done)

    def _check_valid(self, callback):
        """校验 owner_dbid，并按写操作回调格式返回错误。"""
        if self.isValid():
            return True
        self._finish_update(callback, False, bag_storage.OP_UPDATE, 0, bag_storage.empty_item(),
                            "Bag owner_dbid is invalid")
        return False

    def _check_valid_for_list(self, callback):
        """校验 owner_dbid，并按完整列表回调格式返回错误。"""
        if self.isValid():
            return True
        callback(False, [], "Bag owner_dbid is invalid")
        return False

    def _check_valid_for_page(self, callback, page, pageSize):
        """校验 owner_dbid，并按分页回调格式返回错误。"""
        if self.isValid():
            return True
        callback(False, int(page or 1), int(pageSize or 1), 0, [], "Bag owner_dbid is invalid")
        return False

    def _check_valid_for_item(self, callback):
        """校验 owner_dbid，并按单物品查询回调格式返回错误。"""
        if self.isValid():
            return True
        callback(False, None, "Bag owner_dbid is invalid")
        return False

    def _check_valid_for_count(self, callback):
        """校验 owner_dbid，并按数量查询回调格式返回错误。"""
        if self.isValid():
            return True
        callback(False, 0, "Bag owner_dbid is invalid")
        return False

    def _finish_update(self, callback, success, op, index, item, message):
        """统一结束增量写操作。"""
        if callback:
            callback(success, op, index, item, message)
            return
        if not success:
            ERROR_MSG(message)
