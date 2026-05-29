# -*- coding: utf-8 -*-
from KBEDebug import INFO_MSG
from plugins.Bag.common.bag_model import make_item


def onInit(isReload):
    """bots 组件加载插件时调用；这里确认 bots 也能 import 插件 common 模块。"""
    INFO_MSG("Bag bots plugin onInit: isReload=%s" % isReload)
    assert make_item(1, 1001, 1, 0, 1, "")["itemID"] == 1001


def onComponentReady(isFirstGroup):
    """bots 进入 ready 阶段时调用。"""
    INFO_MSG("Bag bots plugin onComponentReady: isFirstGroup=%s" % isFirstGroup)


def onFini():
    """bots 组件退出前调用。"""
    INFO_MSG("Bag bots plugin onFini")
