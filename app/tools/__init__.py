from app.agent.registry import ToolRegistry
from app.data import StaticData

from .info_tools import register_info_tools
from .robot_tools import register_robot_tools


def build_registry(data: StaticData) -> ToolRegistry:
    reg = ToolRegistry()
    register_robot_tools(reg, data)
    register_info_tools(reg, data)
    # 2단계: get_events, draft_parent_message, draft_incident_report
    return reg


__all__ = ["build_registry"]
