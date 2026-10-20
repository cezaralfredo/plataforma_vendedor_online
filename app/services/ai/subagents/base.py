import abc
from typing import Any

from app.services.ai.providers.base import ToolDefinition


class BaseSubagent(abc.ABC):
    """Contrato base para subagentes especializados."""

    @abc.abstractmethod
    def get_tools(self) -> list[ToolDefinition]:
        """Retorna as definições de ferramentas expostas por este subagente."""
        pass

    @abc.abstractmethod
    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Executa a ferramenta solicitada e retorna o resultado em string/JSON."""
        pass
