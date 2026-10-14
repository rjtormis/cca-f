import asyncio

from claude_agent_sdk import ClaudeAgentOptions, query

from lib.formatter import MessageFormatter


async def main():
    formatter = MessageFormatter()
    async for message in query(
        prompt="Review buggy.py for bugs that would cause crashes. Fix any issues you find.",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Glob"],  # Auto-approve these tools
            permission_mode="acceptEdits",  # Auto-approve file edits
            thinking={
                "type": "adaptive",
                "display": "summarized",
            },  # Return thinking text
        ),
    ):
        formatter.print(message)


if __name__ == "__main__":
    asyncio.run(main())
