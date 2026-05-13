"""Async-native composable shell piping in Python with method-chaining streams."""

from nemo_oo_agents.skill import Skill
from nemo_oo_agents_cli.tools.pyp.errors import PipeError as PipeError
from nemo_oo_agents_cli.tools.pyp.errors import Result as Result
from nemo_oo_agents_cli.tools.pyp.errors import make_pipe_error as make_pipe_error
from nemo_oo_agents_cli.tools.pyp.sources import arun as arun
from nemo_oo_agents_cli.tools.pyp.sources import cat as cat
from nemo_oo_agents_cli.tools.pyp.sources import empty as empty
from nemo_oo_agents_cli.tools.pyp.sources import find as find
from nemo_oo_agents_cli.tools.pyp.sources import glob as glob
from nemo_oo_agents_cli.tools.pyp.sources import items as items
from nemo_oo_agents_cli.tools.pyp.sources import lines as lines
from nemo_oo_agents_cli.tools.pyp.sources import rg as rg
from nemo_oo_agents_cli.tools.pyp.sources import run as run
from nemo_oo_agents_cli.tools.pyp.sources import seq as seq
from nemo_oo_agents_cli.tools.pyp.sources import stdin as stdin
from nemo_oo_agents_cli.tools.pyp.stream import Stream as Stream


class Pyp(Skill):
    """Async-native shell piping in Python — method-chaining streams.

    Build pipelines: source → transforms → sink. All async, non-blocking,
    streaming line-by-line via BashSession.

    ## Quick examples

        # Search and collect
        errors = await self.pyp.cat("app.log").grep("ERROR").head(10).collect()

        # Find Python files
        files = await self.pyp.find("src", name="*.py").sort().collect()

        # Ripgrep with post-processing
        todos = await self.pyp.rg("TODO", type_filter="py").wc().first()

        # Shell command → table
        table = await self.pyp.run("ps aux").head(5).table()

        # Apply a function to each line
        result = await self.pyp.run("ls").xargs(process_file).collect()

        # Pipeline to text
        text = await self.pyp.cat("f.txt").grep("key").sort().uniq().text()

        # Stream a long command via ShellTools (uses self.shell)
        out = await self.pyp.arun(self.shell, "make test").grep("FAIL").collect()

        # Write filtered output to file
        n = await self.pyp.cat("data.csv").grep("error").write("errors.csv")

    ## Sources (create a Stream)

        .cat(*paths)                  Read file(s) line-by-line
        .run(cmd, check=True)         Shell command via BashSession (streaming)
        .arun(shell, cmd)             Shell command via ShellTools.run_stream()
        .find(root, name=, type=)     Walk directory (rg --files, fast)
        .glob(pattern, root=".")      Python glob
        .rg(pattern, path=".")        Ripgrep search (streaming)
        .seq(start, end, step=1)      Numeric sequence
        .lines(text)                  From a multiline string
        .items(iterable)              From any iterable
        .empty()                      Empty stream

    ## Transforms (return a new Stream)

        .grep(pat, invert=, ignore_case=, fixed=)   Filter by pattern
        .head(n) / .tail(n)                          First/last N items
        .sort(key=, reverse=, numeric=)              Sort (buffering)
        .uniq(all_unique=, count=)                   Deduplicate
        .cut(fields=, sep=)                          Extract fields
        .sed(pattern, repl)                          Regex substitution
        .map(fn) / .filter(fn)                       Apply/filter function
        .xargs(fn)                                   Apply fn to each item (sync or async)
        .skip(n)                                     Skip first N
        .strip(chars=)                               Strip whitespace
        .flatten(sep=) / .wc(lines_only=)            Flatten / word count
        .tee(path)                                   Copy to file, pass through
        .take_while(fn) / .drop_while(fn)            Predicate-based slicing
        .pipe(*fns)                                  Chain custom transforms

    ## Sinks (consume the Stream, return a value)

        await stream.collect() -> list[str]     All items as list
        await stream.text() -> str              Joined with newlines
        await stream.first() -> str | None      First item
        await stream.last() -> str | None       Last item
        await stream.count() -> int             Number of items
        await stream.table() -> str             Formatted table
        await stream.json() -> list | dict      Parse as JSON
        await stream.result() -> Result         Result with .ok, .returncode, .stderr
        await stream.write(path) -> int         Write to file, return line count
        await stream.to_set() -> set[str]       Unique items
        await stream.to_dict(sep=) -> dict      Key-value pairs

    ## Error handling

        PipeError  — raised on command failure (check=True)
                     .cmd, .returncode, .stderr, .format_error()
        Result     — from .result() sink
                     .ok, .lines, .returncode, .stderr, .text
    """

    # Expose all source functions as instance methods
    cat = staticmethod(cat)
    run = staticmethod(run)
    arun = staticmethod(arun)
    find = staticmethod(find)
    glob = staticmethod(glob)
    rg = staticmethod(rg)
    seq = staticmethod(seq)
    lines = staticmethod(lines)
    items = staticmethod(items)
    empty = staticmethod(empty)
    stdin = staticmethod(stdin)
