"""执行引擎上下文：负责运行态数据存取与参数解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineContext:
    """运行期上下文容器，支持 `$path.to.value` 风格取值。"""

    data: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, path: str, default: Any = None) -> Any:
        current: Any = self.data
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                current = current[index] if 0 <= index < len(current) else None
            else:
                current = getattr(current, part, None)

            if current is None:
                return default
        return current

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        current: Any = self.data
        for part in parts[:-1]:
            if isinstance(current, dict):
                current = current.setdefault(part, {})
                continue

            next_value = getattr(current, part, None)
            if next_value is None:
                next_value = {}
                setattr(current, part, next_value)
            current = next_value

        final_key = parts[-1]
        if isinstance(current, dict):
            current[final_key] = value
        else:
            setattr(current, final_key, value)

    def setdefault(self, key: str, default: Any) -> Any:
        return self.data.setdefault(key, default)

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            return self.get(value[1:])
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value
