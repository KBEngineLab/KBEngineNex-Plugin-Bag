# -*- coding: utf-8 -*-
import KBEngine
from KBEDebug import ERROR_MSG, INFO_MSG
from plugins.Bag.common.bag_model import make_item
from plugins.Bag.common import bag_service, bag_storage


_bagManager = None


def onInit(isReload):
    """base 组件加载插件时调用；这里做轻量自检，确认 common 路径已经注入。"""
    INFO_MSG("Bag base plugin onInit: isReload=%s" % isReload)
    assert make_item(1, 1001, 1, 0, 1, "")["itemID"] == 1001


def onComponentReady(isFirstGroup):
    """baseapp 进入 ready 阶段时调用；这里创建背包表。"""
    INFO_MSG("Bag base plugin onComponentReady: isFirstGroup=%s" % isFirstGroup)
    bag_service.ensureTable(_onCreateTableDone)
    _ensureBagManager()


def onFini():
    """base 组件退出前调用，业务可以在这里释放插件级资源。"""
    INFO_MSG("Bag base plugin onFini")
    _destroyBagManager()


def _onCreateTableDone(success, message):
    """建表回调；建表失败必须打 error，因为背包后续读写都会依赖该表。"""
    if not success:
        ERROR_MSG("Bag plugin create table failed: %s" % message)
        return

    INFO_MSG("Bag plugin tables ready: %s, %s" % (bag_storage.TABLE_NAME, bag_storage.OP_LOG_TABLE_NAME))


def _ensureBagManager():
    """确保插件自己的 BagManager 常驻实体存在；不存在则本地创建一个。"""
    global _bagManager
    if _bagManager is not None and not getattr(_bagManager, "isDestroyed", False):
        return _bagManager

    for entity in KBEngine.entities.values():
        if entity.__class__.__name__ == "BagManager" and not getattr(entity, "isDestroyed", False):
            _bagManager = entity
            return _bagManager

    _bagManager = KBEngine.createEntityLocally("BagManager", {})
    INFO_MSG("Bag plugin BagManager created locally: %s" % _bagManager)
    return _bagManager


def _destroyBagManager():
    """销毁插件自己的 BagManager 常驻实体。"""
    global _bagManager
    if _bagManager is None:
        return

    if not getattr(_bagManager, "isDestroyed", False):
        try:
            _bagManager.destroy()
        except Exception as exc:
            ERROR_MSG("Bag plugin BagManager destroy failed: %s" % exc)

    _bagManager = None
