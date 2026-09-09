"""可由未来应用入口转换为明确拒绝原因的领域错误。"""


class AccountingError(ValueError):
    """输入或状态不满足核算条件。"""
