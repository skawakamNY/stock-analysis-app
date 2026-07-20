import os
import sys
import yaml
from typing import Any
from pathlib import Path
from pydantic import BaseModel, Field

class ToolConfig(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    name: str
    model: str
    description: str
    instructions: str
    tools: list[ToolConfig] = Field(default_factory=list)


class AgentsConfig(BaseModel):
    agents: dict[str, AgentConfig]


def load_agents_config(
    yaml_file: str
) -> AgentsConfig:
    config_path = (
        Path(__file__).parent / yaml_file
    )
    with config_path.open(
        "r",
        encoding="utf-8"
    ) as file:
        data = yaml.safe_load(file)

    return AgentsConfig.model_validate(data)