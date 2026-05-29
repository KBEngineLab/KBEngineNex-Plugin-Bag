# -*- coding: utf-8 -*-
import KBEngine
from KBEDebug import INFO_MSG, WARNING_MSG
from plugins.Bag.common import bag_service
from plugins.Bag.common import bag_storage


class BagComponent(KBEngine.EntityComponent):
    """
    Avatar 在线背包组件。

    组件只负责两件事：
    - 把客户端发来的只读组件 RPC 转成 BagService 查询。
    - 把 BagService 的异步结果转成客户端组件回调。

    背包真正的读写逻辑放在 common/bag_service.py，这样离线奖励、邮件、商城和 GM 工具
    也能通过 getBagForEntityID(databaseID) 使用同一套代码。
    addItem/removeItem/clear 只给服务端业务调用，不能在 .def 中声明 Exposed。
    """

    def __init__(self):
        """组件构造函数，必须调用 KBE 的 EntityComponent 初始化。"""
        KBEngine.EntityComponent.__init__(self)
        self._bagService = None

    def onAttached(self, owner):
        """组件挂载到 Avatar 后触发；不从 KBE 属性恢复背包，只记录 owner 信息。"""
        INFO_MSG("BagComponent.onAttached: ownerID=%s, ownerDBID=%s" % (owner.id, self._owner_dbid()))
        self._sync_capacity_to_storage()

    def onDetached(self, owner):
        """组件从 Avatar 分离时触发；当前没有内存脏数据需要保存。"""
        INFO_MSG("BagComponent.onDetached: ownerID=%s" % owner.id)

    def addItem(self, itemID, count, stackable=1, extra=""):
        """服务端业务添加实例物品；实际落库和增量计算交给 BagService。"""
        self._bag().addItem(itemID, count, stackable, extra, self._on_update_done)

    def setCapacity(self, capacity):
        """设置背包容量；0 表示不限制容量。外部也可以直接改 self.capacity 后再调用本方法同步。"""
        self.capacity = max(0, int(capacity or 0))
        self._bag().setCapacity(self.capacity, self._on_capacity_done)

    def setCallbackSwitch(self, callbackName, enabled):
        """设置单个客户端回调开关：list、updated、page、error。"""
        name = str(callbackName or "").lower()
        value = 1 if int(enabled or 0) else 0
        if name in ("list", "onbaglist"):
            self.notifyBagList = value
        elif name in ("updated", "update", "onbagupdated"):
            self.notifyBagUpdated = value
        elif name in ("page", "onbagpage"):
            self.notifyBagPage = value
        elif name in ("error", "onbagerror"):
            self.notifyBagError = value
        else:
            WARNING_MSG("BagComponent.setCallbackSwitch: unknown callbackName=%s" % callbackName)

    def removeItem(self, bid, count):
        """服务端业务删除实例物品；扣减后剩余数量和删除事件由 BagService 判断。"""
        self._bag().removeItem(bid, count, self._on_update_done)

    def splitItem(self, bid, count):
        """服务端业务拆分实例物品；新实例会放到背包末尾。"""
        self._bag().splitItem(bid, count, self._on_update_done)

    def swapItem(self, bid1, bid2):
        """服务端业务交换两个实例物品的位置。"""
        self._bag().swapItem(bid1, bid2, self._on_update_done)

    def moveItem(self, bid, bagIndex):
        """服务端业务移动实例物品到指定位置；目标位置有物品时自动交换。"""
        self._bag().moveItem(bid, bagIndex, self._on_update_done)

    def mergeItem(self, fromBID, toBID):
        """服务端业务合并两个同 itemID 且 extra 相同的实例物品。"""
        self._bag().mergeItem(fromBID, toBID, self._on_update_done)

    def sortItems(self):
        """服务端业务整理背包；按 itemID 升序重写 bagIndex。"""
        self._bag().sortItems(self._on_update_done)

    def transferItems(self, targetDBID, itemsJson):
        """服务端业务多物品交易；itemsJson 形如 [{"bid":1,"count":2}]。"""
        self._bag().transferItems(targetDBID, itemsJson, self._on_update_done)

    def requestBagList(self):
        """客户端请求完整背包；成功后通过 onBagList 一次性返回 BagItems。"""
        def _on_done(success, items, message):
            if not success:
                self._notify_error(message)
                return

            INFO_MSG("BagComponent.requestBagList: ownerDBID=%s, items=%s" % (self._owner_dbid(), len(items)))
            if self._callback_enabled("list") and self._client_bag():
                self._client_bag().onBagList(items)

        self._bag().listItems(_on_done)

    def requestBagPage(self, page, pageSize):
        """客户端请求分页背包；返回 page、pageSize、total、items。"""
        def _on_done(success, fixed_page, fixed_page_size, total, items, message):
            if not success:
                self._notify_error(message)
                return

            INFO_MSG("BagComponent.requestBagPage: ownerDBID=%s, page=%s, pageSize=%s, total=%s, items=%s" % (
                self._owner_dbid(), fixed_page, fixed_page_size, total, len(items)))
            if self._callback_enabled("page") and self._client_bag():
                self._client_bag().onBagPage(fixed_page, fixed_page_size, total, items)

        self._bag().pageItems(page, pageSize, _on_done)

    def clear(self):
        """服务端业务清空背包；成功后通过 OP_CLEAR 增量通知客户端清空本地缓存。"""
        self._bag().clear(self._on_update_done)

    def _bag(self):
        """
        获取当前 Avatar 的背包服务对象。

        BagService 只绑定 ownerDBID，不持有组件或实体引用；这里做懒缓存，避免每次调用都创建
        一个新的服务句柄。同时兼容新建 Avatar 初始 databaseID 为 0、稍后才获得有效 DBID 的情况。
        """
        owner_dbid = self._owner_dbid()
        if self._bagService is None or self._bagService.owner_dbid != owner_dbid:
            self._bagService = bag_service.getBagForEntityID(owner_dbid)

        return self._bagService

    def _on_update_done(self, success, op, index, item, message):
        """把 BagService 的写操作结果转换成客户端 onBagUpdated 或 onBagError。"""
        if not success:
            self._notify_error(message)
            return

        if self._callback_enabled("updated") and self._client_bag():
            self._client_bag().onBagUpdated(op, index, item)

        INFO_MSG("BagComponent.onBagUpdated: ownerDBID=%s, op=%s, index=%s, bid=%s, itemID=%s" % (
            self._owner_dbid(), bag_storage.op_name(op), index, item["bid"], item["itemID"]))

    def _owner_dbid(self):
        """读取宿主 Avatar 的数据库ID；未写入数据库的 Avatar 无法安全持久化背包。"""
        return int(getattr(self.owner, "databaseID", 0) or 0)

    def _client_bag(self):
        """获取客户端上的 bag 组件 EntityCall，不存在客户端时返回 None。"""
        if not getattr(self.owner, "client", None):
            return None

        return getattr(self.owner.client, "bag", None)

    def _callback_enabled(self, name):
        """读取每个客户端回调自己的开关；默认都开启。"""
        attr = {
            "list": "notifyBagList",
            "updated": "notifyBagUpdated",
            "page": "notifyBagPage",
            "error": "notifyBagError",
        }.get(name)
        return int(getattr(self, attr, 1) or 0) != 0

    def _sync_capacity_to_storage(self):
        """组件属性 capacity 是外部可改的入口，挂载时同步到插件 meta 表。"""
        if int(getattr(self, "capacity", 0) or 0) > 0:
            self._bag().setCapacity(self.capacity, self._on_capacity_done)

    def _on_capacity_done(self, success, message):
        """容量同步结果。"""
        if not success:
            self._notify_error(message)
            return
        INFO_MSG("BagComponent.setCapacity: ownerDBID=%s, capacity=%s" % (
            self._owner_dbid(), getattr(self, "capacity", 0)))

    def _notify_error(self, message):
        """记录错误并尽量通知客户端。"""
        from KBEDebug import ERROR_MSG
        ERROR_MSG(message)
        if self._callback_enabled("error") and self._client_bag():
            self._client_bag().onBagError(message)
        else:
            WARNING_MSG("BagComponent._notify_error: client bag not available.")
