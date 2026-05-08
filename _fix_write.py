
import pathlib
p = pathlib.Path("/Volumes/dev/dev/oo_agents_1/.worktrees/feat-shelltools-run-stream-returncode/src/nemo_oo_agents/tools/shell_tools.py")
content = p.read_text()

old = """    async def write(self, path: str, content: str) -> WriteResult:
        \"\"\"Create or overwrite a file.

        Creates parent directories if needed.

        Args:
            path: File path (relative to cwd).
            content: Full file content.

        Returns:
            WriteResult with path, created flag, and line count.

        Examples:
            r = await self.shell.write("src/new_module.py",
                                       "def hello():\\n    pass\\n")
        \"\"\"""""

new = """    async def write(
        self,
        path: Annotated[str, spec(description="File path (relative to cwd)")],
        content: Annotated[str, spec(description="Full file content")],
    ) -> WriteResult:
        \"\"\"Create or overwrite a file. Creates parent directories if needed.\"\"\"""""

if old in content:
    content = content.replace(old, new)
    p.write_text(content)
    print("write() updated successfully")
else:
    print("old text not found — dumping lines 276-291")
    lines = p.read_text().splitlines()
    for i, ln in enumerate(lines[275:291], 276):
        print(f"{i:3d}|{ln}")
