#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""内置插件的可变状态按请求隔离；命令行可显式限定渲染上下文。"""

from contextlib import contextmanager
from contextvars import ContextVar

from flask import has_request_context, request

_state = ContextVar("otterwiki_render_state", default=None)
_REQUEST_KEY = "otterwiki.render_state"


def render_state():
    if has_request_context():
        return request.environ.setdefault(_REQUEST_KEY, {})
    state = _state.get()
    if state is None:
        state = {}
        _state.set(state)
    return state


def clear_render_state():
    if has_request_context():
        request.environ.pop(_REQUEST_KEY, None)
    else:
        _state.set(None)


@contextmanager
def render_context():
    """命令行或后台批处理每个文档时使用，退出（包括异常）后恢复上下文。"""
    token = _state.set({})
    try:
        yield
    finally:
        _state.reset(token)


class ContextAttribute:
    """保留插件的属性接口，同时避免插件单例持有页面或渲染结果。"""

    def __init__(self, factory=lambda: None):
        self.factory = factory

    def __set_name__(self, owner, name):
        self.key = (owner.__name__, name)

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        state = render_state()
        if self.key not in state:
            state[self.key] = self.factory()
        return state[self.key]

    def __set__(self, instance, value):
        render_state()[self.key] = value
