"""Small helpers for talking to Claude safely."""


def extract_text(msg) -> str:
    """Return the concatenated text from a Claude Message, skipping
    non-text content blocks (thinking, tool_use, etc).

    Claude Sonnet 5 sometimes emits a ThinkingBlock before text, so
    naive `msg.content[0].text` throws AttributeError. This helper is
    safe against any content-block ordering."""
    if not msg or not msg.content:
        return ""
    parts = []
    for block in msg.content:
        # Skip explicit non-text kinds
        btype = getattr(block, "type", None)
        if btype in ("thinking", "tool_use", "server_tool_use", "redacted_thinking"):
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip()
