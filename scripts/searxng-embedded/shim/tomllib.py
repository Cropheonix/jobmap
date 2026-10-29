"""Python 3.10 垫片：tomllib 于 3.11 入标准库，3.10 用 tomli 等价实现。"""
from tomli import *  # noqa: F401,F403
from tomli import TOMLDecodeError, loads, load  # 显式导出常用名
