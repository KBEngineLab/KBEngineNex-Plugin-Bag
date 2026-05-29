# -*- coding: utf-8 -*-
import KBEngine
from KBEDebug import ERROR_MSG, INFO_MSG
from plugins.Bag.common import bag_service


TIMER_TYPE_BAG_QUEUE_WATCHDOG = 92001
WATCHDOG_INITIAL_OFFSET = 5
WATCHDOG_REPEAT_OFFSET = 5


class BagManager(KBEngine.Entity):
    """
    Bag 插件的 baseapp 常驻管理实体。

    当前只负责驱动背包写操作队列 watchdog，避免某条 raw DB 回调丢失后卡住某个玩家的队列。
    管理实体由插件 entry 自动创建，不需要业务在 kbemain.py 或 Avatar 上手动挂生命周期。
    """

    def __init__(self):
        """实体初始化后启动一个轻量定时器，周期性扫描背包队列超时。"""
        KBEngine.Entity.__init__(self)
        self._watchdogTimerID = self.addTimer(
            WATCHDOG_INITIAL_OFFSET, WATCHDOG_REPEAT_OFFSET, TIMER_TYPE_BAG_QUEUE_WATCHDOG)
        INFO_MSG("BagManager started: entityID=%s, watchdogTimerID=%s" % (self.id, self._watchdogTimerID))

    def onTimer(self, tid, userArg):
        """定时扫描背包队列；只处理 BagManager 自己创建的 watchdog timer。"""
        if tid == self._watchdogTimerID and userArg == TIMER_TYPE_BAG_QUEUE_WATCHDOG:
            timeout_count = bag_service.tickBagQueues()
            if timeout_count > 0:
                ERROR_MSG("BagManager watchdog detected timeout operations: count=%s" % timeout_count)

    def onDestroy(self):
        """实体销毁前关闭定时器，避免插件退出时留下无意义回调。"""
        if self._watchdogTimerID:
            self.delTimer(self._watchdogTimerID)
            INFO_MSG("BagManager stopped: entityID=%s, watchdogTimerID=%s" % (
                self.id, self._watchdogTimerID))
            self._watchdogTimerID = 0
