# -*- coding: utf-8 -*-
import KBEngine
from KBEDebug import INFO_MSG


class BagComponent(KBEngine.EntityComponent):
    """bots 侧背包组件占位，用于客户端/机器人环境接收组件实例。"""

    def __init__(self):
        """组件构造函数，必须调用 KBE 的 EntityComponent 初始化。"""
        KBEngine.EntityComponent.__init__(self)

    def onAttached(self, owner):
        """组件挂载到 bots Avatar 时触发。"""
        INFO_MSG("BagComponent[bots].onAttached: ownerID=%s" % owner.id)

    def onDetached(self, owner):
        """组件从 bots Avatar 分离时触发。"""
        INFO_MSG("BagComponent[bots].onDetached: ownerID=%s" % owner.id)

    def onBagList(self, items):
        """服务端推送完整背包列表时触发。"""
        INFO_MSG("BagComponent[bots].onBagList: count=%s" % len(items))

    def onBagUpdated(self, op, index, item):
        """服务端推送单个背包增量时触发。"""
        INFO_MSG("BagComponent[bots].onBagUpdated: op=%s, index=%s, item=%s" % (op, index, item))

    def onBagPage(self, page, pageSize, total, items):
        """服务端推送分页背包数据时触发。"""
        INFO_MSG("BagComponent[bots].onBagPage: page=%s, pageSize=%s, total=%s, count=%s" % (
            page, pageSize, total, len(items)))

    def onBagError(self, message):
        """服务端背包操作失败时触发。"""
        INFO_MSG("BagComponent[bots].onBagError: %s" % message)
