"""Helpers for exposing the knowledge-base sources used by an AI output."""


def source_names(results: list[dict] | None) -> list[str]:
    """Return source names in retrieval order, removing duplicates and blanks."""
    names = []
    seen = set()
    for result in results or []:
        source = str(result.get("source") or "").strip()
        if source and source not in seen:
            seen.add(source)
            names.append(source)
    return names


def citation_text(sources: list[str] | None) -> str:
    """Build a compact, user-facing citation label for an AI output."""
    names = [str(source).strip() for source in (sources or []) if str(source).strip()]
    return f"知识库参考：{'；'.join(names)}" if names else ""
