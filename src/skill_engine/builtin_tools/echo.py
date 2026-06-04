async def echo(message: str = "") -> dict:
    """Simple echo tool for testing the DAG executor."""
    return {"echoed": message}
