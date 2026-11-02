# -*- coding: utf-8 -*-
"""
core/msp_automation.py - 向后兼容代理 (Backwards Compatibility Proxy)
====================================================================
架构重构说明：
MS Project COM 自动化与底层操作已提取至独立的 `exporters/` 组件。
本模块作为向后兼容代理，透传导入 `exporters.msp_automation`。
推荐新代码直接使用：`from exporters.msp_automation import ...`
"""

from exporters.msp_automation import *
