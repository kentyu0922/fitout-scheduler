# -*- coding: utf-8 -*-
"""
core/mpp_renderer.py - 向后兼容代理 (Backwards Compatibility Proxy)
===================================================================
架构重构说明：
通用排程渲染引擎已提取至独立的 `exporters/` 组件。
本模块作为向后兼容代理，透传导入 `exporters.mpp_renderer`，确保既有测试与旧调用不被破坏。
推荐新代码直接使用：`from exporters import build_mpp`
"""

from exporters.mpp_renderer import *
